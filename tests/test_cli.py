import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from indexweave import Document, Put
from indexweave.codec import encode_frame, operations_payload
from indexweave.files import LOG


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "workspace"
        self.command = str(Path(sys.executable).with_name("indexweave"))

    def call(self, *args, expected=0):
        result = subprocess.run(
            [self.command, *map(str, args)], capture_output=True, text=True, timeout=15
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        return json.loads(result.stdout) if expected == 0 else result

    def test_installed_cli_document_lifecycle_and_metadata_pagination(self):
        self.call("create", self.path)
        text = Path(self.tmp.name) / "document.md"
        text.write_text("# Synthetic\nनमस्ते", encoding="utf-8")
        self.call(
            "put",
            self.path,
            "--id",
            "a",
            "--title",
            "Title",
            "--file",
            text,
            "--expected-revision",
            0,
        )
        got = self.call("get", self.path, "--id", "a")
        self.assertEqual(got["document"]["text"], text.read_text())
        self.assertEqual(got["document_revision"], 1)
        self.call(
            "put",
            self.path,
            "--id",
            "b",
            "--title",
            "Second",
            "--file",
            text,
            "--expected-revision",
            1,
        )
        first = self.call("list", self.path, "--limit", 1)
        self.assertEqual(first["next_after"], "a")
        self.assertNotIn("text", first["documents"][0])
        self.assertEqual(
            self.call("list", self.path, "--after", "a")["documents"][0]["id"], "b"
        )
        self.call("delete", self.path, "--id", "a", "--expected-revision", 2)
        self.call("get", self.path, "--id", "a", expected=2)
        self.assertEqual(self.call("inspect", self.path)["revision"], 3)

    def test_invalid_input_and_existing_workspace_never_reset_records(self):
        self.call("create", self.path)
        before = (self.path / LOG).read_bytes()
        self.call("create", self.path, expected=2)
        text = Path(self.tmp.name) / "bad.md"
        text.write_bytes(b"\xff")
        result = self.call(
            "put",
            self.path,
            "--id",
            "a",
            "--title",
            "Private title",
            "--file",
            text,
            "--expected-revision",
            0,
            expected=2,
        )
        self.assertNotIn("Private title", result.stderr)
        text.write_bytes(b"a" * 65537)
        self.call(
            "put",
            self.path,
            "--id",
            "a",
            "--title",
            "Title",
            "--file",
            text,
            "--expected-revision",
            0,
            expected=2,
        )
        self.assertEqual((self.path / LOG).read_bytes(), before)

    def test_inspection_and_explicit_cli_tail_repair(self):
        self.call("create", self.path)
        frame = encode_frame(
            operations_payload([Put(Document("a", "Private title", "Private body"))]), 1
        )
        with (self.path / LOG).open("ab") as stream:
            stream.write(frame[:60])
        report = self.call("inspect", self.path)
        self.assertNotIn("Private body", json.dumps(report))
        args = [
            "repair-tail",
            self.path,
            "--expected-log-sha256",
            report["log_sha256"],
            "--expected-valid-bytes",
            report["valid_bytes"],
        ]
        self.call(*args, expected=2)
        self.assertEqual(
            self.call(*args, "--discard-incomplete-tail")["status"], "clean"
        )
