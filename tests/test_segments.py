import hashlib
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from indexweave import Delete, Document, LexicalIndex, Put, Writer
from indexweave.errors import (
    Busy,
    CommitUnknown,
    Conflict,
    InvalidInput,
    InvalidWorkspace,
    StaleIndex,
)
from indexweave.segment_codec import (
    MANIFEST_MAGIC,
    MAX_MANIFEST_BYTES,
    MAX_SEGMENT_BYTES,
    SEGMENT_MAGIC,
    InvalidIndex,
    decode,
    encode,
    parse_manifest,
    parse_segment,
)
from indexweave.segments import (
    compact_index,
    create_index,
    inspect_index,
    load_index,
    refresh_index,
    vacuum_index,
)


class SegmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.target = self.root / "index"
        with Writer.create(self.source) as writer:
            self.first = writer.write(
                [
                    Put(Document("a", "Cafe\u0301", "red fox recovery", ("notes",))),
                    Put(Document("b", "Beta", "blue fox", ("draft",))),
                ],
                expected_revision=0,
            )
        create_index(self.target, self.first)

    def update(self):
        with Writer(self.source) as writer:
            return writer.write(
                [
                    Put(Document("a", "Alpha", "green go go", ("notes",))),
                    Delete("b"),
                    Put(Document("c", "Gamma", "red fox", ("notes",))),
                ],
                expected_revision=1,
            )

    def assert_search_equal(self, actual, snapshot):
        reference = LexicalIndex.from_snapshot(snapshot)
        for query in [
            "fox",
            '"red fox"',
            '"go go"',
            "tag:notes",
            "tag:draft",
            "recovery",
        ]:
            self.assertEqual(actual.search(query), reference.search(query))

    def test_persisted_postings_load_without_retokenizing(self):
        with patch(
            "indexweave.lexical.analyze", side_effect=AssertionError("retokenized")
        ):
            loaded = load_index(self.target, expected_snapshot=self.first)
        self.assert_search_equal(loaded, self.first)
        self.assertEqual(inspect_index(self.target)["generation"], 1)
        self.assertEqual(
            set(os.listdir(self.source)),
            {"documents.iwl", "writer.lock", "access.lock"},
        )

    def test_delta_replacement_delete_and_incremental_analysis(self):
        old = load_index(self.target, expected_snapshot=self.first)
        second = self.update()
        with self.assertRaises(StaleIndex):
            load_index(self.target, expected_snapshot=second)
        calls = []
        from indexweave.analysis import analyze

        def spy(text):
            calls.append(text)
            return analyze(text)

        with patch("indexweave.lexical.analyze", side_effect=spy):
            manifest = refresh_index(self.target, second, expected_generation=1)
        self.assertEqual(len(calls), 4)  # Two changed documents, two fields each.
        self.assertEqual(manifest["generation"], 2)
        self.assertEqual(len(manifest["segments"]), 2)
        self.assert_search_equal(
            load_index(self.target, expected_snapshot=second), second
        )
        self.assert_search_equal(old, self.first)
        with self.assertRaises(StaleIndex):
            load_index(self.target, expected_snapshot=self.first)

    def test_compaction_vacuum_preserves_source_revision_and_old_view(self):
        second = self.update()
        refresh_index(self.target, second, expected_generation=1)
        old = load_index(self.target, expected_snapshot=second)
        source_bytes = (self.source / "documents.iwl").read_bytes()
        with patch(
            "indexweave.lexical.analyze", side_effect=AssertionError("retokenized")
        ):
            manifest = compact_index(self.target, expected_generation=2)
            loaded = load_index(self.target, expected_snapshot=second)
        self.assertEqual((manifest["generation"], manifest["source_revision"]), (3, 2))
        self.assertEqual(len(manifest["segments"]), 1)
        self.assertEqual(inspect_index(self.target)["unreferenced_segments"], 2)
        self.assertEqual(
            vacuum_index(self.target, expected_generation=3)["removed_files"], 2
        )
        self.assert_search_equal(loaded, second)
        self.assert_search_equal(old, second)
        self.assertEqual(source_bytes, (self.source / "documents.iwl").read_bytes())
        with Writer(self.source) as writer:
            self.assertEqual(
                writer.write(
                    [Put(Document("next", "Next", "next"))], expected_revision=2
                ).revision,
                3,
            )

    def test_cas_rollback_same_revision_mismatch_and_idempotence(self):
        before = (self.target / "CURRENT").read_bytes()
        for invalid in [True, 0, 1.5, 1 << 63]:
            with self.assertRaises(InvalidInput):
                refresh_index(self.target, self.first, expected_generation=invalid)
        with self.assertRaises(Conflict):
            refresh_index(self.target, self.first, expected_generation=2)
        self.assertEqual(
            refresh_index(self.target, self.first, expected_generation=1)["generation"],
            1,
        )
        forged = self.first.from_records(1, {}, 0)
        with self.assertRaises(StaleIndex):
            refresh_index(self.target, forged, expected_generation=1)
        second = self.update()
        refresh_index(self.target, second, expected_generation=1)
        with self.assertRaises(StaleIndex):
            refresh_index(self.target, self.first, expected_generation=2)
        self.assertNotEqual(before, (self.target / "CURRENT").read_bytes())
        self.assertEqual(
            compact_index(self.target, expected_generation=2)["generation"], 3
        )
        self.assertEqual(
            compact_index(self.target, expected_generation=3)["generation"], 3
        )

    def test_each_sync_boundary_and_replace_failure(self):
        second = self.update()
        real_sync = os.fsync
        for failure in [1, 2, 3, 4, "replace"]:
            target = self.root / ("failure-" + str(failure))
            create_index(target, self.first)
            counter = 0

            def fail(fd, fail_at=failure):
                nonlocal counter
                counter += 1
                if counter == fail_at:
                    raise OSError("injected sync failure")
                real_sync(fd)

            context = (
                patch(
                    "indexweave.index_files.os.replace", side_effect=OSError("replace")
                )
                if failure == "replace"
                else patch("indexweave.index_files.os.fsync", side_effect=fail)
            )
            with context, self.assertRaises(CommitUnknown):
                refresh_index(target, second, expected_generation=1)
            expected = second if failure == 4 else self.first
            self.assert_search_equal(
                load_index(target, expected_snapshot=expected), expected
            )
            generation = inspect_index(target)["generation"]
            vacuum_index(target, expected_generation=generation)
            self.assertFalse((target / "CURRENT.pending").exists())

    def test_partial_segment_write_old_manifest_remains_valid(self):
        second = self.update()

        def partial(fd, data):
            os.write(fd, data[:70])
            raise OSError("partial")

        with (
            patch("indexweave.index_files.write_all", side_effect=partial),
            self.assertRaises(CommitUnknown),
        ):
            refresh_index(self.target, second, expected_generation=1)
        self.assert_search_equal(
            load_index(self.target, expected_snapshot=self.first), self.first
        )
        self.assertEqual(
            vacuum_index(self.target, expected_generation=1)["removed_files"], 1
        )

    def test_corrupt_active_generation_never_falls_back_or_vacuums(self):
        second = self.update()
        refresh_index(self.target, second, expected_generation=1)
        manifest = inspect_index(self.target)
        path = self.target / ("seg-" + manifest["segments"][-1] + ".iws")
        data = bytearray(path.read_bytes())
        data[10] ^= 1
        path.write_bytes(data)
        before = set(os.listdir(self.target))
        for operation in [
            lambda: load_index(self.target, expected_snapshot=second),
            lambda: compact_index(self.target, expected_generation=2),
            lambda: vacuum_index(self.target, expected_generation=2),
        ]:
            with self.assertRaises(InvalidIndex):
                operation()
        self.assertEqual(before, set(os.listdir(self.target)))

    def test_header_truncation_and_checksum_valid_semantic_mutation(self):
        manifest_bytes = (self.target / "CURRENT").read_bytes()
        for end in range(len(manifest_bytes)):
            with self.assertRaises(InvalidIndex):
                parse_manifest(manifest_bytes[:end])
        manifest = parse_manifest(manifest_bytes)
        path = self.target / ("seg-" + manifest["segments"][0] + ".iws")
        value = decode(path.read_bytes(), SEGMENT_MAGIC, MAX_SEGMENT_BYTES)
        value["postings"][0][3] = [999]
        bad = encode(value, SEGMENT_MAGIC, MAX_SEGMENT_BYTES)
        with self.assertRaises(InvalidIndex):
            parse_segment(bad, hashlib.sha256(bad).hexdigest(), 0, base=True)
        value = decode(path.read_bytes(), SEGMENT_MAGIC, MAX_SEGMENT_BYTES)
        value["puts"][0]["tokens"]["title"][0][1] = 1
        bad = encode(value, SEGMENT_MAGIC, MAX_SEGMENT_BYTES)
        with self.assertRaises(InvalidIndex):
            parse_segment(bad, hashlib.sha256(bad).hexdigest(), 0, base=True)
        manifest["segments"] = ["../escape"]
        with self.assertRaises(InvalidIndex):
            parse_manifest(encode(manifest, MANIFEST_MAGIC, MAX_MANIFEST_BYTES))

    def test_duplicate_noncanonical_deep_and_nonfinite_json(self):
        for raw in [
            b'{"version":1,"version":1}',
            b'{ "x":1}',
            b'{"x":NaN}',
            b"[" * 40 + b"0" + b"]" * 40,
        ]:
            body = MANIFEST_MAGIC + raw
            data = body + hashlib.sha256(body).digest()
            with self.assertRaises(InvalidIndex):
                decode(data, MANIFEST_MAGIC, MAX_MANIFEST_BYTES)

    def test_provenance_mismatch_refuses_without_rebuild(self):
        raw = (self.target / "CURRENT").read_bytes()
        manifest = parse_manifest(raw)
        manifest["provenance"]["normalization_unicode_version"] = "different"
        (self.target / "CURRENT").write_bytes(
            encode(manifest, MANIFEST_MAGIC, MAX_MANIFEST_BYTES)
        )
        with self.assertRaises(InvalidIndex):
            load_index(self.target, expected_snapshot=self.first)
        self.assertNotEqual((self.target / "CURRENT").read_bytes(), raw)

    def test_directory_guards_and_existing_path_preservation(self):
        before = (self.source / "documents.iwl").read_bytes()
        with self.assertRaises(FileExistsError):
            create_index(self.source, self.first)
        self.assertEqual(before, (self.source / "documents.iwl").read_bytes())
        unexpected = self.target / "unexpected"
        unexpected.write_text("not allowed")
        with self.assertRaises(InvalidIndex):
            inspect_index(self.target)
        unexpected.unlink()
        os.chmod(self.target / "writer.lock", 0o644)
        with self.assertRaises(InvalidWorkspace):
            inspect_index(self.target)
        os.chmod(self.target / "writer.lock", 0o600)
        link = self.root / "linked-index"
        link.symlink_to(self.target)
        with self.assertRaises(OSError):
            inspect_index(link)

    def test_segment_and_directory_budgets_before_write(self):
        second = self.update()
        before = set(os.listdir(self.target))
        with (
            patch("indexweave.segments.MAX_SEGMENTS", 1),
            self.assertRaises(InvalidInput),
        ):
            refresh_index(self.target, second, expected_generation=1)
        with (
            patch("indexweave.index_files.MAX_DIRECTORY_BYTES", 1),
            self.assertRaises(InvalidIndex),
        ):
            refresh_index(self.target, second, expected_generation=1)
        with (
            patch("indexweave.segments.MAX_SEGMENT_BYTES", 20),
            self.assertRaises(InvalidInput),
        ):
            create_index(self.root / "too-small", self.first)
        self.assertFalse((self.root / "too-small").exists())
        self.assertEqual(before, set(os.listdir(self.target)))

    def test_pending_is_explicitly_cleaned_and_not_auto_repaired(self):
        second = self.update()
        pending = self.target / "CURRENT.pending"
        pending.write_bytes(b"incomplete")
        os.chmod(pending, 0o600)
        with self.assertRaises(InvalidIndex):
            refresh_index(self.target, second, expected_generation=1)
        self.assertTrue(pending.exists())
        with self.assertRaises(Conflict):
            vacuum_index(self.target, expected_generation=2)
        self.assertTrue(pending.exists())
        self.assertEqual(
            vacuum_index(self.target, expected_generation=1)["removed_files"], 1
        )
        refresh_index(self.target, second, expected_generation=1)

    def worker(self, mode):
        return subprocess.Popen(
            [
                "python",
                "tests/segment_worker.py",
                mode,
                str(self.target),
                str(self.source),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_real_process_crashes_before_and_after_manifest_replace(self):
        second = self.update()
        for mode in ["before-replace", "after-replace"]:
            child = self.worker(mode)
            out, err = child.communicate(timeout=15)
            self.assertEqual(
                child.returncode,
                -signal.SIGKILL if mode == "after-replace" else 31,
                (out, err),
            )
            expected = second if mode == "after-replace" else self.first
            self.assert_search_equal(
                load_index(self.target, expected_snapshot=expected), expected
            )
            if mode == "before-replace":
                self.assertTrue(inspect_index(self.target)["pending_manifest"])
                vacuum_index(self.target, expected_generation=1)

    def test_shared_reader_gate_blocks_compaction_and_gc(self):
        second = self.update()
        refresh_index(self.target, second, expected_generation=1)
        child = self.worker("hold-reader")
        try:
            self.assertEqual(child.stdout.readline().strip(), "READY")
            with self.assertRaises(Busy):
                compact_index(self.target, expected_generation=2)
            with self.assertRaises(Busy):
                vacuum_index(self.target, expected_generation=2)
        finally:
            child.communicate("\n", timeout=15)
        self.assertEqual(
            compact_index(self.target, expected_generation=2)["generation"], 3
        )

    @settings(max_examples=20, deadline=None, derandomize=True, database=None)
    @given(
        st.lists(
            st.tuples(
                st.sampled_from(["a", "b", "c"]),
                st.sampled_from(["red fox", "blue go go", "recovery", "green"]),
            ),
            min_size=1,
            max_size=12,
        )
    )
    def test_generations_against_rebuilt_index_oracle(self, changes):
        with tempfile.TemporaryDirectory() as root:
            source, target = Path(root) / "source", Path(root) / "index"
            with Writer.create(source) as writer:
                snapshot = writer.snapshot()
                create_index(target, snapshot)
                generation = 1
                for number, (key, words) in enumerate(changes):
                    operation = (
                        Delete(key)
                        if number % 3 == 2 and snapshot.get(key)
                        else Put(Document(key, "Title", words, ("notes",)))
                    )
                    snapshot = writer.write(
                        [operation], expected_revision=snapshot.revision
                    )
                    generation = refresh_index(
                        target, snapshot, expected_generation=generation
                    )["generation"]
                    self.assert_search_equal(
                        load_index(target, expected_snapshot=snapshot), snapshot
                    )
                    if number % 3 == 2:
                        generation = compact_index(
                            target, expected_generation=generation
                        )["generation"]
                        vacuum_index(target, expected_generation=generation)
                        self.assert_search_equal(
                            load_index(target, expected_snapshot=snapshot), snapshot
                        )

    def test_separate_process_writer_lease_and_exclusive_gate(self):
        second = self.update()
        child = self.worker("hold-writer")
        try:
            self.assertEqual(child.stdout.readline().strip(), "READY")
            with self.assertRaises(Busy):
                refresh_index(self.target, second, expected_generation=1)
            with self.assertRaises(Busy):
                load_index(self.target, expected_snapshot=self.first)
        finally:
            child.communicate("\n", timeout=15)
        self.assertEqual(
            refresh_index(self.target, second, expected_generation=1)["generation"], 2
        )

    def test_installed_cli_generation_lifecycle(self):
        import json

        target = self.root / "cli-index"

        def call(*args, success=True):
            result = subprocess.run(
                ["indexweave", *map(str, args)], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 0 if success else 2, result.stderr)
            return json.loads(result.stdout) if success else None

        self.assertEqual(
            call("index-create", self.source, "--index", target)["generation"], 1
        )
        call("search", self.source, "--index", target, "--query", '"red fox"')
        second = self.update()
        call("search", self.source, "--index", target, "--query", "fox", success=False)
        self.assertEqual(
            call(
                "index-refresh",
                self.source,
                "--index",
                target,
                "--expected-generation",
                1,
            )["generation"],
            2,
        )
        self.assertEqual(
            call("index-compact", target, "--expected-generation", 2)["generation"], 3
        )
        self.assertEqual(
            call("index-vacuum", target, "--expected-generation", 3)["removed_files"], 2
        )
        self.assertEqual(
            call("index-inspect", target)["source_revision"], second.revision
        )

    def test_symlink_hardlink_and_fifo_files_refused(self):
        current = self.target / "CURRENT"
        saved = self.root / "saved"
        current.rename(saved)
        try:
            current.symlink_to(saved)
            with self.assertRaises(InvalidWorkspace):
                inspect_index(self.target)
            current.unlink()
        finally:
            saved.rename(current)
        hardlink = self.root / "outside-manifest"
        os.link(current, hardlink)
        try:
            with self.assertRaises(InvalidWorkspace):
                inspect_index(self.target)
        finally:
            hardlink.unlink()
        fifo = self.target / ("seg-" + "0" * 64 + ".iws")
        os.mkfifo(fifo, 0o600)
        try:
            with self.assertRaises(InvalidWorkspace):
                inspect_index(self.target)
        finally:
            fifo.unlink()

    def test_two_process_refresh_race_has_one_published_generation(self):
        second = self.update()
        children = [self.worker("race-refresh") for _ in range(2)]
        try:
            for child in children:
                self.assertEqual(child.stdout.readline().strip(), "READY")
            for child in children:
                child.stdin.write("\n")
                child.stdin.flush()
            outcomes = []
            for child in children:
                out, err = child.communicate(timeout=15)
                self.assertEqual(child.returncode, 0, err)
                outcomes.append(out.strip())
            self.assertEqual(sorted(outcomes), ["PUBLISHED", "REFUSED"])
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=15)
        self.assertEqual(inspect_index(self.target)["generation"], 2)
        self.assert_search_equal(
            load_index(self.target, expected_snapshot=second), second
        )
