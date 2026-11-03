import json
import math
import subprocess
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

from indexweave import Delete, Document, LexicalIndex, Put, Writer
from indexweave.analysis import analyze
from indexweave.errors import InvalidInput
from indexweave.query import parse_query


def index_of(documents):
    with tempfile.TemporaryDirectory() as parent:
        with Writer.create(parent + "/store") as writer:
            snapshot = writer.write(
                [Put(doc) for doc in documents], expected_revision=0
            )
            return LexicalIndex.from_snapshot(snapshot)


def ids(result):
    return [hit["id"] for hit in result["hits"]]


class AnalysisTests(unittest.TestCase):
    def test_unicode_offsets_and_normalization(self):
        source = "Cafe\u0301 Straße ＦＯＯ नेपाली 中文 😀"
        tokens = analyze(source)
        self.assertEqual(
            [t.term for t in tokens], ["café", "strasse", "ｆｏｏ", "नेपाली", "中文"]
        )
        self.assertEqual(
            [source[t.start : t.end] for t in tokens],
            ["Cafe\u0301", "Straße", "ＦＯＯ", "नेपाली", "中文"],
        )
        self.assertEqual([(t.start, t.end) for t in tokens[:2]], [(0, 5), (6, 12)])
        self.assertEqual(analyze("😀 --"), ())

    def test_fixed_pattern_and_term_limits(self):
        self.assertEqual(
            [t.term for t in analyze("a_b c++ x-y")], ["a", "b", "c", "x", "y"]
        )
        for text in ["a" * 257, "\ud800", "a " * 32769, ""]:
            with self.subTest(text_length=len(text)), self.assertRaises(InvalidInput):
                analyze(text)
        self.assertEqual(len(analyze("a " * 32768)), 32768)
        with self.assertRaises(FrozenInstanceError):
            analyze("hello")[0].term = "changed"

    def test_grammar_and_no_executable_syntax(self):
        clauses = parse_query('Café "red, fox" tag:notes')
        self.assertEqual(
            [(c.kind, c.terms) for c in clauses],
            [("term", ("café",)), ("phrase", ("red", "fox")), ("tag", ("notes",))],
        )
        invalid = [
            "",
            "   ",
            '""',
            '"..."',
            '"unclosed',
            '"one"two',
            'one"two',
            "x OR y",
            "(x)",
            "x*",
            "tag:",
            "tag:Upper",
            "title:foo",
            "a-b",
            "x\\y",
            "\x00",
            "x " * 17,
            '"' + "x " * 9 + '"',
            "a" * 2049,
        ]
        for query in invalid:
            with self.subTest(query=query), self.assertRaises(InvalidInput):
                parse_query(query)
        self.assertEqual(len(parse_query(" ".join(['"a b c d e f g h"'] * 4))), 4)
        with self.assertRaises(InvalidInput):
            parse_query(" ".join(['"a b c d e f g h"'] * 5))


