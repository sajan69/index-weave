"""Explicit public-model acquisition and strictly local MiniLM inference."""

import hashlib
import importlib.metadata
import importlib.resources
import json
import os
import stat
import urllib.request
from pathlib import Path

from .errors import InvalidInput
from .model import canonical

MANIFEST = "MODEL-MANIFEST.json"


def pins():
    return json.loads(
        importlib.resources.files("indexweave")
        .joinpath("minilm-assets.json")
        .read_text()
    )


def _checked_bytes(raw, asset):
    if len(raw) != asset["bytes"] or len(raw) > 128 * 1024 * 1024:
        raise InvalidInput("Model asset size differs from its pinned record.")
    if asset["lfs_sha256"]:
        actual = hashlib.sha256(raw).hexdigest()
        expected = asset["lfs_sha256"]
    else:
        actual = hashlib.sha1(
            b"blob " + str(len(raw)).encode() + b"\0" + raw
        ).hexdigest()
        expected = asset["git_blob"]
    if actual != expected:
        raise InvalidInput("Model asset differs from its pinned digest.")
    return hashlib.sha256(raw).hexdigest()


def _review(root):
    modules = json.loads((root / "modules.json").read_text())
    expected = [
        (0, "0", "", "Transformer"),
        (1, "1", "1_Pooling", "Pooling"),
        (2, "2", "2_Normalize", "Normalize"),
    ]
    if modules != [
        {
            "idx": i,
            "name": name,
            "path": path,
            "type": "sentence_transformers.models." + kind,
        }
        for i, name, path, kind in expected
    ]:
        raise InvalidInput("Unreviewed model module configuration.")
    config = json.loads((root / "config.json").read_text())
    sentence = json.loads((root / "config_sentence_transformers.json").read_text())
    tokenizer = json.loads((root / "tokenizer_config.json").read_text())
    bert = json.loads((root / "sentence_bert_config.json").read_text())
    pooling = json.loads((root / "1_Pooling/config.json").read_text())
    if (
        config.get("model_type") != "bert"
        or config.get("auto_map")
        or sentence.get("prompts")
        or sentence.get("default_prompt_name")
        or tokenizer.get("auto_map")
        or tokenizer.get("tokenizer_class")
        not in (None, "BertTokenizer", "BertTokenizerFast")
        or bert.get("max_seq_length") != 256
        or pooling.get("word_embedding_dimension") != 384
        or pooling.get("pooling_mode_mean_tokens") is not True
    ):
        raise InvalidInput("Unreviewed model/tokenizer configuration.")


def acquire_model(path):
    """The only network operation here: download exactly the fixed public assets."""
    root = Path(path)
    if root.exists() or root.is_symlink():
        raise FileExistsError("Refusing existing model destination")
    record = pins()
    root.mkdir(mode=0o700)
    hashes = {}
    for name, asset in record["assets"].items():
        url = (
            f"https://huggingface.co/{record['id']}/resolve/{record['revision']}/{name}"
        )
        with urllib.request.urlopen(url, timeout=90) as response:
            raw = response.read(asset["bytes"] + 1)
        hashes[name] = _checked_bytes(raw, asset)
        target = root / name
        target.parent.mkdir(mode=0o700, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
    _review(root)
    value = {"id": record["id"], "revision": record["revision"], "sha256": hashes}
    descriptor = os.open(root / MANIFEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical(value))
    return verify_model(root)


def verify_model(path):
    root = Path(path)
    record = pins()
    expected_files = {*record["assets"], MANIFEST}
    found = set()
    for entry in [root, *root.rglob("*")]:
        mode = entry.lstat()
        directory = stat.S_ISDIR(mode.st_mode)
        if (
            mode.st_uid != os.getuid()
            or stat.S_IMODE(mode.st_mode) != (0o700 if directory else 0o600)
            or not (directory or stat.S_ISREG(mode.st_mode))
            or (not directory and mode.st_nlink != 1)
        ):
            raise InvalidInput("Model cache requires private owned regular files.")
        if directory:
            if entry != root and entry.relative_to(root).as_posix() != "1_Pooling":
                raise InvalidInput("Unexpected model directory.")
        else:
            found.add(entry.relative_to(root).as_posix())
    if found != expected_files:
        raise InvalidInput("Model cache contains unexpected or missing files.")
    hashes = {}
    for name, asset in record["assets"].items():
        with (root / name).open("rb") as stream:
            raw = stream.read(asset["bytes"] + 1)
        hashes[name] = _checked_bytes(raw, asset)
    with (root / MANIFEST).open("rb") as stream:
        raw = stream.read(16 * 1024 + 1)
    if len(raw) > 16 * 1024 or json.loads(raw) != {
        "id": record["id"],
        "revision": record["revision"],
        "sha256": hashes,
    }:
        raise InvalidInput("Model manifest differs from verified assets.")
    _review(root)
    return {
        "id": record["id"],
        "revision": record["revision"],
        "assets_sha256": hashlib.sha256(canonical(hashes)).hexdigest(),
    }


class MiniLMEncoder:
    """Inference never downloads and never loads arbitrary repository Python."""

    def __init__(self, path):
        identity = verify_model(path)
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise InvalidInput(
                "Install the optional semantic requirements first."
            ) from error

        torch.set_num_threads(2)
        self.model = SentenceTransformer(
            str(Path(path).resolve()),
            device="cpu",
            local_files_only=True,
            trust_remote_code=False,
            token=False,
            prompts={"query": "", "document": ""},
            model_kwargs={"use_safetensors": True},
        )
        self.model.eval()
        if (
            not self.model.tokenizer.is_fast
            or self.model.get_sentence_embedding_dimension() != 384
        ):
            raise InvalidInput("Model requires a fast tokenizer and 384 dimensions.")
        self.identity = {
            **identity,
            **{
                name: importlib.metadata.version(name)
                for name in (
                    "sentence-transformers",
                    "transformers",
                    "torch",
                    "numpy",
                    "tokenizers",
                    "safetensors",
                )
            },
        }

    def offsets(self, text):
        values = self.model.tokenizer(
            text,
            add_special_tokens=False,
            truncation=False,
            return_offsets_mapping=True,
        )["offset_mapping"]
        if any(not 0 <= start < end <= len(text) for start, end in values):
            raise InvalidInput("Tokenizer returned invalid original-source offsets.")
        return tuple(values)

    def token_count(self, text):
        return len(
            self.model.tokenizer(text, add_special_tokens=True, truncation=False)[
                "input_ids"
            ]
        )

    def _encode(self, texts, method):
        if any(self.token_count(text) > 256 for text in texts):
            raise InvalidInput("Model input exceeds its token cap; no truncation.")
        return method(
            texts,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
            precision="float32",
        )

    def encode_documents(self, texts):
        return self._encode(texts, self.model.encode_document)

    def encode_queries(self, texts):
        return self._encode(texts, self.model.encode_query)
