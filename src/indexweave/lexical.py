"""Immutable positional postings and exact, bounded lexical search."""

import hashlib
import math
from bisect import bisect_left
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .analysis import Token, analyze, analyzer_metadata
from .errors import InvalidInput
from .model import Snapshot, canonical
from .query import QUERY_LANGUAGE, Clause, parse_query

SCORING = "bm25-positive-idf-v1"
MAX_OCCURRENCES = 1_000_000
MAX_TERMS = 100_000
FIELDS = ("title", "text")
K1 = 1.2
B = 0.75


def _frozen(values):
    return MappingProxyType(values.copy())


def _first_phrase(position_lists: list[tuple[int, ...]]) -> int | None:
    for start in position_lists[0]:
        found = True
        for delta, positions in enumerate(position_lists[1:], 1):
            target = start + delta
            offset = bisect_left(positions, target)
            if offset == len(positions) or positions[offset] != target:
                found = False
                break
        if found:
            return start
    return None


@dataclass(frozen=True, slots=True)
class LexicalIndex:
    """Built atomically from one storage snapshot; never refreshed in place."""

    snapshot: Snapshot
    tokens: Mapping[str, Mapping[str, tuple[Token, ...]]]
    postings: Mapping[str, Mapping[str, Mapping[str, tuple[int, ...]]]]
    tags: Mapping[str, frozenset[str]]
    lengths: Mapping[str, int]
    total_tokens: int
    content_sha256: str
    provenance: Mapping[str, str]

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot):
        if not isinstance(snapshot, Snapshot):
            raise InvalidInput("A storage Snapshot is required.")
        # Copy caller-created snapshots too; storage snapshots already freeze records.
        snapshot = Snapshot.from_records(
            snapshot.revision, dict(snapshot.documents), snapshot.live_bytes
        )
        tokens, postings, tags, lengths = {}, {}, {}, {}
        total = 0
        digest = hashlib.sha256()
        for document_id, record in sorted(snapshot.documents.items()):
            document = record.document
            digest.update(
                canonical({"document": document.to_dict(), "revision": record.revision})
            )
            digest.update(b"\n")
            fields = {}
            for field in FIELDS:
                analyzed = analyze(getattr(document, field))
                total += len(analyzed)
                if total > MAX_OCCURRENCES:
                    raise InvalidInput("Index exceeds one million token occurrences.")
                fields[field] = analyzed
                for position, token in enumerate(analyzed):
                    if token.term not in postings:
                        if len(postings) >= MAX_TERMS:
                            raise InvalidInput("Index exceeds 100000 distinct terms.")
                        postings[token.term] = {}
                    postings[token.term].setdefault(document_id, {}).setdefault(
                        field, []
                    ).append(position)
            tokens[document_id] = _frozen(fields)
            lengths[document_id] = sum(len(values) for values in fields.values())
            for tag in document.tags:
                tags.setdefault(tag, set()).add(document_id)
        immutable_postings = {
            term: _frozen(
                {
                    key: _frozen(
                        {field: tuple(values) for field, values in fields.items()}
                    )
                    for key, fields in documents.items()
                }
            )
            for term, documents in postings.items()
        }
        return cls(
            snapshot,
            _frozen(tokens),
            _frozen(immutable_postings),
            _frozen({tag: frozenset(ids) for tag, ids in tags.items()}),
            _frozen(lengths),
            total,
            digest.hexdigest(),
            _frozen(
                {
                    **analyzer_metadata(),
                    "query_language": QUERY_LANGUAGE,
                    "scoring": SCORING,
                }
            ),
        )

    def metadata(self) -> dict:
        return {
            **self.provenance,
            "snapshot_revision": self.snapshot.revision,
            "content_sha256": self.content_sha256,
            "document_count": len(self.lengths),
            "distinct_terms": len(self.postings),
            "token_occurrences": self.total_tokens,
            "k1": K1,
            "b": B,
        }

    def _candidates(self, clause: Clause) -> set[str]:
        if clause.kind == "tag":
            return set(self.tags.get(clause.terms[0], ()))
        lists = [self.postings.get(term, {}) for term in clause.terms]
        result = set(min(lists, key=len))
        for values in lists:
            result.intersection_update(values)
        return result

    def _matches(self, document_id: str, clause: Clause) -> list[dict]:
        if clause.kind == "tag":
            return []
        matches = []
        for field in FIELDS:
            positions = [
                self.postings[term][document_id].get(field, ()) for term in clause.terms
            ]
            if not all(positions):
                continue
            start = _first_phrase(positions)
            if start is not None:
                field_tokens = self.tokens[document_id][field]
                first = field_tokens[start]
                last = field_tokens[start + len(clause.terms) - 1]
                matches.append({"field": field, "start": first.start, "end": last.end})
        return matches

    def _score(
        self, document_id: str, terms: tuple[str, ...]
    ) -> tuple[float, list[dict]]:
        count = len(self.lengths)
        average = self.total_tokens / count if count else 0
        explanation = []
        for term in terms:
            documents = self.postings.get(term, {})
            tf = sum(len(values) for values in documents.get(document_id, {}).values())
            df = len(documents)
            idf = math.log1p((count - df + 0.5) / (df + 0.5))
            norm = (
                K1 * (1 - B + B * self.lengths[document_id] / average) if average else 0
            )
            contribution = idf * tf * (K1 + 1) / (tf + norm) if tf else 0
            explanation.append(
                {
                    "term": term,
                    "tf": tf,
                    "df": df,
                    "idf": idf,
                    "contribution": contribution,
                }
            )
        return math.fsum(item["contribution"] for item in explanation), explanation

    def search(self, query: str, *, limit: int = 10) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Search limit requires an integer from 1 to 100.")
        clauses = parse_query(query)
        candidates = [self._candidates(clause) for clause in clauses]
        ids = set(min(candidates, key=len))
        for candidate in candidates:
            ids.intersection_update(candidate)
        terms = tuple(
            sorted(
                {
                    term
                    for clause in clauses
                    if clause.kind != "tag"
                    for term in clause.terms
                }
            )
        )
        ranked = []
        for document_id in sorted(ids):
            matches = [self._matches(document_id, clause) for clause in clauses]
            if any(
                not values and clause.kind != "tag"
                for clause, values in zip(clauses, matches, strict=True)
            ):
                continue
            score, _ = self._score(document_id, terms)
            ranked.append((document_id, score))
        ranked.sort(key=lambda row: (-row[1], row[0]))
        hits = [
            self._hit(document_id, score, clauses, terms)
            for document_id, score in ranked[:limit]
        ]
        return {
            "index": self.metadata(),
            "query": query,
            "total_matches": len(ranked),
            "limit": limit,
            "hits": hits,
        }

    def rank_text(self, query: str, *, limit: int = 10) -> dict:
        """Free-text OR BM25; does not interpret required-clause query syntax."""
        from .semantic import text_terms

        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Search limit requires 1–100.")
        terms = text_terms(query)
        ids = set().union(*(self.postings.get(term, {}) for term in terms))
        ranked = [(key, self._score(key, terms)[0]) for key in ids]
        ranked.sort(key=lambda item: (-item[1], item[0]))
        hits = []
        for key, score in ranked[:limit]:
            # Bound passage evidence to 16 matched terms while scoring every term.
            clauses = [
                Clause("term", (term,))
                for term in terms
                if key in self.postings.get(term, {})
            ][:16]
            hits.append(self._hit(key, score, clauses, terms))
        return {
            "index": self.metadata(),
            "query": query,
            "limit": limit,
            "query_mode": "free-text-or-v1",
            "total_matches": len(ranked),
            "hits": hits,
        }

    def _hit(self, document_id, score, clauses, terms) -> dict:
        matches = [self._matches(document_id, clause) for clause in clauses]
        _, explanation = self._score(document_id, terms)
        record = self.snapshot.documents[document_id]
        evidence = [
            {"clause": index, **match}
            for index, group in enumerate(matches)
            for match in group
        ]
        first = evidence[0] if evidence else None
        field = first["field"] if first else "text"
        source = getattr(record.document, field)
        start = max(0, first["start"] - 60) if first else 0
        end = min(len(source), start + 240)
        return {
            "id": document_id,
            "title": record.document.title,
            "document_revision": record.revision,
            "tags": list(record.document.tags),
            "score": score,
            "term_scores": explanation,
            "matches": evidence,
            "passage": {
                "field": field,
                "start": start,
                "end": end,
                "text": source[start:end],
            },
        }
