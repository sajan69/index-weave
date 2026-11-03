import hashlib
import json
import os
import signal
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from indexweave import (
    Delete,
    Document,
    Put,
    Record,
    Snapshot,
    Writer,
    compact_store,
    create_index,
    inspect,
    load_index,
    read_snapshot,
    repair_tail,
)
from indexweave.checkpoint import (
    BASE_COMMIT,
    CHUNK_MAGIC,
    MAGIC_V2,
    META_MAGIC,
    encode_checkpoint,
)
from indexweave.codec import (
    HEADER_BYTES,
    PREFIX,
    TRAILER_BYTES,
    encode_frame,
    operations_payload,
    scan,
)
from indexweave.errors import (
    Busy,
    CommitUnknown,
    Conflict,
    Corruption,
    InvalidInput,
    StoreError,
)
from indexweave.model import MAX_REVISION, canonical, snapshot_digest

DOC = b'{"id":"a","tags":[],"text":"old","title":"A"}'
ROW = b'{"document":' + DOC + b',"revision":2}'


def golden_checkpoint():
    def frame(magic, payload, number):
        prefix = struct.pack(">4sIQ", magic, len(payload), number)
        header = prefix + hashlib.sha256(prefix).digest()
        return (
            header + payload + hashlib.sha256(header + payload).digest() + b"IWCOMMIT"
        )

    records_hash = hashlib.sha256(ROW + b"\n").hexdigest().encode()
    metadata = (
        b'{"chunk_count":1,"document_count":1,"live_bytes":'
        + str(len(DOC)).encode()
        + b',"records_sha256":"'
        + records_hash
        + b'","revision":7,"version":2}'
    )
    chunk = b'{"records":[' + ROW + b'],"version":2}'
    prefix = MAGIC_V2 + frame(b"IWCP", metadata, 7) + frame(b"IWCR", chunk, 1)
    return prefix + b"IWBASEOK" + hashlib.sha256(prefix).digest()


def sample_snapshot():
    doc = Document("a", "A", "old")
    return Snapshot.from_records(7, {"a": Record(doc, 2)}, len(DOC))


def mutate_checkpoint(data, change):
    offset, frames = len(MAGIC_V2), []
    while data[offset : offset + 8] != BASE_COMMIT:
        marker, length, revision = PREFIX.unpack(data[offset : offset + PREFIX.size])
        payload = json.loads(
            data[offset + HEADER_BYTES : offset + HEADER_BYTES + length]
        )
        frames.append([marker, payload, revision])
        offset += HEADER_BYTES + length + TRAILER_BYTES
    change(frames)
    prefix = MAGIC_V2 + b"".join(
        encode_frame(canonical(value), number, magic=marker)
        for marker, value, number in frames
    )
    return prefix + BASE_COMMIT + hashlib.sha256(prefix).digest()


