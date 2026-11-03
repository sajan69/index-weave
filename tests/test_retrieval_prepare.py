import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_retrieval_data.py"
spec = importlib.util.spec_from_file_location("source_prepare", SCRIPT)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def pins(raw=b"fixture"):
    return {
        "result": {
            "dataset": {
                "id": prepare.DATASET,
                "license": "CC-BY-SA-4.0",
                "revision": "a" * 40,
                "asset": {
                    prepare.DATA_FILE: {
                        "bytes": len(raw),
                        "lfs_sha256": hashlib.sha256(raw).hexdigest(),
                    }
                },
            }
        }
    }


class PreparationTests(unittest.TestCase):
    def test_fixed_source_rejects_mutable_revisions_and_missing_hashes(self):
        value = pins()
        url, asset = prepare.frozen_source(value)
        self.assertIn("/resolve/" + "a" * 40 + "/distractor/", url)
        self.assertEqual(asset["bytes"], 7)
        for key, invalid in (
            ("revision", "main"),
            ("id", "unreviewed/data"),
            ("license", "changed"),
        ):
            value = pins()
            value["result"]["dataset"][key] = invalid
            with self.assertRaises(ValueError):
                prepare.frozen_source(value)
        for key, invalid in (
            ("bytes", True),
            ("bytes", prepare.MAX_DOWNLOAD + 1),
            ("lfs_sha256", None),
            ("lfs_sha256", "x" * 64),
        ):
            value = pins()
            value["result"]["dataset"]["asset"][prepare.DATA_FILE][key] = invalid
            with self.assertRaises(ValueError):
                prepare.frozen_source(value)

    def test_download_verifies_both_length_and_digest_before_parsing(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, count):
                return b"fixture"

        with patch.object(prepare.urllib.request, "urlopen", return_value=Response()):
            self.assertEqual(prepare.acquire(pins()), b"fixture")
            for raw in (b"wrong!!", b"short"):
                with self.assertRaises(ValueError):
                    prepare.acquire(pins(raw))

    def test_existing_destination_is_never_reset_or_downloaded(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "existing"
            target.mkdir()
            sentinel = target / "keep"
            sentinel.write_bytes(b"original")
            with patch.object(
                prepare, "acquire", side_effect=AssertionError("download")
            ):
                with self.assertRaises(FileExistsError):
                    prepare.prepare(target)
                link = Path(root) / "dangling"
                link.symlink_to(Path(root) / "absent")
                with self.assertRaises(FileExistsError):
                    prepare.prepare(link)
            self.assertEqual(sentinel.read_bytes(), b"original")

    def test_invalid_acquisition_does_not_create_target(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            (directory / "examples").mkdir()
            (directory / "examples/retrieval-sources.json").write_text(
                json.dumps(pins())
            )
            with (
                patch.object(prepare, "ROOT", directory),
                patch.object(prepare, "acquire", side_effect=ValueError("hash")),
            ):
                with self.assertRaises(ValueError):
                    prepare.prepare(directory / "new")
            self.assertFalse((directory / "new").exists())
