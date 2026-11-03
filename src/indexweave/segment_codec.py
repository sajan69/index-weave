"""Canonical, checksummed derived-index records; no filesystem operations."""

import hashlib
import json
import re
import unicodedata

from .analysis import _WORD, MAX_FIELD_TOKENS, MAX_TERM_BYTES, Token, analyzer_metadata
from .codec import _exact, _not_number, _pairs
from .errors import InvalidIndex, InvalidInput
from .lexical import FIELDS, SCORING, LexicalIndex, _frozen
from .model import (
    MAX_DOCUMENTS,
    MAX_LIVE_BYTES,
    MAX_REVISION,
    Document,
    Record,
    Snapshot,
    canonical,
    valid_id,
)
from .query import QUERY_LANGUAGE

SEGMENT_MAGIC = b"IWSEG1\n"
MANIFEST_MAGIC = b"IWMAN1\n"
MAX_SEGMENT_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 16384
MAX_SEGMENTS = 16
SHA_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def require(condition):
    if not condition:
        raise InvalidIndex("Derived index structure or integrity check failed.")


def integer(value, minimum=0, maximum=MAX_REVISION):
    require(type(value) is int and minimum <= value <= maximum)
    return value


def digest(snapshot):
    result = hashlib.sha256()
    for _, record in sorted(snapshot.documents.items()):
        result.update(
            canonical(
                {"document": record.document.to_dict(), "revision": record.revision}
            )
        )
        result.update(b"\n")
    return result.hexdigest()


def provenance():
    return {**analyzer_metadata(), "query_language": QUERY_LANGUAGE, "scoring": SCORING}


def encode(value, magic, maximum):
    payload = magic + canonical(value)
    data = payload + hashlib.sha256(payload).digest()
    if len(data) > maximum:
        raise InvalidInput("Derived index file exceeds its byte budget.")
    return data


def decode(data, magic, maximum):
    try:
        require(len(magic) + 34 <= len(data) <= maximum and data.startswith(magic))
        require(hashlib.sha256(data[:-32]).digest() == data[-32:])
        payload = data[len(magic) : -32]
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_float=_not_number,
            parse_constant=_not_number,
        )
        stack, visits = [(value, 0)], 0
        while stack:
            item, depth = stack.pop()
            visits += 1
            require(depth <= 32 and visits <= 4_000_000)
            if isinstance(item, (dict, list)):
                stack.extend(
                    (child, depth + 1)
                    for child in (item.values() if isinstance(item, dict) else item)
                )
        require(canonical(value) == payload)
        return value
    except (ValueError, TypeError, RecursionError, UnicodeError, InvalidInput) as error:
        raise InvalidIndex("Derived index JSON or checksum is invalid.") from error


def exact(value, fields):
    try:
        _exact(value, fields)
    except InvalidInput as error:
        raise InvalidIndex("Unexpected derived index fields.") from error


def parse_manifest(data):
    value = decode(data, MANIFEST_MAGIC, MAX_MANIFEST_BYTES)
    exact(
        value,
        [
            "version",
            "generation",
            "source_revision",
            "source_sha256",
            "provenance",
            "segments",
        ],
    )
    require(type(value["version"]) is int and value["version"] == 1)
    integer(value["generation"], 1)
    integer(value["source_revision"])
    require(
        isinstance(value["source_sha256"], str)
        and SHA_PATTERN.fullmatch(value["source_sha256"])
    )
    require(value["provenance"] == provenance())
    hashes = value["segments"]
    require(isinstance(hashes, list) and 1 <= len(hashes) <= MAX_SEGMENTS)
    require(
        all(isinstance(item, str) and SHA_PATTERN.fullmatch(item) for item in hashes)
    )
    require(len(set(hashes)) == len(hashes))
    return value


def token_rows(tokens):
    return [[token.term, token.start, token.end] for token in tokens]


def restore_tokens(text, rows):
    require(isinstance(rows, list) and len(rows) <= MAX_FIELD_TOKENS)
    tokens, end = [], 0
    for row in rows:
        require(isinstance(row, list) and len(row) == 3)
        term, start, stop = row
        integer(start, end, len(text))
        integer(stop, start + 1, len(text))
        require(
            isinstance(term, str) and 1 <= len(term.encode("utf-8")) <= MAX_TERM_BYTES
        )
        raw = text[start:stop]
        require(
            _WORD.search(text[end:start]) is None and _WORD.fullmatch(raw) is not None
        )
        # A token must consume every continuation mark, number and letter.
        require(stop == len(text) or _WORD.fullmatch("a" + text[stop]) is None)
        require(
            term
            == unicodedata.normalize(
                "NFC", unicodedata.normalize("NFC", raw).casefold()
            )
        )
        tokens.append(Token(term, start, stop))
        end = stop
    require(_WORD.search(text[end:]) is None)
    return tuple(tokens)


