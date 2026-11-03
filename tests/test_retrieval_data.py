"""Synthetic mechanical fixtures, not human-labelled relevance evidence."""

import copy
import hashlib
import math
import unittest
from unittest.mock import patch

from indexweave.errors import InvalidInput
from indexweave.retrieval_data import (
    project_hotpot,
    reciprocal_rank_fusion,
    retrieval_metrics,
    select_rows,
)


def row(identifier, title="Shared title", sentences=None):
    return {
        "id": identifier,
        "question": "Which fictional source contains the note?",
        "answer": "THIS MUST NOT ENTER THE CORPUS",
        "context": {
            "title": [title, "Distractor " + identifier],
            "sentences": [
                sentences or ["A note. ", "Another sentence."],
                ["Unrelated."],
            ],
        },
        "supporting_facts": {"title": [title, title], "sent_id": [0, 1]},
    }


class ProjectionTests(unittest.TestCase):
    def test_selection_uses_ids_only_and_does_not_replace_invalid_selected_rows(self):
        rows = [row(str(i)) for i in range(6)]
        expected = sorted(
            rows,
            key=lambda item: hashlib.sha256(
                ("indexweave-hotpot-v1:" + item["id"]).encode()
            ).digest(),
        )[:4]
        selected = select_rows(list(reversed(rows)), count=4, development=2)
        self.assertEqual(
            [item["id"] for item, _ in selected], [r["id"] for r in expected]
        )
        self.assertEqual(
            [split for _, split in selected], ["development"] * 2 + ["holdout"] * 2
        )
        expected[0]["question"] = ""
        with self.assertRaises(InvalidInput):
            project_hotpot(rows, count=4, development=2)

    def test_projection_keeps_distractors_deduplicates_and_preserves_source(self):
        rows = [row("a"), row("b")]
        original = copy.deepcopy(rows)
        corpus = project_hotpot(rows, count=2, development=1)
        self.assertEqual(len(corpus.passages), 3)
        shared = next(p for p in corpus.passages if p.document.title == "Shared title")
        self.assertEqual(shared.document.text, "A note. Another sentence.")
        self.assertEqual(shared.sentences, ("A note. ", "Another sentence."))
        self.assertEqual(
            shared.source_url, "https://en.wikipedia.org/wiki/Shared_title"
        )
        self.assertEqual(len(shared.document.id), 63)
        self.assertEqual(
            {q.relevant for q in corpus.questions}, {frozenset([shared.document.id])}
        )
        self.assertNotIn(rows[0]["answer"], repr(corpus))
        self.assertEqual(rows, original)
        again = project_hotpot(list(reversed(rows)), count=2, development=1)
        self.assertEqual(corpus, again)
        with self.assertRaises(AttributeError):
            shared.document.text = "changed"

    def test_title_variants_remain_distinct_and_source_urls_are_encoded(self):
        corpus = project_hotpot(
            [
                row("a", "A/B?", ["One. ", "Two."]),
                row("b", "A/B?", ["Different. ", "Two."]),
            ],
            count=2,
            development=1,
        )
        self.assertEqual(len(corpus.passages), 4)
        related = [p for p in corpus.passages if p.document.title == "A/B?"]
        self.assertNotEqual(related[0].sha256, related[1].sha256)
        self.assertTrue(all(p.source_url.endswith("A/B%3F") for p in related))
        self.assertNotEqual(corpus.questions[0].relevant, corpus.questions[1].relevant)

    def test_bad_annotations_and_schema_are_errors_not_excluded_rows(self):
        changes = [
            lambda r: r["supporting_facts"].update(sent_id=[False, 1]),
            lambda r: r["supporting_facts"].update(sent_id=[-1, 1]),
            lambda r: r["supporting_facts"].update(sent_id=[2, 1]),
            lambda r: r["supporting_facts"].update(title=["Missing", "Shared title"]),
            lambda r: r["supporting_facts"].update(title=[], sent_id=[]),
            lambda r: r["context"].update(title=["Shared title", "Shared title"]),
            lambda r: r["context"].update(sentences=[[1], ["Text"]]),
            lambda r: r["context"].update(extra=[]),
            lambda r: r["context"].update(title=[]),
            lambda r: r.update(question="x" * 2049),
            lambda r: r["context"].update(sentences=[["x" * 65537], ["Text"]]),
        ]
        for change in changes:
            rows = [row("a"), row("b")]
            change(rows[0])
            with self.subTest(change=change), self.assertRaises(InvalidInput):
                project_hotpot(rows, count=2, development=1)

    def test_selection_and_capacity_guards(self):
        for rows in ([row("a")], [row("a"), row("a")], [{"id": 1}, row("b")]):
            with self.assertRaises(InvalidInput):
                project_hotpot(rows, count=2, development=1)
        for count, development in ((True, 1), (201, 100), (2, 0), (2, 2)):
            with self.assertRaises(InvalidInput):
                select_rows([], count=count, development=development)
        with patch("indexweave.retrieval_data.MAX_DOCUMENTS", 2):
            with self.assertRaises(InvalidInput):
                project_hotpot([row("a"), row("b")], count=2, development=1)
        with patch("indexweave.retrieval_data.MAX_LIVE_BYTES", 10):
            with self.assertRaises(InvalidInput):
                project_hotpot([row("a"), row("b")], count=2, development=1)


class MetricTests(unittest.TestCase):
    def test_hand_calculated_full_qrel_denominator_and_rank_discount(self):
        result = retrieval_metrics(["noise", "a", "b"], {"a", "b", "c"}, k=3)
        self.assertEqual(result["recall"], 2 / 3)
        expected = (1 / math.log2(3) + 0.5) / (1 + 1 / math.log2(3) + 0.5)
        self.assertAlmostEqual(result["ndcg"], expected)
        self.assertFalse(result["all_support"])
        self.assertEqual(retrieval_metrics(["b", "a"], {"a", "b"})["ndcg"], 1)
        self.assertTrue(retrieval_metrics(["b", "a"], {"a", "b"})["all_support"])
        self.assertEqual(
            retrieval_metrics([], {"a"}), {"recall": 0, "ndcg": 0, "all_support": False}
        )
        self.assertEqual(retrieval_metrics(["a", "b"], {"a", "b"}, k=1)["recall"], 0.5)

    def test_invalid_metrics_never_silently_deduplicate_or_drop_qrels(self):
        for ranks, relevant in (
            (["a", "a"], {"a"}),
            ([1], {"a"}),
            (["a"], set()),
            (["a"], {1}),
            ({"a"}, {"a"}),
        ):
            with self.assertRaises(InvalidInput):
                retrieval_metrics(ranks, relevant)
        for k in (False, 0, 101):
            with self.assertRaises(InvalidInput):
                retrieval_metrics([], {"a"}, k=k)

    def test_rrf_missing_ranks_ties_window_and_invalid_duplicates(self):
        self.assertEqual(reciprocal_rank_fusion(["b", "a"], ["a", "b"]), ["a", "b"])
        self.assertEqual(
            reciprocal_rank_fusion(["b", "x"], ["a", "x"]), ["x", "a", "b"]
        )
        self.assertEqual(
            reciprocal_rank_fusion(["b", "x"], ["a", "x"], window=1), ["a", "b"]
        )
        self.assertEqual(reciprocal_rank_fusion([], []), [])
        for first, second in ((["a", "a"], []), ([1], []), ([], {"a"})):
            with self.assertRaises(InvalidInput):
                reciprocal_rank_fusion(first, second)
