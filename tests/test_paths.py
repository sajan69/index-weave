import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from indexweave import Document, Writer, inspect, read_snapshot
from indexweave.codec import MAX_LOG_BYTES
from indexweave.errors import Corruption, InvalidInput, InvalidWorkspace
from indexweave.files import LOG


class PathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.path = self.parent / "workspace"
        with Writer.create(self.path):
            pass

    def test_create_refuses_existing_directory_and_preserves_files(self):
        sentinel = self.path / "sentinel"
        sentinel.write_text("Keep")
        with self.assertRaises(FileExistsError):
            Writer.create(self.path)
        self.assertEqual(sentinel.read_text(), "Keep")

    def test_root_symlink_and_file_symlink_are_refused(self):
        alias = self.parent / "alias"
        alias.symlink_to(self.path, target_is_directory=True)
        with self.assertRaises(OSError):
            read_snapshot(alias)
        original = self.path / LOG
        outside = self.parent / "outside"
        original.rename(outside)
        original.symlink_to(outside)
        with self.assertRaises(OSError):
            read_snapshot(self.path)
        self.assertEqual(outside.read_bytes(), b"IWLOG\x00\x01\n")

    def test_hardlink_fifo_and_public_permissions_are_refused(self):
        log = self.path / LOG
        other = self.parent / "linked"
        os.link(log, other)
        with self.assertRaises(InvalidWorkspace):
            read_snapshot(self.path)
        other.unlink()
        log.chmod(0o644)
        with self.assertRaises(InvalidWorkspace):
            read_snapshot(self.path)
        log.chmod(0o600)
        log.unlink()
        os.mkfifo(log, 0o600)
        with self.assertRaises(InvalidWorkspace):
            read_snapshot(self.path)

    def test_unknown_entries_and_permissive_root_are_refused(self):
        extra = self.path / "unexpected"
        extra.write_text("Keep")
        with self.assertRaises(InvalidWorkspace):
            Writer(self.path)
        extra.unlink()
        self.path.chmod(0o755)
        with self.assertRaises(InvalidWorkspace):
            read_snapshot(self.path)
        self.path.chmod(0o700)

    def test_sparse_oversized_log_refused_before_read(self):
        with (self.path / LOG).open("r+b") as stream:
            stream.truncate(MAX_LOG_BYTES + 1)
        with patch(
            "indexweave.files.os.read", side_effect=AssertionError("Must not read")
        ):
            with self.assertRaises(Corruption):
                inspect(self.path)

    def test_document_unicode_type_and_byte_limits(self):
        invalid = [
            ("../x", "Title", "Body", ()),
            ("a", " ", "Body", ()),
            ("a", "Title", "", ()),
            ("a", "Title", "\x00", ()),
            ("a", "Title", "\ud800", ()),
            ("a", "Title", "é" * 32769, ()),
            ("a", "x" * 513, "Body", ()),
            ("a", "Title", "Body", ["tag"]),
            ("a", "Title", "Body", ("tag", "tag")),
            ("a", "Title", "Body", (True,)),
        ]
        for values in invalid:
            with self.subTest(values=str(values)[:30]), self.assertRaises(InvalidInput):
                Document(*values)
        self.assertEqual(Document("a", "नमस्ते", "Body\n\tText").id, "a")

    def test_failed_initialization_leaves_partial_directory_without_reuse(self):
        failed = self.parent / "failed"
        with patch(
            "indexweave.storage.sync_file",
            side_effect=OSError("Injected initial sync failure"),
        ):
            with self.assertRaises(OSError):
                Writer.create(failed)
        self.assertTrue(failed.is_dir())
        with self.assertRaises(InvalidWorkspace):
            Writer(failed)
        before = list(failed.iterdir())
        with self.assertRaises(FileExistsError):
            Writer.create(failed)
        self.assertEqual(list(failed.iterdir()), before)

    def test_unsupported_platform_refused_before_file_access(self):
        with patch("indexweave.files.os.name", "nt"):
            with patch(
                "indexweave.files.os.open", side_effect=AssertionError("No opens")
            ):
                with self.assertRaises(InvalidWorkspace):
                    read_snapshot(self.path)
