import tempfile
import unittest
from pathlib import Path

from indexweave import Document, Put, Writer, inspect, read_snapshot
from indexweave.errors import InvalidInput
from indexweave.files import LOG
from indexweave.model import MAX_DOCUMENTS, MAX_LIVE_BYTES


class LimitTests(unittest.TestCase):
    def test_live_document_count_limit_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store"
            with Writer.create(path) as writer:
                for start in range(0, MAX_DOCUMENTS, 100):
                    writer.write(
                        [
                            Put(Document(str(i), "Title", "Body"))
                            for i in range(start, start + 100)
                        ],
                        expected_revision=writer.snapshot().revision,
                    )
                before = (path / LOG).stat().st_size
                with self.assertRaises(InvalidInput):
                    writer.write(
                        [Put(Document("overflow", "Title", "Body"))],
                        expected_revision=100,
                    )
                self.assertEqual(writer.snapshot().revision, 100)
                self.assertEqual((path / LOG).stat().st_size, before)
            self.assertEqual(len(read_snapshot(path).documents), MAX_DOCUMENTS)

    def test_live_byte_limit_is_atomic_with_real_document_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store"
            with Writer.create(path) as writer:
                text = "x" * 65536
                for start in range(0, 510, 15):
                    writer.write(
                        [
                            Put(Document(str(i), "Title", text))
                            for i in range(start, start + 15)
                        ],
                        expected_revision=writer.snapshot().revision,
                    )
                before = inspect(path)
                self.assertLessEqual(before.live_bytes, MAX_LIVE_BYTES)
                with self.assertRaises(InvalidInput):
                    writer.write(
                        [Put(Document(str(i), "Title", text)) for i in range(510, 525)],
                        expected_revision=writer.snapshot().revision,
                    )
                after = inspect(path)
                self.assertEqual(before, after)
                self.assertEqual(after.document_count, 510)
