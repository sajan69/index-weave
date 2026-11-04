"""Strict summaries for five complete bounded-runtime-v1 worker records."""

import math
import statistics


def _durations(values, count):
    if not isinstance(values, list) or len(values) != count:
        raise ValueError("Wrong observation count")
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("Durations require nonnegative integer nanoseconds")
    return sorted(values)


def query_summary(values):
    """Summarize one worker/mode, never an average of worker percentiles."""
    ordered = _durations(values, 100)
    return {
        "count": 100,
        "median_ns": statistics.median(ordered),
        "p95_ns": ordered[math.ceil(0.95 * len(ordered)) - 1],
    }


def summarize(workers):
    """Refuse missing/failed repetitions and mismatched workload identities."""
    if not isinstance(workers, list) or len(workers) != 5:
        raise ValueError("Exactly five worker records are required")
    if any(not isinstance(row, dict) for row in workers):
        raise ValueError("Worker records require objects")
    if any(
        row.get("completed") is not True
        or type(row.get("repeat")) is not int
        or row["repeat"] != number
        for number, row in enumerate(workers)
    ):
        raise ValueError("Every ordered repetition must complete")
    identity = workers[0].get("identity")
    if not isinstance(identity, dict) or not identity:
        raise ValueError("Workload identity is required")
    if any(row.get("identity") != identity for row in workers):
        raise ValueError("Worker workload identities differ")
    phases = workers[0].get("phase_ns")
    if not isinstance(phases, dict) or not phases:
        raise ValueError("Named phase measurements are required")
    for row in workers:
        if (
            not isinstance(row.get("phase_ns"), dict)
            or row["phase_ns"].keys() != phases.keys()
        ):
            raise ValueError("Worker phase sets differ")
        if not isinstance(row.get("query_ns"), dict) or set(row["query_ns"]) != {
            "text",
            "semantic",
            "hybrid",
        }:
            raise ValueError("All three query modes are required")
    return {
        "completed": True,
        "repetitions": 5,
        "phase_median_ns": {
            name: statistics.median(
                _durations([row["phase_ns"][name] for row in workers], 5)
            )
            for name in sorted(phases)
        },
        "query_per_worker": [
            {
                "repeat": row["repeat"],
                "modes": {
                    mode: query_summary(values)
                    for mode, values in row["query_ns"].items()
                },
            }
            for row in workers
        ],
    }