class LexicalTests(unittest.TestCase):
    def test_required_terms_tags_and_field_phrase_boundaries(self):
        index = index_of(
            [
                Document("a", "red", "fox red fox", ("notes",)),
                Document("b", "red", "fox", ("notes",)),
                Document("c", "other", "red fast fox", ("draft",)),
            ]
        )
        self.assertEqual(set(ids(index.search("red fox"))), {"a", "b", "c"})
        self.assertEqual(ids(index.search('"red fox"')), ["a"])
        self.assertEqual(ids(index.search("red tag:notes tag:draft")), [])
        self.assertEqual(ids(index.search("tag:notes")), ["a", "b"])
        self.assertEqual(ids(index.search("tag:missing")), [])
        self.assertEqual(ids(index.search("missing")), [])
        self.assertTrue(all(h["score"] == 0 for h in index.search("tag:notes")["hits"]))

    def test_repeated_terms_and_phrase_adjacency(self):
        index = index_of(
            [Document("a", "title", "go go go"), Document("b", "title", "go stop go")]
        )
        self.assertEqual(ids(index.search('"go go go"')), ["a"])
        self.assertEqual(ids(index.search('"go go go go"')), [])
        once = index.search("go")["hits"]
        repeated = index.search("go go")["hits"]
        self.assertEqual([h["score"] for h in once], [h["score"] for h in repeated])
        self.assertEqual([len(h["term_scores"]) for h in repeated], [1, 1])

    def test_hand_calculated_bm25_and_global_statistics(self):
        index = index_of(
            [
                Document("a", "---", "apple apple", ("one",)),
                Document("b", "---", "apple pear"),
                Document("c", "---", "..."),
            ]
        )
        hits = index.search("apple")["hits"]
        idf = math.log(1 + 1.5 / 2.5)
        norm = 1.2 * (0.25 + 0.75 * 2 / (4 / 3))
        self.assertEqual(ids(index.search("apple")), ["a", "b"])
        self.assertAlmostEqual(hits[0]["score"], idf * 2 * 2.2 / (2 + norm))
        self.assertAlmostEqual(hits[1]["score"], idf * 1 * 2.2 / (1 + norm))
        self.assertEqual(hits[0]["term_scores"][0]["df"], 2)
        self.assertEqual(
            hits[0]["score"], index.search("apple tag:one")["hits"][0]["score"]
        )
        self.assertEqual(index.metadata()["token_occurrences"], 4)

    def test_evidence_preserves_original_source(self):
        index = index_of(
            [Document("a", "Cafe\u0301", "Straße red, fox <script>alert(1)</script>")]
        )
        result = index.search('café "RED FOX"')
        hit = result["hits"][0]
        self.assertEqual(
            hit["matches"],
            [
                {"clause": 0, "field": "title", "start": 0, "end": 5},
                {"clause": 1, "field": "text", "start": 7, "end": 15},
            ],
        )
        self.assertEqual(hit["passage"]["text"], "Cafe\u0301")
        source = index.snapshot.documents["a"].document.text
        match = hit["matches"][1]
        self.assertEqual(source[match["start"] : match["end"]], "red, fox")
        self.assertIn("<script>", index.search("strasse")["hits"][0]["passage"]["text"])

    def test_snapshot_update_delete_reopen_and_identity(self):
        with tempfile.TemporaryDirectory() as parent:
            with Writer.create(parent + "/store") as writer:
                first = writer.write(
                    [
                        Put(Document("a", "Old", "alpha")),
                        Put(Document("b", "Other", "beta")),
                    ],
                    expected_revision=0,
                )
                old = LexicalIndex.from_snapshot(first)
                writer.write(
                    [Put(Document("a", "New", "gamma")), Delete("b")],
                    expected_revision=1,
                )
            with Writer(parent + "/store") as writer:
                new = LexicalIndex.from_snapshot(writer.snapshot())
            self.assertEqual(ids(old.search("alpha")), ["a"])
            self.assertEqual(ids(old.search("beta")), ["b"])
            self.assertEqual(ids(new.search("alpha")), [])
            self.assertEqual(ids(new.search("beta")), [])
            self.assertEqual(ids(new.search("gamma")), ["a"])
            self.assertEqual(new.search("gamma")["hits"][0]["document_revision"], 2)
            self.assertNotEqual(old.content_sha256, new.content_sha256)
            self.assertEqual(
                new.content_sha256,
                LexicalIndex.from_snapshot(new.snapshot).content_sha256,
            )
            self.assertEqual(old.metadata()["snapshot_revision"], 1)
            with self.assertRaises(TypeError):
                old.postings["alpha"]["a"]["text"] = (999,)
            with self.assertRaises(TypeError):
                old.tokens["a"]["text"] = ()
            metadata = old.metadata()
            metadata["scoring"] = "changed"
            self.assertNotEqual(old.metadata()["scoring"], "changed")

    def test_limits_fail_without_store_or_prior_index_changes(self):
        original = index_of([Document("a", "title", "alpha beta gamma")])
        for name, cap in [("MAX_OCCURRENCES", 3), ("MAX_TERMS", 2)]:
            with (
                patch("indexweave.lexical." + name, cap),
                self.assertRaises(InvalidInput),
            ):
                LexicalIndex.from_snapshot(original.snapshot)
        self.assertEqual(ids(original.search("alpha")), ["a"])
        for limit in [True, 0, 101, 1.5, None]:
            with self.assertRaises(InvalidInput):
                original.search("alpha", limit=limit)
        with self.assertRaises(InvalidInput):
            LexicalIndex.from_snapshot({})

    def test_total_before_limit_ties_and_empty_collection(self):
        index = index_of([Document(str(i), "title", "same") for i in range(15)])
        result = index.search("same", limit=3)
        self.assertEqual(result["total_matches"], 15)
        self.assertEqual(ids(result), ["0", "1", "10"])
        with tempfile.TemporaryDirectory() as parent:
            with Writer.create(parent + "/store") as writer:
                empty = LexicalIndex.from_snapshot(writer.snapshot())
            self.assertEqual(empty.search("missing")["total_matches"], 0)
            self.assertEqual(empty.search("tag:notes")["hits"], [])

    @settings(max_examples=60, deadline=None, derandomize=True, database=None)
    @given(
        st.lists(
            st.lists(
                st.sampled_from(["red", "fox", "go", "blue"]), min_size=1, max_size=12
            ),
            min_size=1,
            max_size=12,
        )
    )
    def test_postings_against_linear_scan_oracle(self, corpus):
        index = index_of(
            [
                Document(
                    str(i), "---", " ".join(words), ("even",) if i % 2 == 0 else ()
                )
                for i, words in enumerate(corpus)
            ]
        )
        for query, required, phrase, even in [
            ("red fox", ["red", "fox"], False, False),
            ('"go go"', ["go", "go"], True, False),
            ('"red fox" tag:even', ["red", "fox"], True, True),
            ("blue tag:even", ["blue"], False, True),
        ]:
            expected = []
            for i, words in enumerate(corpus):
                matches = (
                    any(
                        words[p : p + len(required)] == required
                        for p in range(len(words))
                    )
                    if phrase
                    else all(word in words for word in required)
                )
                if matches and (not even or i % 2 == 0):
                    score = 0
                    for word in sorted(set(required)):
                        df = sum(word in doc for doc in corpus)
                        tf = words.count(word)
                        avg = sum(map(len, corpus)) / len(corpus)
                        score += (
                            math.log(1 + (len(corpus) - df + 0.5) / (df + 0.5))
                            * tf
                            * 2.2
                            / (tf + 1.2 * (0.25 + 0.75 * len(words) / avg))
                        )
                    expected.append((str(i), score))
            expected.sort(key=lambda item: (-item[1], item[0]))
            actual = index.search(query, limit=100)
            self.assertEqual(ids(actual), [item[0] for item in expected])
            for hit, (_, score) in zip(actual["hits"], expected, strict=True):
                self.assertAlmostEqual(hit["score"], score)

    def test_installed_cli_search_does_not_mutate_log(self):
        with tempfile.TemporaryDirectory() as parent:
            with Writer.create(parent + "/store") as writer:
                writer.write(
                    [Put(Document("a", "Title", "alpha beta", ("notes",)))],
                    expected_revision=0,
                )
            from pathlib import Path

            path = Path(parent + "/store/documents.iwl")
            before = path.read_bytes()
            process = subprocess.run(
                [
                    "indexweave",
                    "search",
                    parent + "/store",
                    "--query",
                    '"alpha beta" tag:notes',
                ],
                text=True,
                capture_output=True,
                check=True,
            )
            result = json.loads(process.stdout)
            self.assertEqual(ids(result), ["a"])
            self.assertEqual(result["index"]["snapshot_revision"], 1)
            invalid = subprocess.run(
                ["indexweave", "search", parent + "/store", "--query", "x OR y"],
                text=True,
                capture_output=True,
            )
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(path.read_bytes(), before)
