"""Bounded immutable exact retrieval; inference is supplied by a local encoder."""

from dataclasses import dataclass
from types import MappingProxyType

from .analysis import analyze
from .errors import InvalidInput, StaleIndex
from .model import MAX_DOCUMENTS, MAX_LIVE_BYTES, Snapshot, snapshot_digest, utf8
from .retrieval_data import reciprocal_rank_fusion

CONTRACT = "semantic-exact-v1"
CHUNKING = "wordpiece-192-stride160-title32-v1"
DIMENSIONS = 384
MAX_CHUNKS = 20_000
MAX_DOCUMENT_CHUNKS = 64


def text_terms(query):
    utf8(query, 2048, "Search text")
    tokens = analyze(query)
    if not 1 <= len(tokens) <= 128:
        raise InvalidInput("Search text requires 1–128 lexical tokens.")
    return tuple(sorted({token.term for token in tokens}))


def _numpy():
    import numpy as np

    return np


def normalized_matrix(values, count):
    np = _numpy()
    matrix = np.asarray(values)
    if matrix.shape != (count, DIMENSIONS) or matrix.dtype.kind != "f":
        raise InvalidInput("Encoder must return a floating 384-dimensional matrix.")
    matrix = np.array(matrix, dtype=np.float32, copy=True)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if (
        not np.isfinite(matrix).all()
        or not np.isfinite(norms).all()
        or (norms == 0).any()
    ):
        raise InvalidInput("Encoder returned a nonfinite or zero vector.")
    matrix /= norms
    # Immutable bytes backing prevents callers re-enabling ndarray writes.
    return np.frombuffer(matrix.tobytes(), dtype=np.float32).reshape(count, DIMENSIONS)


@dataclass(frozen=True, slots=True)
class Passage:
    document_id: str
    title_start: int
    title_end: int
    start: int
    end: int
    encoded_text: str


def document_passages(document, encoder):
    title_offsets = encoder.offsets(document.title)
    title_start = title_offsets[0][0] if title_offsets else 0
    title_end = (
        title_offsets[min(32, len(title_offsets)) - 1][1] if title_offsets else 0
    )
    prefix = document.title[title_start:title_end]
    body = encoder.offsets(document.text)
    starts = list(range(0, len(body), 160)) if body else [0]
    # Stop at the first complete window; do not add a redundant suffix window.
    starts = [
        position
        for position in starts
        if position == 0 or position - 160 + 192 < len(body)
    ]
    if len(starts) > MAX_DOCUMENT_CHUNKS:
        raise InvalidInput("Document exceeds 64 semantic chunks.")
    result = []
    for position in starts:
        start = body[position][0] if body else 0
        end = body[min(position + 192, len(body)) - 1][1] if body else 0
        text = prefix + "\n" + document.text[start:end]
        if encoder.token_count(text) > 256:
            raise InvalidInput("Passage exceeds the model token limit; no truncation.")
        result.append(Passage(document.id, title_start, title_end, start, end, text))
    return result