def segment_value(index, previous=None):
    old = previous.snapshot.documents if previous else {}
    changed = {
        key
        for key, record in index.snapshot.documents.items()
        if old.get(key) != record
    }
    deleted = sorted(set(old) - set(index.snapshot.documents))
    puts = [
        {
            "document": index.snapshot.documents[key].document.to_dict(),
            "revision": index.snapshot.documents[key].revision,
            "tokens": {field: token_rows(index.tokens[key][field]) for field in FIELDS},
        }
        for key in sorted(changed)
    ]
    rows = [
        [term, key, field, list(positions)]
        for term, documents in sorted(index.postings.items())
        for key, fields in sorted(documents.items())
        if key in changed
        for field, positions in sorted(fields.items())
    ]
    return {
        "version": 1,
        "kind": "delta" if previous else "base",
        "from_revision": previous.snapshot.revision if previous else 0,
        "to_revision": index.snapshot.revision,
        "provenance": dict(index.provenance),
        "puts": puts,
        "deletes": deleted,
        "postings": rows,
    }


def parse_segment(data, expected_hash, previous_revision, *, base):
    require(hashlib.sha256(data).hexdigest() == expected_hash)
    value = decode(data, SEGMENT_MAGIC, MAX_SEGMENT_BYTES)
    exact(
        value,
        [
            "version",
            "kind",
            "from_revision",
            "to_revision",
            "provenance",
            "puts",
            "deletes",
            "postings",
        ],
    )
    require(type(value["version"]) is int and value["version"] == 1)
    require(value["kind"] == ("base" if base else "delta"))
    require(integer(value["from_revision"]) == previous_revision)
    integer(value["to_revision"], 0 if base else previous_revision + 1)
    require(value["provenance"] == provenance())
    require(isinstance(value["puts"], list) and len(value["puts"]) <= MAX_DOCUMENTS)
    require(
        isinstance(value["deletes"], list) and len(value["deletes"]) <= MAX_DOCUMENTS
    )
    try:
        records, tokens, coverage = _put_records(
            value["puts"], 1 if base else previous_revision + 1, value["to_revision"]
        )
        deletes = [valid_id(key) for key in value["deletes"]]
        require(
            deletes == sorted(set(deletes)) and not set(deletes).intersection(records)
        )
        require(not base or not deletes)
        postings = _posting_rows(value["postings"], coverage)
    except (InvalidInput, TypeError, ValueError, UnicodeError) as error:
        raise InvalidIndex("Invalid derived index document or posting.") from error
    return value["to_revision"], records, tokens, postings, deletes


def _put_records(puts, minimum_revision, maximum_revision):
    records, tokens, coverage = {}, {}, {}
    for put in puts:
        exact(put, ["document", "revision", "tokens"])
        exact(put["document"], ["id", "title", "text", "tags"])
        raw = put["document"]
        require(isinstance(raw["tags"], list))
        document = Document(raw["id"], raw["title"], raw["text"], tuple(raw["tags"]))
        require(document.id not in records)
        revision = integer(
            put["revision"],
            minimum_revision,
            maximum_revision,
        )
        exact(put["tokens"], FIELDS)
        fields = {
            field: restore_tokens(getattr(document, field), put["tokens"][field])
            for field in FIELDS
        }
        records[document.id] = Record(document, revision)
        tokens[document.id] = _frozen(fields)
        for field, values in fields.items():
            for position, token in enumerate(values):
                coverage.setdefault((token.term, document.id, field), []).append(
                    position
                )
    require(list(records) == sorted(records))
    return records, tokens, coverage


def _posting_rows(rows, coverage):
    require(isinstance(rows, list) and len(rows) == len(coverage))
    postings, previous_key = {}, None
    for row in rows:
        require(isinstance(row, list) and len(row) == 4)
        term, key, field, positions = row
        require(all(isinstance(item, str) for item in [term, key, field]))
        position_key = (term, key, field)
        require(previous_key is None or previous_key < position_key)
        require(isinstance(positions, list) and all(type(p) is int for p in positions))
        require(coverage.get(position_key) == positions)
        postings.setdefault(term, {}).setdefault(key, {})[field] = tuple(positions)
        previous_key = position_key
    return postings


def materialize(revision, records, tokens, postings):
    from .lexical import MAX_OCCURRENCES, MAX_TERMS

    live_bytes = sum(
        len(canonical(record.document.to_dict())) for record in records.values()
    )
    require(len(records) <= MAX_DOCUMENTS and live_bytes <= MAX_LIVE_BYTES)
    lengths = {
        key: sum(len(rows) for rows in fields.values())
        for key, fields in tokens.items()
    }
    total = sum(lengths.values())
    require(total <= MAX_OCCURRENCES and len(postings) <= MAX_TERMS)
    snapshot = Snapshot.from_records(revision, records, live_bytes)
    tags = {}
    for key, record in records.items():
        for tag in record.document.tags:
            tags.setdefault(tag, set()).add(key)
    frozen_postings = {
        term: _frozen({key: _frozen(fields) for key, fields in documents.items()})
        for term, documents in postings.items()
    }
    return LexicalIndex(
        snapshot,
        _frozen(tokens),
        _frozen(frozen_postings),
        _frozen({tag: frozenset(ids) for tag, ids in tags.items()}),
        _frozen(lengths),
        total,
        digest(snapshot),
        _frozen(provenance()),
    )
