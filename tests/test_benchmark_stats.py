import copy
import unittest

from indexweave.benchmark_stats import query_summary, summarize


def fixture():
    return [
        {
            "repeat": number,
            "completed": True,
            "identity": {"corpus": "fixed", "model": "fixed"},
            "phase_ns": {"load": [90, 10, 30, 20, 100][number]},
            "query_ns": {
                mode: list(range(1 + number * 100, 101 + number * 100))
                for mode in ("text", "semantic", "hybrid")
            },
        }
        for number in range(5)
    ]


class BenchmarkStatisticsTests(unittest.TestCase):
    def test_nearest_rank_and_even_median(self):
        self.assertEqual(
            query_summary(list(range(100, 0, -1))),
            {"count": 100, "median_ns": 50.5, "p95_ns": 95},
        )

    def test_worker_percentiles_remain_separate_and_input_is_unchanged(self):
        rows = fixture()
        original = copy.deepcopy(rows)
        result = summarize(rows)
        self.assertEqual(result["phase_median_ns"], {"load": 30})
        self.assertEqual(
            [row["modes"]["hybrid"]["p95_ns"] for row in result["query_per_worker"]],
            [95, 195, 295, 395, 495],
        )
        self.assertEqual(rows, original)

    def test_missing_failed_duplicate_or_different_workloads_refused(self):
        mutations = [
            lambda rows: rows.pop(),
            lambda rows: rows[2].update(completed=False),
            lambda rows: rows[2].update(repeat=1),
            lambda rows: rows[2].update(identity={"corpus": "other"}),
            lambda rows: rows[2].update(phase_ns={"other": 1}),
            lambda rows: rows[2].update(query_ns={"text": list(range(100))}),
        ]
        for mutate in mutations:
            rows = fixture()
            mutate(rows)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                summarize(rows)

    def test_invalid_observations_refused(self):
        for bad in (-1, True, 1.5, float("nan"), float("inf"), "1"):
            rows = fixture()
            rows[1]["query_ns"]["text"][0] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                summarize(rows)


if __name__ == "__main__":
    unittest.main()
