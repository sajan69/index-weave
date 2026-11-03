import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_retrieval_sources.py"
spec = importlib.util.spec_from_file_location("source_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class SourceAuditTests(unittest.TestCase):
    def test_revision_and_asset_caps(self):
        self.assertEqual(audit.sha("a" * 40), "a" * 40)
        for value in ("main", "A" * 40, "../x", None, "a" * 39):
            with self.assertRaises(ValueError):
                audit.sha(value)
        for size in (0, 128 * 1024 * 1024 + 1):
            with self.assertRaises(ValueError):
                audit.selected_assets(
                    [{"type": "file", "path": "a", "size": size}], ["a"]
                )
        with self.assertRaises(KeyError):
            audit.selected_assets([], ["model.safetensors"])

    def test_bounded_metadata_read(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, count):
                return b"x" * count

        with patch.object(audit.urllib.request, "urlopen", return_value=Response()):
            with self.assertRaises(ValueError):
                audit.get("https://example.invalid", maximum=10)

    def test_audit_review_blocks_custom_model_modules_and_licence_changes(self):
        configurations = {
            "modules.json": [
                {"type": "sentence_transformers.models.Transformer"},
                {"type": "sentence_transformers.models.Pooling"},
            ],
            "config.json": {"model_type": "bert"},
            "sentence_bert_config.json": {"max_seq_length": 256},
            "1_Pooling/config.json": {},
        }

        def metadata(kind, name):
            return {
                "sha": "a" * 40,
                "cardData": {
                    "license": ["cc-by-sa-4.0"] if kind == "datasets" else "apache-2.0"
                },
            }

        def tree(kind, name, revision):
            files = [audit.DATA_FILE] if kind == "datasets" else audit.MODEL_FILES
            return [{"type": "file", "path": path, "size": 100} for path in files]

        def get(url):
            name = url.split(audit.MODEL_REVISION + "/", 1)[1]
            return json.dumps(configurations[name]).encode()

        with (
            patch.object(audit, "metadata", side_effect=metadata),
            patch.object(audit, "tree", side_effect=tree),
            patch.object(audit, "get", side_effect=get),
        ):
            result = audit.run()
            self.assertTrue(result["completed"])
            configurations["modules.json"].append(
                {"type": "sentence_transformers.models.Normalize"}
            )
            self.assertTrue(audit.run()["completed"])
            self.assertEqual(result["dataset"]["revision"], "a" * 40)
            configurations["config.json"]["auto_map"] = {"AutoModel": "custom.Code"}
            with self.assertRaises(ValueError):
                audit.run()
            configurations["config.json"].pop("auto_map")
            configurations["modules.json"][0]["type"] = "custom.Code"
            with self.assertRaises(ValueError):
                audit.run()
        with patch.object(
            audit,
            "metadata",
            return_value={"sha": "a" * 40, "cardData": {"license": "changed"}},
        ):
            with self.assertRaises(ValueError):
                audit.run()
