"""Mechanical fake-encoder oracles, not evidence of neural relevance."""

import hashlib
import math
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from indexweave import Delete, Document, LexicalIndex, Put, Writer
from indexweave.embedding import _checked_bytes, acquire_model, pins, verify_model
from indexweave.errors import InvalidInput, StaleIndex
from indexweave.semantic import (
    DIMENSIONS,
    SemanticIndex,
    document_passages,
    hybrid_search,
    normalized_matrix,
)


class FakeEncoder:
    identity = {"id": "synthetic-unit-fixture"}

    def __init__(self):
        self.calls = 0

    def offsets(self, text):
        return tuple((m.start(), m.end()) for m in re.finditer(r"\S+", text))

    def token_count(self, text):
        return len(self.offsets(text)) + 2

    def encode_documents(self, texts):
        self.calls += 1
        return self.encode_queries(texts)

    def encode_queries(self, texts):
        rows = []
        for text in texts:
            values = np.zeros(DIMENSIONS, dtype=np.float32)
            values[0] = 1 + text.count("red")
            values[1] = 1 + text.count("blue")
            values[2] = text.count("fox")
            rows.append(values)
        return np.array(rows, dtype=np.float32).reshape(len(rows), DIMENSIONS)


class SemanticTests(unittest.TestCase):
    def make(self, root):
        writer = Writer.create(Path(root) / "source")
        snapshot = writer.write(
            [
                Put(Document("a", "Red", "red fox")),
                Put(Document("b", "Blue", "blue fox")),
            ],
            expected_revision=0,
        )
        return writer, snapshot

    def test_cosine_against_independent_scalar_oracle_and_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            writer, snapshot = self.make(root)
            with writer:
                encoder = FakeEncoder()
                index = SemanticIndex.from_snapshot(snapshot, encoder)
                result = index.search("red", expected_snapshot=snapshot)
                query = [2, 1, 0]
                expected = {}
                for passage in index.passages:
                    text = passage.encoded_text
                    vector = [
                        1 + text.count("red"),
                        1 + text.count("blue"),
                        text.count("fox"),
                    ]
                    dot = sum(a * b for a, b in zip(query, vector, strict=True))
                    expected[passage.document_id] = dot / math.sqrt(
                        sum(x * x for x in query) * sum(x * x for x in vector)
                    )
                self.assertEqual(
                    [h["id"] for h in result["hits"]],
                    sorted(expected, key=lambda key: (-expected[key], key)),
                )
                for hit in result["hits"]:
                    self.assertAlmostEqual(hit["score"], expected[hit["id"]], places=6)
                    record = snapshot.get(hit["id"])
                    passage = hit["passage"]
                    self.assertEqual(
                        passage["text"],
                        record.document.text[passage["start"] : passage["end"]],
                    )
                with self.assertRaises(ValueError):
                    index.vectors.setflags(write=True)
                with self.assertRaises(ValueError):
                    index.vectors[0, 0] = 100

    def test_chunks_offsets_overlap_title_caps_and_no_redundant_suffix(self):
        encoder = FakeEncoder()
        text = " ".join("w" + str(i) for i in range(352))
        title = " ".join("T" + str(i) for i in range(40))
        document = Document("a", title, text)
        chunks = document_passages(document, encoder)
        offsets = encoder.offsets(text)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].end, offsets[191][1])
        self.assertEqual(chunks[1].start, offsets[160][0])
        self.assertEqual(chunks[1].end, len(text))
        self.assertEqual(chunks[0].title_end, encoder.offsets(title)[31][1])
        unicode = document_passages(
            Document("u", "Résumé", "🙂 Café\u0301 Straße"), encoder
        )[0]
        self.assertEqual(unicode.encoded_text, "Résumé\n🙂 Café\u0301 Straße")
        self.assertEqual(unicode.end, len("🙂 Café\u0301 Straße"))
        with patch("indexweave.semantic.MAX_DOCUMENT_CHUNKS", 1):
            with self.assertRaises(InvalidInput):
                document_passages(document, encoder)

    def test_source_updates_deletes_old_views_and_stale_guard(self):
        with tempfile.TemporaryDirectory() as root:
            writer, first = self.make(root)
            with writer:
                old = SemanticIndex.from_snapshot(first, FakeEncoder())
                second = writer.write(
                    [Delete("b"), Put(Document("a", "Changed", "new blue"))],
                    expected_revision=1,
                )
                fresh = SemanticIndex.from_snapshot(second, FakeEncoder())
                with self.assertRaises(StaleIndex):
                    old.search("red", expected_snapshot=second)
                self.assertEqual(
                    {
                        h["id"]
                        for h in old.search("red", expected_snapshot=first)["hits"]
                    },
                    {"a", "b"},
                )
                hit = fresh.search("blue", expected_snapshot=second)["hits"][0]
                self.assertEqual(
                    (hit["id"], hit["document_revision"], hit["passage"]["text"]),
                    ("a", 2, "new blue"),
                )
                with self.assertRaises(StaleIndex):
                    hybrid_search(
                        LexicalIndex.from_snapshot(first),
                        fresh,
                        "blue",
                        expected_snapshot=second,
                    )

    def test_preflight_before_inference_and_empty_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            writer, snapshot = self.make(root)
            with writer:
                encoder = FakeEncoder()
                with patch("indexweave.semantic.MAX_CHUNKS", 1):
                    with self.assertRaises(InvalidInput):
                        SemanticIndex.from_snapshot(snapshot, encoder)
                self.assertEqual(encoder.calls, 0)
                empty = writer.write([Delete("a"), Delete("b")], expected_revision=1)
                index = SemanticIndex.from_snapshot(empty, encoder)
                self.assertEqual(
                    index.search("red", expected_snapshot=empty)["hits"], []
                )

    def test_vector_and_query_refusal(self):
        for values in (
            np.zeros((1, DIMENSIONS)),
            np.full((1, DIMENSIONS), float("nan")),
            np.full((1, DIMENSIONS), float("inf")),
            np.ones((1, 3)),
            np.ones((1, DIMENSIONS), dtype=int),
        ):
            with self.assertRaises(InvalidInput):
                normalized_matrix(values, 1)
        with tempfile.TemporaryDirectory() as root:
            writer, snapshot = self.make(root)
            with writer:
                encoder = FakeEncoder()
                index = SemanticIndex.from_snapshot(snapshot, encoder)
                for query in ("", "?", "w " * 129, "x" * 2049):
                    with self.assertRaises(InvalidInput):
                        index.search(query, expected_snapshot=snapshot)
                with patch.object(encoder, "token_count", return_value=257):
                    with self.assertRaises(InvalidInput):
                        index.search("red", expected_snapshot=snapshot)
                for limit in (False, 0, 101):
                    with self.assertRaises(InvalidInput):
                        index.search("red", expected_snapshot=snapshot, limit=limit)

    def test_hybrid_document_dedup_ranks_and_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            writer, snapshot = self.make(root)
            with writer:
                lexical = LexicalIndex.from_snapshot(snapshot)
                semantic = SemanticIndex.from_snapshot(snapshot, FakeEncoder())
                result = hybrid_search(
                    lexical, semantic, "red", expected_snapshot=snapshot
                )
                self.assertEqual(
                    len({h["id"] for h in result["hits"]}), len(result["hits"])
                )
                top = result["hits"][0]
                self.assertAlmostEqual(
                    top["score"],
                    1 / (60 + top["lexical_rank"]) + 1 / (60 + top["semantic_rank"]),
                )


