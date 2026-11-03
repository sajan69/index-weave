"""Deterministic HotpotQA projection and document-retrieval metrics.

This module does no downloading or inference. Inputs are the public distribution's
validation rows, not arbitrary private document collections.
"""

import hashlib
import math
from dataclasses import dataclass
from urllib.parse import quote

from .errors import InvalidInput
from .model import MAX_DOCUMENTS, MAX_LIVE_BYTES, Document, canonical, utf8

PROTOCOL = "hotpot-pool-v1"
SELECTION_PREFIX = "indexweave-hotpot-v1:"


@dataclass(frozen=True, slots=True)
class RetrievalQuestion:
    id: str
    text: str
    split: str
    relevant: frozenset[str]


@dataclass(frozen=True, slots=True)
class SourcePassage:
    document: Document
    sentences: tuple[str, ...]
    source_url: str
    sha256: str


@dataclass(frozen=True, slots=True)
class RetrievalCorpus:
    passages: tuple[SourcePassage, ...]
    questions: tuple[RetrievalQuestion, ...]
    projection_sha256: str


def _text(value, maximum, label):
    utf8(value, maximum, label)
    if not value.strip():
        raise InvalidInput(f"{label} must not be blank.")
    return value


def select_rows(rows, *, count=200, development=100):
    """Select by ID alone; alternate counts are for synthetic mechanical tests."""
    if (
        type(count) is not int
        or type(development) is not int
        or not 0 < development < count <= 200
    ):
        raise InvalidInput("Invalid retrieval split sizes.")
    if not isinstance(rows, list) or len(rows) > 10_000:
        raise InvalidInput("Expected a bounded validation row list.")
    by_id = {}
    for row in rows:
        if not isinstance(row, dict):
            raise InvalidInput("Invalid validation row.")
        identifier = _text(row.get("id"), 128, "Question ID")
        if identifier in by_id:
            raise InvalidInput("Duplicate question ID.")
        by_id[identifier] = row
    if len(by_id) < count:
        raise InvalidInput(
            "Insufficient validation questions; no replacement sampling."
        )
    ordered = sorted(
        by_id,
        key=lambda identifier: (
            hashlib.sha256((SELECTION_PREFIX + identifier).encode()).digest(),
            identifier,
        ),
    )
    return [
        (by_id[key], "development" if i < development else "holdout")
        for i, key in enumerate(ordered[:count])
    ]


def _parallel(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise InvalidInput(f"Invalid {label} schema.")
    arrays = [value[key] for key in keys]
    if any(not isinstance(array, list) for array in arrays):
        raise InvalidInput(f"Invalid {label} lists.")
    if not 1 <= len(arrays[0]) <= 32 or any(
        len(array) != len(arrays[0]) for array in arrays
    ):
        raise InvalidInput(f"Invalid {label} list lengths.")
    return zip(*arrays, strict=True)


def project_hotpot(rows, *, count=200, development=100):
    """Preserve every selected context, including distractors, or reject the input."""
    passages = {}
    questions = []
    live_bytes = 0
    for row, split in select_rows(rows, count=count, development=development):
        question = _text(row.get("question"), 2048, "Question")
        context = {}
        for title, sentences in _parallel(
            row.get("context"), ("title", "sentences"), "context"
        ):
            title = _text(title, 512, "Context title")
            if title in context:
                raise InvalidInput("Ambiguous repeated title within a question.")
            if not isinstance(sentences, list) or not 1 <= len(sentences) <= 1024:
                raise InvalidInput("Invalid source sentence list.")
            if any(not isinstance(sentence, str) for sentence in sentences):
                raise InvalidInput("Source sentences must be strings.")
            text = "".join(sentences)
            Document("validation", title, text)
            digest = hashlib.sha256((title + "\0" + text).encode()).hexdigest()
            identifier = "hp-" + digest[:60]
            document = Document(identifier, title, text)
            previous = passages.get(identifier)
            if previous is not None and previous.sha256 != digest:
                raise InvalidInput("Truncated document hash collision.")
            if previous is None:
                live_bytes += len(canonical(document.to_dict()))
                passages[identifier] = SourcePassage(
                    document,
                    tuple(sentences),
                    "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_")),
                    digest,
                )
            context[title] = (identifier, len(sentences))
        relevant = set()
        for title, sentence_index in _parallel(
            row.get("supporting_facts"), ("title", "sent_id"), "supporting facts"
        ):
            if (
                not isinstance(title, str)
                or title not in context
                or type(sentence_index) is not int
                or not 0 <= sentence_index < context[title][1]
            ):
                raise InvalidInput("Supporting fact does not map to a source sentence.")
            relevant.add(context[title][0])
        questions.append(
            RetrievalQuestion(row["id"], question, split, frozenset(relevant))
        )
    if len(passages) > MAX_DOCUMENTS or live_bytes > MAX_LIVE_BYTES:
        raise InvalidInput("Projected corpus exceeds storage bounds.")
    ordered_passages = tuple(passages[key] for key in sorted(passages))
    identity = {
        "protocol": PROTOCOL,
        "documents": [p.document.to_dict() for p in ordered_passages],
        "questions": [
            {
                "id": q.id,
                "text": q.text,
                "split": q.split,
                "relevant": sorted(q.relevant),
            }
            for q in questions
        ],
    }
    return RetrievalCorpus(
        ordered_passages,
        tuple(questions),
        hashlib.sha256(canonical(identity)).hexdigest(),
    )


def retrieval_metrics(ranked_ids, relevant, *, k=10):
    """Binary document metrics; duplicate ranks and absent qrels are errors."""
    if type(k) is not int or not 1 <= k <= 100:
        raise InvalidInput("Metric cutoff must be 1–100.")
    if not isinstance(relevant, (set, frozenset)) or not relevant:
        raise InvalidInput("Metrics require nonempty relevant-document labels.")
    if not isinstance(ranked_ids, (list, tuple)):
        raise InvalidInput("Ranking must be an ordered sequence.")
    if any(not isinstance(item, str) or not item for item in (*ranked_ids, *relevant)):
        raise InvalidInput("Metric document IDs must be nonempty strings.")
    if len(set(ranked_ids)) != len(ranked_ids):
        raise InvalidInput("Duplicate document rank.")
    found = [rank for rank, key in enumerate(ranked_ids[:k], 1) if key in relevant]
    dcg = math.fsum(1 / math.log2(rank + 1) for rank in found)
    ideal = math.fsum(
        1 / math.log2(rank + 1) for rank in range(1, min(k, len(relevant)) + 1)
    )
    return {
        "recall": len(found) / len(relevant),
        "ndcg": dcg / ideal,
        "all_support": len(found) == len(relevant),
    }


def reciprocal_rank_fusion(first, second, *, window=100):
    """Fixed equal-weight RRF, rank constant 60; stable ID tie breaking."""
    if type(window) is not int or not 1 <= window <= 100:
        raise InvalidInput("Fusion window must be 1–100.")
    scores = {}
    for ranking in (first, second):
        if not isinstance(ranking, (list, tuple)) or any(
            not isinstance(key, str) or not key for key in ranking
        ):
            raise InvalidInput("Invalid fusion ranking.")
        if len(set(ranking)) != len(ranking):
            raise InvalidInput("Duplicate document rank.")
        for rank, key in enumerate(ranking[:window], 1):
            scores[key] = scores.get(key, 0.0) + 1 / (60 + rank)
    return sorted(scores, key=lambda key: (-scores[key], key))
