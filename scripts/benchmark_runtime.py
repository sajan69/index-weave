"""Five fresh-process measurements of the frozen public paragraph workload."""

import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import subprocess
import sys
import time
from pathlib import Path

from indexweave import Document, LexicalIndex, Put, Writer, read_snapshot
from indexweave.benchmark_stats import summarize
from indexweave.embedding import MiniLMEncoder
from indexweave.model import canonical, snapshot_digest
from indexweave.segments import create_index, load_index
from indexweave.semantic import SemanticIndex, hybrid_search


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")


def workload(directory):
    data = json.loads((directory / "corpus.json").read_text())
    frozen = json.loads(
        (
            Path(__file__).resolve().parents[1] / "examples/retrieval-data.json"
        ).read_text()
    )["result"]
    documents = [
        Document(**{**row["document"], "tags": tuple(row["document"]["tags"])})
        for row in data["passages"]
    ]
    identity = {
        "protocol": "hotpot-pool-v1",
        "documents": [document.to_dict() for document in documents],
        "questions": data["questions"],
    }
    if (
        data["provenance"] != frozen
        or hashlib.sha256(canonical(identity)).hexdigest()
        != frozen["projection_sha256"]
        or [{"id": q["id"], "split": q["split"]} for q in data["questions"]]
        != frozen["question_ids"]
        or len(documents) != 1984
    ):
        raise ValueError("Workload differs from frozen projection")
    questions = [q for q in data["questions"] if q["split"] == "development"]
    if len(questions) != 100:
        raise ValueError("All 100 development queries are required")
    return documents, questions, frozen["projection_sha256"]


def peak():
    if sys.platform != "linux":
        raise ValueError("Peak RSS units are defined for Linux only")
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def worker(data_path, model_path, root, repeat):
    row = {"repeat": repeat, "completed": False, "phase_ns": {}, "query_ns": {}}
    phase = "validate_workload"
    try:
        documents, questions, projection = workload(data_path)
        row["query_ids"] = [q["id"] for q in questions]
        row["peak_rss_bytes"] = {"after_core_imports_and_workload": peak()}

        def timed(name, action):
            nonlocal phase
            phase = name
            before = time.perf_counter_ns()
            result = action()
            row["phase_ns"][name] = time.perf_counter_ns() - before
            return result

        source = root / "source"

        def ingest():
            with Writer.create(source) as writer:
                for i in range(0, len(documents), 100):
                    writer.write(
                        [Put(document) for document in documents[i : i + 100]],
                        expected_revision=writer.snapshot().revision,
                    )

        timed("import_with_fsync", ingest)
        snapshot = timed("source_replay", lambda: read_snapshot(source))
        lexical = timed("lexical_build", lambda: LexicalIndex.from_snapshot(snapshot))
        index = root / "index"
        timed("index_create_including_build", lambda: create_index(index, snapshot))
        loaded = timed(
            "index_load_validate", lambda: load_index(index, expected_snapshot=snapshot)
        )
        if loaded.content_sha256 != lexical.content_sha256:
            raise ValueError("Loaded index identity differs")
        del loaded
        encoder = timed("verified_model_load", lambda: MiniLMEncoder(model_path))
        row["peak_rss_bytes"]["after_model_load"] = peak()
        semantic = timed(
            "semantic_build", lambda: SemanticIndex.from_snapshot(snapshot, encoder)
        )
        searches = {
            "text": lambda q: lexical.rank_text(q, limit=100),
            "semantic": lambda q: semantic.search(
                q, expected_snapshot=snapshot, limit=100
            ),
            "hybrid": lambda q: hybrid_search(
                lexical, semantic, q, expected_snapshot=snapshot, limit=100
            ),
        }
        for mode, search in searches.items():
            phase = "queries_" + mode
            search(questions[0]["text"])
            durations = []
            for question in questions:
                before = time.perf_counter_ns()
                search(question["text"])
                durations.append(time.perf_counter_ns() - before)
            row["query_ns"][mode] = durations
        row["peak_rss_bytes"]["final"] = peak()
        row["identity"] = {
            "protocol": "bounded-runtime-v1",
            "projection_sha256": projection,
            "source_sha256": snapshot_digest(snapshot),
            "model": semantic.metadata()["model"],
        }
        row["bytes"] = {
            "live_canonical_documents": sum(
                len(canonical(d.to_dict())) for d in documents
            ),
            "source_log": (source / "documents.iwl").stat().st_size,
            "index_files": {p.name: p.stat().st_size for p in index.iterdir()},
            "vector_payload": semantic.vectors.nbytes,
        }
        row["documents"] = len(snapshot.documents)
        row["semantic_metadata"] = semantic.metadata()
        row["environment"] = {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count(),
            "packages": {
                d.metadata["Name"]: d.version
                for d in importlib.metadata.distributions()
            },
            "threads": {
                key: os.environ.get(key)
                for key in (
                    "OMP_NUM_THREADS",
                    "MKL_NUM_THREADS",
                    "OPENBLAS_NUM_THREADS",
                )
            },
        }
        row["completed"] = True
    except Exception as error:
        row["failure"] = {"phase": phase, "type": type(error).__name__}
    save(root / "worker.json", row)


def coordinate(data, model, output):
    output.mkdir(mode=0o700)
    workers = []
    for repeat in range(5):
        root = output / str(repeat)
        root.mkdir(mode=0o700)
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "worker",
                    str(data),
                    str(model),
                    str(root),
                    str(repeat),
                ],
                timeout=900,
                check=False,
            )
            row = json.loads((root / "worker.json").read_text())
            if result.returncode:
                row["completed"] = False
        except (subprocess.TimeoutExpired, OSError, ValueError):
            row = {
                "repeat": repeat,
                "completed": False,
                "failure": {"phase": "worker_process"},
            }
        workers.append(row)
        print(
            "BENCHMARK_REPEAT="
            + json.dumps({"repeat": repeat, "completed": row["completed"]}),
            flush=True,
        )
    save(output / "workers.json", workers)
    summary = summarize(workers)
    save(output / "summary.json", summary)
    print("BENCHMARK_SUMMARY=" + json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    if sys.argv[1] == "worker":
        worker(
            Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), int(sys.argv[5])
        )
    else:
        coordinate(
            Path(sys.argv[1]).resolve(),
            Path(sys.argv[2]).resolve(),
            Path(sys.argv[3]).resolve(),
        )
