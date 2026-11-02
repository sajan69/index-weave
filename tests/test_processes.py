import selectors
import signal
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from indexweave import Document, Put, Writer, inspect, read_snapshot, repair_tail
from indexweave.errors import Busy, Conflict, IncompleteTail


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "workspace"
        with Writer.create(self.path):
            pass

    def child_exit(self, mode):
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("crash_worker.py")),
                str(self.path),
                mode,
            ],
            capture_output=True,
            timeout=15,
        ).returncode

    def test_exit_before_write_leaves_revision_zero_and_releases_lease(self):
        self.assertEqual(self.child_exit("before"), 73)
        self.assertEqual(read_snapshot(self.path).revision, 0)
        with Writer(self.path):
            pass

    def test_exit_after_partial_write_requires_explicit_repair(self):
        self.assertEqual(self.child_exit("partial"), 73)
        report = inspect(self.path)
        self.assertEqual(report.tail_bytes, 70)
        with self.assertRaises(IncompleteTail):
            read_snapshot(self.path)
        repair_tail(
            self.path,
            expected_log_sha256=report.log_sha256,
            expected_valid_bytes=report.valid_bytes,
        )
        self.assertEqual(read_snapshot(self.path).revision, 0)

    def test_sigkill_after_fsync_retains_complete_unacknowledged_commit(self):
        self.assertEqual(self.child_exit("synced"), -signal.SIGKILL)
        self.assertEqual(
            read_snapshot(self.path).get("crash").document.text, "Synthetic body"
        )
        with Writer(self.path):
            pass

    def test_second_writer_refused_in_same_and_separate_process(self):
        script = """import sys
from indexweave import Writer
from indexweave.errors import Busy
try:
    with Writer(sys.argv[1]): pass
except Busy:
    print("busy")
else:
    print("opened")
"""
        with Writer(self.path):
            with self.assertRaises(Busy):
                Writer(self.path)
            result = subprocess.run(
                [sys.executable, "-c", script, str(self.path)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "busy")
            self.assertEqual(read_snapshot(self.path).revision, 0)
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.stdout.strip(), "opened")

    def test_exclusive_gate_blocks_readers_and_write_without_poisoning(self):
        script = """import sys
from indexweave.files import workspace,gated
with workspace(sys.argv[1]) as root, gated(root):
    print("ready",flush=True)
    sys.stdin.readline()
"""
        with Writer(self.path) as writer:
            child = subprocess.Popen(
                [sys.executable, "-c", script, str(self.path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(child.stdout, selectors.EVENT_READ)
                    self.assertTrue(
                        selector.select(timeout=10), "Child did not signal readiness"
                    )
                self.assertEqual(child.stdout.readline().strip(), "ready")
                with self.assertRaises(Busy):
                    read_snapshot(self.path)
                with self.assertRaises(Busy):
                    writer.write(
                        [Put(Document("a", "Title", "Body"))], expected_revision=0
                    )
                self.assertEqual(writer.snapshot().revision, 0)
                _, stderr = child.communicate("done\n", timeout=15)
                self.assertEqual(child.returncode, 0, stderr)
                writer.write([Put(Document("a", "Title", "Body"))], expected_revision=0)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=15)

    def test_threaded_compare_and_swap_has_one_winner(self):
        with Writer(self.path) as writer:

            def attempt(number):
                try:
                    writer.write(
                        [Put(Document(str(number), "Title", "Body"))],
                        expected_revision=0,
                    )
                    return "saved"
                except Conflict:
                    return "conflict"

            with ThreadPoolExecutor(max_workers=8) as pool:
                outcomes = list(pool.map(attempt, range(8)))
            self.assertEqual(outcomes.count("saved"), 1)
            self.assertEqual(outcomes.count("conflict"), 7)
        self.assertEqual(len(read_snapshot(self.path).documents), 1)
