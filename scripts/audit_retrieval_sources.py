"""Audit fixed public source metadata; no credentials, documents or model execution."""

import hashlib
import json
import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
DATASET = "hotpotqa/hotpot_qa"
DATA_FILE = "distractor/validation-00000-of-00001.parquet"
MODEL_FILES = [
    "modules.json",
    "config.json",
    "sentence_bert_config.json",
    "config_sentence_transformers.json",
    "1_Pooling/config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
    "model.safetensors",
    "README.md",
]


def get(url, maximum=2 * 1024 * 1024):
    # URLs are constructed only from fixed sources and validated revision hashes.
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Public metadata exceeds its byte cap")
    return data


def metadata(kind, name):
    return json.loads(get(f"https://huggingface.co/api/{kind}/{name}"))


def sha(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{40}", value):
        raise ValueError("Source revision is not a commit hash")
    return value


def tree(kind, name, revision):
    return json.loads(
        get(f"https://huggingface.co/api/{kind}/{name}/tree/{revision}?recursive=true")
    )


def selected_assets(entries, names):
    files = {entry["path"]: entry for entry in entries if entry.get("type") == "file"}
    result = {}
    for name in names:
        item = files[name]
        if not 0 < item["size"] <= 128 * 1024 * 1024:
            raise ValueError("Source asset outside byte cap")
        result[name] = {
            "bytes": item["size"],
            "git_blob": item.get("oid"),
            "lfs_sha256": item.get("lfs", {}).get("oid"),
        }
    return result


def run():
    data = metadata("datasets", DATASET)
    revision = sha(data["sha"])
    if data.get("cardData", {}).get("license") not in (
        "cc-by-sa-4.0",
        ["cc-by-sa-4.0"],
    ):
        raise ValueError("Dataset licence declaration changed")
    model = metadata("models", MODEL)
    if model.get("cardData", {}).get("license") not in ("apache-2.0", ["apache-2.0"]):
        raise ValueError("Model licence declaration changed")
    assets = selected_assets(tree("models", MODEL, MODEL_REVISION), MODEL_FILES)
    reviewed = {}
    for name in [
        "modules.json",
        "config.json",
        "sentence_bert_config.json",
        "1_Pooling/config.json",
    ]:
        raw = get(f"https://huggingface.co/{MODEL}/resolve/{MODEL_REVISION}/{name}")
        reviewed[name] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "value": json.loads(raw),
        }
    types = [entry["type"] for entry in reviewed["modules.json"]["value"]]
    if types != [
        "sentence_transformers.models.Transformer",
        "sentence_transformers.models.Pooling",
    ]:
        raise ValueError("Unreviewed model modules")
    config = reviewed["config.json"]["value"]
    if config.get("model_type") != "bert" or config.get("auto_map"):
        raise ValueError("Unreviewed model architecture or custom code")
    return {
        "schema_version": 1,
        "acquired_at": datetime.now(UTC).isoformat(),
        "dataset": {
            "id": DATASET,
            "revision": revision,
            "license": "CC-BY-SA-4.0",
            "asset": selected_assets(tree("datasets", DATASET, revision), [DATA_FILE]),
            "original_publisher": "https://hotpotqa.github.io/",
            "distribution": (
                "Hugging Face distribution; not an authenticity certificate"
            ),
        },
        "model": {
            "id": MODEL,
            "revision": MODEL_REVISION,
            "license": "Apache-2.0",
            "assets": assets,
            "reviewed_configuration": reviewed,
        },
        "completed": True,
    }


if __name__ == "__main__":
    target = Path(sys.argv[1])
    if target.exists():
        raise SystemExit("Refusing existing output")
    result = run()
    with target.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print("RETRIEVAL_AUDIT=" + json.dumps(result, sort_keys=True))
