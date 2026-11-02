import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from indexweave import (
    Delete,
    Document,
    Put,
    Writer,
    inspect,
    read_snapshot,
    repair_tail,
)
from indexweave.errors import (
    CommitUnknown,
    Conflict,
    Corruption,
    IncompleteTail,
    InvalidInput,
    MissingDocument,
    UnusableWriter,
)
from indexweave.files import LOG


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "workspace"
        with Writer.create(self.path):
            pass

    def raw(self):
        return (self.path / LOG).read_bytes()

    def test_batch_update_delete_and_record_revisions_survive_reopen(self):
        with Writer(self.path) as writer:
            first = writer.write(
                [
                    Put(Document("a", "Title", "Original", ("notes",))),
                    Put(Document("b", "二", "नमस्ते")),
                ],
                expected_revision=0,
            )
            second = writer.write(
                [Put(Document("a", "Changed", "Updated")), Delete("b")],
                expected_revision=1,
            )
            self.assertEqual(first.revision, 1)
            self.assertEqual(second.revision, 2)
            self.assertEqual(second.get("a").revision, 2)
            self.assertIsNone(second.get("b"))
        reopened = read_snapshot(self.path)
        self.assertEqual(dict(reopened.documents), dict(second.documents))
        self.assertEqual(first.get("a").document.text, "Original")
        self.assertEqual(first.get("b").document.text, "नमस्ते")

    def test_snapshot_values_and_map_are_immutable(self):
        with Writer(self.path) as writer:
            state = writer.write(
                [Put(Document("a", "Title", "Body"))], expected_revision=0
            )
            with self.assertRaises(TypeError):
                state.documents["x"] = state.get("a")
            with self.assertRaises(FrozenInstanceError):
                state.get("a").document.text = "changed"
            writer.write([Delete("a")], expected_revision=1)
            self.assertIsNotNone(state.get("a"))

    def test_conflict_invalid_revision_and_missing_delete_do_not_append(self):
        with Writer(self.path) as writer:
            original = self.raw()
            for revision in [True, -1, 1 << 63]:
                with self.assertRaises(InvalidInput):
                    writer.write([Delete("missing")], expected_revision=revision)
            with self.assertRaises(Conflict):
                writer.write([Delete("missing")], expected_revision=1)
            with self.assertRaises(MissingDocument):
                writer.write(
                    [Put(Document("a", "Title", "Body")), Delete("missing")],
                    expected_revision=0,
                )
            self.assertEqual(self.raw(), original)
            self.assertEqual(writer.snapshot().revision, 0)

    def test_invalid_batches_and_payload_cap_leave_writer_usable(self):
        with Writer(self.path) as writer:
            for operations in [
                [],
                [object()],
                [Delete("a")] * 2,
                [Delete(str(i)) for i in range(101)],
                [Put(Document(str(i), "Title", "x" * 65536)) for i in range(20)],
            ]:
                with self.assertRaises(InvalidInput):
                    writer.write(operations, expected_revision=0)
            self.assertEqual(inspect(self.path).revision, 0)
            writer.write([Put(Document("ok", "Title", "Body"))], expected_revision=0)

    def test_short_writes_and_interrupted_syscall_complete_one_frame(self):
        original_write = os.write
        interrupted = False

        def partial(fd, data):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise InterruptedError
            return original_write(fd, data[:7])

        with (
            Writer(self.path) as writer,
            patch("indexweave.files.os.write", side_effect=partial),
        ):
            writer.write([Put(Document("a", "Title", "Body"))], expected_revision=0)
        self.assertEqual(read_snapshot(self.path).get("a").document.text, "Body")

    def test_fsync_failure_has_unknown_outcome_and_poisoned_handle(self):
        with Writer(self.path) as writer:
            with patch(
                "indexweave.storage.sync_file",
                side_effect=OSError("Injected sync failure"),
            ):
                with self.assertRaises(CommitUnknown):
                    writer.write(
                        [Put(Document("a", "Title", "Body"))], expected_revision=0
                    )
            for call in [
                writer.snapshot,
                lambda: writer.write([Delete("a")], expected_revision=0),
            ]:
                with self.assertRaises(UnusableWriter):
                    call()
        # A complete frame may be visible despite the unsuccessful acknowledgement.
        self.assertEqual(read_snapshot(self.path).revision, 1)

    def test_partial_append_requires_explicit_matching_repair_tokens(self):
        def partial(fd, data):
            os.write(fd, data[:70])
            raise OSError("Injected partial append")

        with Writer(self.path) as writer:
            with patch("indexweave.storage.write_all", side_effect=partial):
                with self.assertRaises(CommitUnknown):
                    writer.write(
                        [Put(Document("a", "Title", "Body"))], expected_revision=0
                    )
        before = self.raw()
        report = inspect(self.path)
        self.assertEqual(report.revision, 0)
        self.assertEqual(report.tail_bytes, 70)
        with self.assertRaises(IncompleteTail):
            read_snapshot(self.path)
        with self.assertRaises(IncompleteTail):
            Writer(self.path)
        for digest, size in [
            ("0" * 64, report.valid_bytes),
            (report.log_sha256, report.valid_bytes + 1),
        ]:
            with self.assertRaises(Conflict):
                repair_tail(
                    self.path, expected_log_sha256=digest, expected_valid_bytes=size
                )
            self.assertEqual(self.raw(), before)
        repaired = repair_tail(
            self.path,
            expected_log_sha256=report.log_sha256,
            expected_valid_bytes=report.valid_bytes,
        )
        self.assertEqual(repaired.tail_bytes, 0)
        with Writer(self.path) as writer:
            writer.write([Put(Document("b", "Title", "New"))], expected_revision=0)
        self.assertEqual(read_snapshot(self.path).revision, 1)

    def test_complete_corruption_is_never_truncated_by_repair(self):
        with Writer(self.path) as writer:
            writer.write([Put(Document("a", "Title", "Body"))], expected_revision=0)
        raw = bytearray(self.raw())
        raw[-1] ^= 1
        (self.path / LOG).write_bytes(raw)
        for call in [
            lambda: inspect(self.path),
            lambda: read_snapshot(self.path),
            lambda: repair_tail(
                self.path, expected_log_sha256="0" * 64, expected_valid_bytes=8
            ),
        ]:
            with self.assertRaises(Corruption):
                call()
        self.assertEqual(self.raw(), raw)

    def test_closed_writer_and_clean_repair_are_refused(self):
        writer = Writer(self.path)
        writer.close()
        writer.close()
        with self.assertRaises(UnusableWriter):
            writer.snapshot()
        report = inspect(self.path)
        with self.assertRaises(InvalidInput):
            repair_tail(
                self.path,
                expected_log_sha256=report.log_sha256,
                expected_valid_bytes=report.valid_bytes,
            )

    def test_outside_log_growth_poison_writer_without_appending(self):
        with Writer(self.path) as writer:
            with (self.path / LOG).open("ab") as stream:
                stream.write(b"I")
            before = self.raw()
            with self.assertRaises(Corruption):
                writer.write([Put(Document("a", "Title", "Body"))], expected_revision=0)
            with self.assertRaises(UnusableWriter):
                writer.snapshot()
            self.assertEqual(before, self.raw())

    def test_gate_release_failure_after_sync_leaves_unknown_outcome(self):
        with Writer(self.path) as writer:
            with patch(
                "indexweave.files.portalocker.unlock",
                side_effect=OSError("Injected unlock failure"),
            ):
                with self.assertRaises(CommitUnknown):
                    writer.write(
                        [Put(Document("a", "Title", "Body"))], expected_revision=0
                    )
            with self.assertRaises(UnusableWriter):
                writer.snapshot()
        self.assertEqual(read_snapshot(self.path).revision, 1)

    def test_repair_sync_failure_requires_fresh_inspection(self):
        from indexweave.codec import encode_frame, operations_payload

        frame = encode_frame(
            operations_payload([Put(Document("a", "Title", "Body"))]), 1
        )
        with (self.path / LOG).open("ab") as stream:
            stream.write(frame[:70])
        report = inspect(self.path)
        with patch(
            "indexweave.storage.sync_file", side_effect=OSError("Injected sync failure")
        ):
            with self.assertRaises(CommitUnknown):
                repair_tail(
                    self.path,
                    expected_log_sha256=report.log_sha256,
                    expected_valid_bytes=report.valid_bytes,
                )
        self.assertEqual(inspect(self.path).tail_bytes, 0)
        self.assertEqual(read_snapshot(self.path).revision, 0)
