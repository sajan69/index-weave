"""Acquire the frozen public benchmark into a NEW directory; no model execution."""

import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from indexweave.retrieval_data import PROTOCOL, project_hotpot

ROOT = Path(__file__).resolve().parents[1]
DATASET = "hotpotqa/hotpot_qa"
DATA_FILE = "distractor/validation-00000-of-00001.parquet"
MAX_DOWNLOAD = 64 * 1024 * 1024
ATTRIBUTION = {
    "license": "CC-BY-SA-4.0",
    "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
    "source": "https://hotpotqa.github.io/",
    "authors": (
        "Zhilin Yang, Peng Qi, Saizheng Zhang, Yoshua Bengio, William W. Cohen, "
        "Ruslan Salakhutdinov and Christopher D. Manning (HotpotQA, 2018); "
        "Wikipedia contributors (source passages)"
    ),
    "changes": (
        "Deterministic 200-question subset; paragraphs joined from original "
        "sentences without inserted characters; title/text deduplication; "
        "supporting sentence annotations projected to binary document qrels."
    ),
}


def frozen_source(pins):
    source = pins["result"]["dataset"]
    if source["id"] != DATASET or source["license"] != "CC-BY-SA-4.0":
        raise ValueError("Unexpected dataset or licence")
    revision = source["revision"]
    asset = source["asset"][DATA_FILE]
    if not re.fullmatch("[0-9a-f]{40}", revision):
        raise ValueError("Dataset revision must be frozen")
    if (
        type(asset["bytes"]) is not int
        or not 0 < asset["bytes"] <= MAX_DOWNLOAD
        or not isinstance(asset["lfs_sha256"], str)
        or not re.fullmatch("[0-9a-f]{64}", asset["lfs_sha256"])
    ):
        raise ValueError("Dataset needs a bounded SHA-256-pinned asset")
    url = f"https://huggingface.co/datasets/{DATASET}/resolve/{revision}/{DATA_FILE}"
    return url, asset


def acquire(pins):
    url, asset = frozen_source(pins)
    with urllib.request.urlopen(url, timeout=90) as response:
        raw = response.read(MAX_DOWNLOAD + 1)
    if (
        len(raw) != asset["bytes"]
        or hashlib.sha256(raw).hexdigest() != asset["lfs_sha256"]
    ):
        raise ValueError("Downloaded dataset differs from the frozen asset")
    return raw


def project_parquet(raw):
    import pyarrow as pa
    import pyarrow.parquet as pq

    source = pq.ParquetFile(pa.BufferReader(raw))
    metadata = source.metadata
    if not 200 <= metadata.num_rows <= 10_000 or metadata.num_columns > 32:
        raise ValueError("Parquet table outside row/column bounds")
    uncompressed = sum(
        metadata.row_group(group).column(column).total_uncompressed_size
        for group in range(metadata.num_row_groups)
        for column in range(metadata.num_columns)
    )
    if not 0 < uncompressed <= 128 * 1024 * 1024:
        raise ValueError("Parquet table exceeds declared decompressed-byte cap")
    columns = ["id", "question", "context", "supporting_facts"]
    rows = []
    for batch in source.iter_batches(
        batch_size=128, columns=columns, use_threads=False
    ):
        rows.extend(batch.to_pylist())
    if len(rows) != metadata.num_rows:
        raise ValueError("Incomplete validation split")
    return project_hotpot(rows), metadata.num_rows, pa.__version__


def prepare(target):
    # Existing paths are refused, including symlinks; no user collection is opened.
    if target.exists() or target.is_symlink():
        raise FileExistsError("Refusing existing data destination")
    pins = json.loads((ROOT / "examples/retrieval-sources.json").read_text())
    raw = acquire(pins)
    corpus, rows, arrow_version = project_parquet(raw)
    result = {
        "protocol": PROTOCOL,
        "source_revision": pins["result"]["dataset"]["revision"],
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_bytes": len(raw),
        "validation_rows": rows,
        "documents": len(corpus.passages),
        "development_questions": sum(
            q.split == "development" for q in corpus.questions
        ),
        "holdout_questions": sum(q.split == "holdout" for q in corpus.questions),
        "projection_sha256": corpus.projection_sha256,
        "pyarrow_version": arrow_version,
        "question_ids": [{"id": q.id, "split": q.split} for q in corpus.questions],
        "completed": True,
    }
    payload = {
        "attribution": ATTRIBUTION,
        "provenance": result,
        "passages": [
            {
                "document": p.document.to_dict(),
                "sentences": p.sentences,
                "source_url": p.source_url,
                "sha256": p.sha256,
            }
            for p in corpus.passages
        ],
        "questions": [
            {
                "id": q.id,
                "text": q.text,
                "split": q.split,
                "relevant": sorted(q.relevant),
            }
            for q in corpus.questions
        ],
    }
    # Input acquisition/validation completes before creating the new destination.
    # Write failure can leave a partial new directory; never reset it on retry.
    target.mkdir(mode=0o700)
    for name, value in (("corpus.json", payload), ("summary.json", result)):
        descriptor = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
    summary = {key: value for key, value in result.items() if key != "question_ids"}
    print("RETRIEVAL_DATA=" + json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    prepare(Path(sys.argv[1]))
