"""Real local-model lifecycle smoke test on fictional text, not relevance labels."""

import json
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from indexweave import Delete, Document, LexicalIndex, Put, Writer
from indexweave.embedding import MiniLMEncoder
from indexweave.errors import StaleIndex
from indexweave.semantic import SemanticIndex, hybrid_search


def run(model_path):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "source"
        with Writer.create(root) as writer:
            first = writer.write(
                [
                    Put(Document("journal", "Journal", "A durable recovery journal.")),
                    Put(Document("draft", "Draft", "A draft about blue flowers.")),
                ],
                expected_revision=0,
            )
            # Block network connections during actual model load and inference.
            with patch.object(
                socket.socket, "connect", side_effect=AssertionError("network attempt")
            ):
                encoder = MiniLMEncoder(model_path)
                old = SemanticIndex.from_snapshot(first, encoder)
                first_result = old.search("recovery notes", expected_snapshot=first)
                second = writer.write(
                    [
                        Put(
                            Document(
                                "journal",
                                "Updated journal",
                                "An updated recovery guide.",
                            )
                        ),
                        Delete("draft"),
                    ],
                    expected_revision=1,
                )
                refused = False
                try:
                    old.search("recovery notes", expected_snapshot=second)
                except StaleIndex:
                    refused = True
                current = SemanticIndex.from_snapshot(second, encoder)
                result = hybrid_search(
                    LexicalIndex.from_snapshot(second),
                    current,
                    "recovery notes",
                    expected_snapshot=second,
                )
                assert refused and {h["id"] for h in result["hits"]} == {"journal"}
                assert {
                    h["id"]
                    for h in old.search("recovery notes", expected_snapshot=first)[
                        "hits"
                    ]
                } == {"journal", "draft"}
                assert result["hits"][0]["document_revision"] == 2
                assert (
                    result["hits"][0]["passage"]["text"] == "An updated recovery guide."
                )
                model_identity = dict(encoder.identity)
        before = (root / "documents.iwl").read_bytes()
        cli = subprocess.run(
            [
                str(Path(sys.executable).with_name("indexweave")),
                "search",
                str(root),
                "--mode",
                "semantic",
                "--model",
                str(model_path),
                "--query",
                "recovery notes",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        actual = json.loads(cli.stdout)
        assert (
            actual["index"]["snapshot_revision"] == 2
            and actual["hits"][0]["id"] == "journal"
        )
        assert before == (root / "documents.iwl").read_bytes()
        return {
            "completed": True,
            "initial_ids": [h["id"] for h in first_result["hits"]],
            "current_ids": [h["id"] for h in result["hits"]],
            "stale_refused": refused,
            "old_view_preserved": True,
            "query_source_unchanged": True,
            "installed_cli": True,
            "network_blocked_smoke": True,
            "model": model_identity,
        }


if __name__ == "__main__":
    print("SEMANTIC_DEMO=" + json.dumps(run(Path(sys.argv[1])), sort_keys=True))