class CheckpointCodecTests(unittest.TestCase):
    def test_hand_built_golden_checkpoint_and_record_revisions(self):
        expected = golden_checkpoint()
        self.assertEqual(encode_checkpoint(sample_snapshot()), expected)
        state, report = scan(expected)
        self.assertEqual(state, sample_snapshot())
        self.assertEqual(
            (
                report.format_version,
                report.checkpoint_revision,
                report.checkpoint_bytes,
            ),
            (2, 7, len(expected)),
        )
        self.assertEqual(report.valid_bytes, len(expected))
        self.assertEqual(state.get("a").revision, 2)

    def test_every_incomplete_checkpoint_prefix_is_corruption(self):
        data = golden_checkpoint()
        for cut in range(len(data)):
            with self.subTest(cut=cut), self.assertRaises(Corruption):
                scan(data[:cut])

    def test_complete_bit_flips_and_footer_loss_fail_closed(self):
        data = golden_checkpoint()
        for offset in [0, 10, 30, 80, len(data) - 1, len(data) - 35]:
            bad = bytearray(data)
            bad[offset] ^= 1
            with self.assertRaises(Corruption):
                scan(bytes(bad))
        with self.assertRaises(Corruption):
            scan(data[:-40] + b"X" * 40)

    def test_checksum_valid_semantic_errors(self):
        mutations = [
            lambda f: f[0][1].update(document_count=2),
            lambda f: f[0][1].update(live_bytes=999),
            lambda f: f[0][1].update(revision=True),
            lambda f: f[0][1].update(records_sha256="0" * 64),
            lambda f: f[0][1].update(chunk_count=257),
            lambda f: f[1][1]["records"][0].update(revision=8),
            lambda f: f[1][1]["records"][0].update(revision=0),
            lambda f: f[1][1]["records"].append(f[1][1]["records"][0]),
            lambda f: f[1][1].update(unknown=1),
            lambda f: f[1].__setitem__(2, 2),
        ]
        for change in mutations:
            with self.assertRaises(Corruption):
                scan(mutate_checkpoint(golden_checkpoint(), change))

    def test_empty_zero_and_maximum_revision(self):
        for revision in [0, 13, MAX_REVISION]:
            snapshot = Snapshot.from_records(revision, {}, 0)
            state, report = scan(encode_checkpoint(snapshot))
            self.assertEqual(state, snapshot)
            self.assertEqual(report.checkpoint_revision, revision)
        data = encode_checkpoint(Snapshot.from_records(MAX_REVISION, {}, 0))
        with self.assertRaises(Corruption):
            scan(data + b"I")

    def test_actual_chunk_count_and_payload_boundaries(self):
        records = {
            f"d{i:03}": Record(Document(f"d{i:03}", "Title", "text"), i + 1)
            for i in range(201)
        }
        snapshot = Snapshot.from_records(
            300,
            records,
            sum(len(canonical(r.document.to_dict())) for r in records.values()),
        )
        data = encode_checkpoint(snapshot)
        length = PREFIX.unpack(data[8 : 8 + PREFIX.size])[1]
        meta = json.loads(data[8 + HEADER_BYTES : 8 + HEADER_BYTES + length])
        self.assertEqual(meta["chunk_count"], 3)
        self.assertEqual(scan(data)[0], snapshot)
        records = {
            f"d{i:03}": Record(Document(f"d{i:03}", "Title", "x" * 65536), 1)
            for i in range(21)
        }
        snapshot = Snapshot.from_records(
            1,
            records,
            sum(len(canonical(r.document.to_dict())) for r in records.values()),
        )
        data = encode_checkpoint(snapshot)
        length = PREFIX.unpack(data[8 : 8 + PREFIX.size])[1]
        meta = json.loads(data[8 + HEADER_BYTES : 8 + HEADER_BYTES + length])
        self.assertEqual(meta["chunk_count"], 2)
        self.assertEqual(scan(data)[0], snapshot)

    def test_following_transactions_and_partial_tail_boundary(self):
        data = golden_checkpoint()
        payload = operations_payload([Put(Document("b", "B", "next"))])
        frame = encode_frame(payload, 8)
        for cut in range(len(frame)):
            state, report = scan(data + frame[:cut])
            self.assertEqual(state, sample_snapshot())
            self.assertEqual(report.valid_bytes, len(data))
            self.assertEqual(report.tail_bytes, cut)
        state, report = scan(data + frame)
        self.assertEqual(state.revision, 8)
        self.assertEqual(state.get("a").revision, 2)
        self.assertEqual(state.get("b").revision, 8)
        with self.assertRaises(Corruption):
            scan(data + encode_frame(payload, 1))

    def test_encoder_bounds_and_accounting(self):
        for value in [
            None,
            Snapshot.from_records(True, {}, 0),
            Snapshot.from_records(0, {}, 10),
        ]:
            with self.assertRaises(InvalidInput):
                encode_checkpoint(value)
        with (
            patch("indexweave.checkpoint.MAX_CHUNKS", 0),
            self.assertRaises(InvalidInput),
        ):
            encode_checkpoint(sample_snapshot())
        with (
            patch("indexweave.checkpoint.MAX_LOG_BYTES", 20),
            self.assertRaises(InvalidInput),
        ):
            encode_checkpoint(sample_snapshot())
        with (
            patch("indexweave.checkpoint.MAX_PAYLOAD_BYTES", 20),
            self.assertRaises(InvalidInput),
        ):
            encode_checkpoint(sample_snapshot())

    def test_strict_json_depth_value_and_encoding_guards(self):
        bad_payloads = [
            b'{"version":2,"version":2}',
            b'{ "x":1}',
            b'{"x":NaN}',
            b'{"x":1.0}',
            b'{"x":"\xff"}',
            b"[" * 33 + b"0" + b"]" * 33,
        ]
        for payload in bad_payloads:
            prefix = MAGIC_V2 + encode_frame(payload, 7, magic=META_MAGIC)
            with self.assertRaises(Corruption):
                scan(prefix + BASE_COMMIT + hashlib.sha256(prefix).digest())
        data = golden_checkpoint()
        metadata_length = PREFIX.unpack(data[8 : 8 + PREFIX.size])[1]
        end = 8 + HEADER_BYTES + metadata_length + TRAILER_BYTES
        huge_chunk = b'{"records":[' + b",".join([b"0"] * 100001) + b'],"version":2}'
        prefix = data[:end] + encode_frame(huge_chunk, 1, magic=CHUNK_MAGIC)
        with self.assertRaises(Corruption):
            scan(prefix + BASE_COMMIT + hashlib.sha256(prefix).digest())


class CompactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.target = self.root / "compacted"
        with Writer.create(self.source) as writer:
            self.old = writer.write(
                [Put(Document("a", "A", "old")), Put(Document("b", "B", "remove"))],
                expected_revision=0,
            )
            self.current = writer.write(
                [Put(Document("a", "A", "current")), Delete("b")], expected_revision=1
            )

    def test_source_unchanged_cas_and_old_snapshots(self):
        before = (self.source / "documents.iwl").read_bytes()
        result = compact_store(self.source, self.target, expected_revision=2)
        self.assertEqual(result["revision"], 2)
        self.assertEqual((self.target / "documents.iwl").read_bytes()[:8], MAGIC_V2)
        self.assertEqual(read_snapshot(self.target), self.current)
        with Writer(self.target) as writer:
            with self.assertRaises(Conflict):
                writer.write([Put(Document("c", "C", "wrong"))], expected_revision=0)
            next_state = writer.write(
                [Put(Document("c", "C", "next"))], expected_revision=2
            )
        self.assertEqual(next_state.revision, 3)
        self.assertEqual(next_state.get("a").revision, 2)
        self.assertEqual(next_state.get("c").revision, 3)
        self.assertEqual(self.old.get("a").document.text, "old")
        self.assertIsNotNone(self.old.get("b"))
        self.assertEqual(before, (self.source / "documents.iwl").read_bytes())
        self.assertEqual(read_snapshot(self.source), self.current)

    def test_existing_derived_index_matches_compacted_source(self):
        target = self.root / "index"
        create_index(target, self.current)
        before = load_index(target, expected_snapshot=self.current).search("current")
        compact_store(self.source, self.target, expected_revision=2)
        after = load_index(target, expected_snapshot=read_snapshot(self.target)).search(
            "current"
        )
        self.assertEqual(before, after)

    def test_expected_revision_existing_and_nested_destination_guards(self):
        for bad in [True, -1, MAX_REVISION + 1]:
            with self.assertRaises(InvalidInput):
                compact_store(self.source, self.target, expected_revision=bad)
        with self.assertRaises(Conflict):
            compact_store(self.source, self.target, expected_revision=1)
        self.assertFalse(self.target.exists())
        for target in [
            self.source,
            self.source / "nested",
            self.source / ".." / "source" / "alias",
        ]:
            with self.assertRaises(InvalidInput):
                compact_store(self.source, target, expected_revision=2)
        self.target.mkdir()
        (self.target / "sentinel").write_text("keep")
        with self.assertRaises(FileExistsError):
            compact_store(self.source, self.target, expected_revision=2)
        self.assertEqual((self.target / "sentinel").read_text(), "keep")
        self.assertEqual(
            set(os.listdir(self.source)),
            {"documents.iwl", "writer.lock", "access.lock"},
        )

    def test_source_writer_lease_blocks_copy_before_destination_creation(self):
        with Writer(self.source):
            with self.assertRaises(Busy):
                compact_store(self.source, self.target, expected_revision=2)
        self.assertFalse(self.target.exists())

    def test_partial_base_never_repaired_or_reused(self):
        compact_store(self.source, self.target, expected_revision=2)
        log = self.target / "documents.iwl"
        log.write_bytes(log.read_bytes()[:70])
        before = log.read_bytes()
        with self.assertRaises(Corruption):
            inspect(self.target)
        with self.assertRaises(Corruption):
            repair_tail(
                self.target,
                expected_log_sha256=hashlib.sha256(before).hexdigest(),
                expected_valid_bytes=8,
            )
        with self.assertRaises(FileExistsError):
            compact_store(self.source, self.target, expected_revision=2)
        self.assertEqual(log.read_bytes(), before)

    def test_post_checkpoint_tail_repair_and_subsequent_write(self):
        compact_store(self.source, self.target, expected_revision=2)
        log = self.target / "documents.iwl"
        base_bytes = log.stat().st_size
        frame = encode_frame(operations_payload([Put(Document("c", "C", "next"))]), 3)
        with log.open("ab") as stream:
            stream.write(frame[:70])
        report = inspect(self.target)
        self.assertEqual(
            (report.valid_bytes, report.checkpoint_bytes, report.revision),
            (base_bytes, base_bytes, 2),
        )
        repair_tail(
            self.target,
            expected_log_sha256=report.log_sha256,
            expected_valid_bytes=report.valid_bytes,
        )
        with Writer(self.target) as writer:
            self.assertEqual(
                writer.write(
                    [Put(Document("c", "C", "next"))], expected_revision=2
                ).revision,
                3,
            )

    def test_each_destination_sync_failure_keeps_source_intact(self):
        before = (self.source / "documents.iwl").read_bytes()
        real_sync = os.fsync
        for failure in range(1, 6):
            target = self.root / f"failure-{failure}"
            count = 0

            def sync(fd, fail_at=failure):
                nonlocal count
                count += 1
                if count == fail_at:
                    raise OSError("injected sync failure")
                real_sync(fd)

            with (
                patch("indexweave.storage.sync_file", side_effect=sync),
                self.assertRaises(CommitUnknown),
            ):
                compact_store(self.source, target, expected_revision=2)
            self.assertEqual(before, (self.source / "documents.iwl").read_bytes())
            if failure >= 3:
                self.assertEqual(read_snapshot(target), self.current)
            else:
                with self.assertRaises(StoreError):
                    inspect(target)
            with self.assertRaises(FileExistsError):
                compact_store(self.source, target, expected_revision=2)

    def test_source_corruption_refuses_without_creating_destination(self):
        log = self.source / "documents.iwl"
        data = bytearray(log.read_bytes())
        data[30] ^= 1
        log.write_bytes(data)
        with self.assertRaises(Corruption):
            compact_store(self.source, self.target, expected_revision=2)
        self.assertFalse(self.target.exists())

    def test_child_process_partial_and_synced_unacknowledged_copy(self):
        before = (self.source / "documents.iwl").read_bytes()
        for mode, expected in [
            ("before", 31),
            ("partial", 32),
            ("synced", -signal.SIGKILL),
        ]:
            target = self.root / mode
            child = subprocess.run(
                [
                    "python",
                    "tests/checkpoint_worker.py",
                    mode,
                    str(self.source),
                    str(target),
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(child.returncode, expected, child.stderr)
            self.assertEqual(before, (self.source / "documents.iwl").read_bytes())
            if mode == "synced":
                self.assertEqual(read_snapshot(target), self.current)
            else:
                with self.assertRaises(StoreError):
                    read_snapshot(target)
            with self.assertRaises(FileExistsError):
                compact_store(self.source, target, expected_revision=2)
        with Writer(self.source) as writer:
            self.assertEqual(writer.snapshot(), self.current)

    def test_installed_cli_copy_and_append(self):
        result = subprocess.run(
            [
                "indexweave",
                "compact-store",
                str(self.source),
                "--out",
                str(self.target),
                "--expected-revision",
                "2",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(result.stdout)["format_version"], 2)
        self.assertEqual(read_snapshot(self.target), self.current)
        self.assertEqual(
            scan((self.source / "documents.iwl").read_bytes())[1].to_dict()[
                "format_version"
            ],
            1,
        )

    @settings(max_examples=20, deadline=None, derandomize=True, database=None)
    @given(
        st.lists(
            st.tuples(
                st.sampled_from(["a", "b", "c"]),
                st.sampled_from(["red fox", "blue go", "notes"]),
            ),
            min_size=1,
            max_size=10,
        )
    )
    def test_repeated_compaction_against_dictionary_oracle(self, changes):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "start"
            with Writer.create(path):
                pass
            expected, revision = {}, 0
            for number, (key, text) in enumerate(changes):
                with Writer(path) as writer:
                    operation = (
                        Delete(key)
                        if number % 3 == 2 and key in expected
                        else Put(Document(key, "Title", text))
                    )
                    revision += 1
                    if isinstance(operation, Delete):
                        del expected[key]
                    else:
                        expected[key] = (text, revision)
                    state = writer.write([operation], expected_revision=revision - 1)
                if number % 2 == 1:
                    destination = Path(root) / f"copy-{number}"
                    compact_store(path, destination, expected_revision=revision)
                    path = destination
                actual = read_snapshot(path)
                self.assertEqual(actual.revision, revision)
                self.assertEqual(
                    {
                        key: (r.document.text, r.revision)
                        for key, r in actual.documents.items()
                    },
                    expected,
                )
                self.assertEqual(snapshot_digest(actual), snapshot_digest(state))