@dataclass(frozen=True, slots=True)
class SemanticIndex:
    snapshot: Snapshot
    content_sha256: str
    encoder: object
    passages: tuple[Passage, ...]
    vectors: object
    provenance: object

    @classmethod
    def from_snapshot(cls, snapshot, encoder):
        if not isinstance(snapshot, Snapshot):
            raise InvalidInput("A source Snapshot is required.")
        if (
            len(snapshot.documents) > MAX_DOCUMENTS
            or snapshot.live_bytes > MAX_LIVE_BYTES
        ):
            raise InvalidInput("Snapshot exceeds document-store bounds.")
        snapshot = Snapshot.from_records(
            snapshot.revision, dict(snapshot.documents), snapshot.live_bytes
        )
        passages = []
        for _, record in sorted(snapshot.documents.items()):
            passages.extend(document_passages(record.document, encoder))
            if len(passages) > MAX_CHUNKS:
                raise InvalidInput("Snapshot exceeds 20000 semantic chunks.")
        # Complete all chunk validation before invoking expensive document inference.
        values = (
            encoder.encode_documents([p.encoded_text for p in passages])
            if passages
            else _numpy().empty((0, DIMENSIONS), dtype="float32")
        )
        vectors = normalized_matrix(values, len(passages))
        return cls(
            snapshot,
            snapshot_digest(snapshot),
            encoder,
            tuple(passages),
            vectors,
            MappingProxyType(dict(encoder.identity)),
        )

    def require_snapshot(self, expected_snapshot):
        if (
            expected_snapshot.revision != self.snapshot.revision
            or snapshot_digest(expected_snapshot) != self.content_sha256
        ):
            raise StaleIndex(
                "Semantic index does not match the captured source snapshot."
            )

    def metadata(self):
        return {
            "contract": CONTRACT,
            "chunking": CHUNKING,
            "snapshot_revision": self.snapshot.revision,
            "content_sha256": self.content_sha256,
            "document_count": len(self.snapshot.documents),
            "passage_count": len(self.passages),
            "dimensions": DIMENSIONS,
            "model": dict(self.provenance),
        }

    def search(self, query, *, expected_snapshot, limit=10):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Search limit requires 1–100.")
        self.require_snapshot(expected_snapshot)
        text_terms(query)
        if self.encoder.token_count(query) > 256:
            raise InvalidInput("Query exceeds the model token limit; no truncation.")
        query_vector = normalized_matrix(self.encoder.encode_queries([query]), 1)[0]
        scores = self.vectors @ query_vector
        best = {}
        for i, passage in enumerate(self.passages):
            value = float(scores[i])
            current = best.get(passage.document_id)
            if current is None or value > current[0]:
                best[passage.document_id] = (value, i)
        ordered = sorted(best, key=lambda key: (-best[key][0], key))
        hits = []
        for key in ordered[:limit]:
            score, i = best[key]
            passage = self.passages[i]
            record = self.snapshot.documents[key]
            hits.append(
                {
                    "id": key,
                    "title": record.document.title,
                    "document_revision": record.revision,
                    "score": score,
                    "passage": {
                        "field": "text",
                        "start": passage.start,
                        "end": passage.end,
                        "text": record.document.text[passage.start : passage.end],
                    },
                    "title_span": {
                        "start": passage.title_start,
                        "end": passage.title_end,
                    },
                }
            )
        return {
            "index": self.metadata(),
            "query": query,
            "limit": limit,
            "total_matches": len(best),
            "hits": hits,
        }


def hybrid_search(lexical, semantic, query, *, expected_snapshot, limit=10):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise InvalidInput("Search limit requires 1–100.")
    semantic.require_snapshot(expected_snapshot)
    if (
        lexical.snapshot.revision != semantic.snapshot.revision
        or lexical.content_sha256 != semantic.content_sha256
    ):
        raise StaleIndex("Lexical and semantic source snapshots differ.")
    first = lexical.rank_text(query, limit=100)
    second = semantic.search(query, expected_snapshot=expected_snapshot, limit=100)
    first_ids = [hit["id"] for hit in first["hits"]]
    second_ids = [hit["id"] for hit in second["hits"]]
    ranks = [
        {key: i for i, key in enumerate(ids, 1)} for ids in (first_ids, second_ids)
    ]
    lookup = {hit["id"]: hit for hit in [*first["hits"], *second["hits"]]}
    ordered = reciprocal_rank_fusion(first_ids, second_ids)
    hits = []
    for key in ordered[:limit]:
        hit = {
            **lookup[key],
            "score": sum(1 / (60 + rank[key]) for rank in ranks if key in rank),
            "lexical_rank": ranks[0].get(key),
            "semantic_rank": ranks[1].get(key),
        }
        hits.append(hit)
    return {
        "index": semantic.metadata(),
        "query": query,
        "limit": limit,
        "fusion": "equal-rrf-window100-k60-v1",
        "total_matches": len(ordered),
        "hits": hits,
    }