class FreeTextTests(unittest.TestCase):
    def test_or_terms_punctuation_repetition_and_strict_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            with Writer.create(Path(root) / "source") as writer:
                snapshot = writer.write(
                    [
                        Put(Document("a", "One", "red")),
                        Put(Document("b", "Two", "blue")),
                        Put(Document("c", "Three", "red blue")),
                    ],
                    expected_revision=0,
                )
                index = LexicalIndex.from_snapshot(snapshot)
                self.assertEqual(index.search("red blue")["total_matches"], 1)
                result = index.rank_text("red, blue?")
                self.assertEqual(result["total_matches"], 3)
                self.assertEqual(
                    result["hits"], index.rank_text("red red blue")["hits"]
                )
                for hit in result["hits"]:
                    self.assertAlmostEqual(
                        hit["score"], sum(t["contribution"] for t in hit["term_scores"])
                    )
                self.assertEqual(index.rank_text("unknown")["hits"], [])
                with self.assertRaises(InvalidInput):
                    index.rank_text("?")


class AssetTests(unittest.TestCase):
    def test_asset_checks_git_blob_lfs_length_and_pins(self):
        raw = b"fixture"
        sha = hashlib.sha256(raw).hexdigest()
        git = hashlib.sha1(b"blob 7\0fixture").hexdigest()
        for asset in (
            {"bytes": 7, "lfs_sha256": sha},
            {"bytes": 7, "lfs_sha256": None, "git_blob": git},
        ):
            self.assertEqual(_checked_bytes(raw, asset), sha)
            with self.assertRaises(InvalidInput):
                _checked_bytes(b"changed", asset)
        with self.assertRaises(InvalidInput):
            _checked_bytes(raw, {"bytes": 8})
        self.assertEqual(len(pins()["assets"]), 11)
        self.assertTrue(
            all(not name.endswith((".bin", ".py")) for name in pins()["assets"])
        )

    def test_existing_destination_and_unexpected_files_refused_before_network(self):
        with tempfile.TemporaryDirectory() as root:
            with patch("urllib.request.urlopen", side_effect=AssertionError("network")):
                with self.assertRaises(FileExistsError):
                    acquire_model(root)
            with self.assertRaises(InvalidInput):
                verify_model(root)
