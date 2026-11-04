"""Run the precommitted public retrieval protocol; never train or tune a model."""

import hashlib
import importlib.metadata
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

from indexweave import Document, LexicalIndex, Put, Writer, read_snapshot
from indexweave.embedding import MiniLMEncoder
from indexweave.model import canonical
from indexweave.retrieval_data import reciprocal_rank_fusion, retrieval_metrics
from indexweave.semantic import SemanticIndex


def evaluate(data_directory, model_directory, output):
    if output.exists():
        raise FileExistsError("Refusing existing evaluation output")
    data = json.loads((data_directory / "corpus.json").read_text())
    frozen = json.loads(
        (
            Path(__file__).resolve().parents[1] / "examples/retrieval-data.json"
        ).read_text()
    )["result"]
    if data["provenance"] != frozen:
        raise ValueError("Corpus preparation summary differs from frozen protocol")
    # Exercise the actual custom source store rather than bypassing it with rows.
    store = data_directory / "source"
    documents = [
        Document(**{**row["document"], "tags": tuple(row["document"]["tags"])})
        for row in data["passages"]
    ]
    identity = {
        "protocol": "hotpot-pool-v1",
        "documents": [document.to_dict() for document in documents],
        "questions": data["questions"],
    }
    if hashlib.sha256(canonical(identity)).hexdigest() != frozen["projection_sha256"]:
        raise ValueError("Corpus content or qrels differs from the frozen projection")
    if [{"id": q["id"], "split": q["split"]} for q in data["questions"]] != frozen[
        "question_ids"
    ]:
        raise ValueError("Question order/split differs from the frozen selection")
    with Writer.create(store) as writer:
        for i in range(0, len(documents), 100):
            writer.write(
                [Put(document) for document in documents[i : i + 100]],
                expected_revision=writer.snapshot().revision,
            )
    snapshot = read_snapshot(store)
    start = time.perf_counter()
    lexical = LexicalIndex.from_snapshot(snapshot)
    lexical_seconds = time.perf_counter() - start
    encoder = MiniLMEncoder(model_directory)
    start = time.perf_counter()
    semantic = SemanticIndex.from_snapshot(snapshot, encoder)
    embedding_seconds = time.perf_counter() - start
    rows = []
    for question in data["questions"]:
        ranks = {}
        times = {}
        for name, search in (
            ("lexical", lambda text: lexical.rank_text(text, limit=100)),
            (
                "semantic",
                lambda text: semantic.search(
                    text, expected_snapshot=snapshot, limit=100
                ),
            ),
        ):
            before = time.perf_counter()
            result = search(question["text"])
            times[name] = time.perf_counter() - before
            ranks[name] = [hit["id"] for hit in result["hits"]]
        ranks["hybrid"] = reciprocal_rank_fusion(ranks["lexical"], ranks["semantic"])
        metrics = {
            name: retrieval_metrics(ranking, set(question["relevant"]))
            for name, ranking in ranks.items()
        }
        rows.append(
            {
                "id": question["id"],
                "split": question["split"],
                "relevant": question["relevant"],
                "ranked_ids": ranks,
                "metrics": metrics,
                "seconds": times,
            }
        )
    aggregates = {}
    for split in ("development", "holdout"):
        selected = [row for row in rows if row["split"] == split]
        if len(selected) != 100:
            raise ValueError("Evaluation must retain all 100 questions per split")
        aggregates[split] = {
            name: {
                metric: statistics.mean(
                    row["metrics"][name][metric] for row in selected
                )
                for metric in ("recall", "ndcg", "all_support")
            }
            for name in ("lexical", "semantic", "hybrid")
        }
        for method in ("semantic", "hybrid"):
            aggregates[split]["paired_" + method + "_vs_lexical"] = {
                "improved": sum(
                    row["metrics"][method]["ndcg"] > row["metrics"]["lexical"]["ndcg"]
                    for row in selected
                ),
                "worsened": sum(
                    row["metrics"][method]["ndcg"] < row["metrics"]["lexical"]["ndcg"]
                    for row in selected
                ),
                "unchanged": sum(
                    row["metrics"][method]["ndcg"] == row["metrics"]["lexical"]["ndcg"]
                    for row in selected
                ),
            }
    result = {
        "protocol": "hotpot-pool-v1",
        "corpus": frozen["projection_sha256"],
        "index": semantic.metadata(),
        "aggregates": aggregates,
        "queries": rows,
        "build_seconds": {"lexical": lexical_seconds, "embedding": embedding_seconds},
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "packages": {
                d.metadata["Name"]: d.version
                for d in importlib.metadata.distributions()
            },
        },
        "completed": True,
    }
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(
        "RETRIEVAL_EVALUATION="
        + json.dumps(
            {
                "completed": True,
                "aggregates": aggregates,
                "index": semantic.metadata(),
                "build_seconds": result["build_seconds"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    evaluate(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
