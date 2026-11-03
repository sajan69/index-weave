"""Version 2 immutable checkpoint prefix followed by ordinary revisioned frames."""

import hashlib
import json

from .codec import (
    COMMIT_MAGIC,
    HEADER_BYTES,
    MAX_LOG_BYTES,
    MAX_PAYLOAD_BYTES,
    PREFIX,
    TRAILER_BYTES,
    _exact,
    _not_number,
    _pairs,
    encode_frame,
)
from .errors import Corruption, InvalidInput
from .model import (
    MAX_DOCUMENTS,
    MAX_LIVE_BYTES,
    MAX_OPERATIONS,
    MAX_REVISION,
    Document,
    Record,
    Snapshot,
    canonical,
    snapshot_digest,
)

MAGIC_V2 = b"IWLOG\x00\x02\n"
META_MAGIC = b"IWCP"
CHUNK_MAGIC = b"IWCR"
BASE_COMMIT = b"IWBASEOK"
MAX_META_BYTES = 16384
MAX_CHUNKS = 256


def _uint(value, maximum=MAX_REVISION, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise InvalidInput("Checkpoint integer is outside its supported range.")
    return value


def _json(payload):
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
        if depth > 32 or visits > 100000:
            raise InvalidInput("Checkpoint JSON exceeds structural limits.")
        if isinstance(item, (dict, list)):
            stack.extend(
                (child, depth + 1)
                for child in (item.values() if isinstance(item, dict) else item)
            )
    if canonical(value) != payload:
        raise InvalidInput("Checkpoint payload is not canonical.")
    return value


def _chunk_payload(records):
    return canonical({"records": records, "version": 2})


def encode_checkpoint(snapshot):
    if not isinstance(snapshot, Snapshot):
        raise InvalidInput("A storage snapshot is required.")
    _uint(snapshot.revision)
    _uint(snapshot.live_bytes, MAX_LIVE_BYTES)
    chunks, batch = [], []
    live_bytes = 0
    if len(snapshot.documents) > MAX_DOCUMENTS:
        raise InvalidInput("Checkpoint document count exceeds its limit.")
    for key, record in sorted(snapshot.documents.items()):
        if (
            not isinstance(record, Record)
            or not isinstance(record.document, Document)
            or record.document.id != key
        ):
            raise InvalidInput("Invalid checkpoint source record.")
        _uint(record.revision, snapshot.revision, 1)
        live_bytes += len(canonical(record.document.to_dict()))
        if live_bytes > MAX_LIVE_BYTES:
            raise InvalidInput("Checkpoint live bytes exceed their limit.")
        row = {"document": record.document.to_dict(), "revision": record.revision}
        if len(_chunk_payload([row])) > MAX_PAYLOAD_BYTES:
            raise InvalidInput("A checkpoint record exceeds its chunk byte budget.")
        candidate = [*batch, row]
        if (
            len(candidate) > MAX_OPERATIONS
            or len(_chunk_payload(candidate)) > MAX_PAYLOAD_BYTES
        ):
            chunks.append(_chunk_payload(batch))
            batch = [row]
        else:
            batch = candidate
    if batch:
        chunks.append(_chunk_payload(batch))
    if live_bytes != snapshot.live_bytes or len(chunks) > MAX_CHUNKS:
        raise InvalidInput("Checkpoint source accounting is inconsistent.")
    metadata = canonical(
        {
            "version": 2,
            "revision": snapshot.revision,
            "document_count": len(snapshot.documents),
            "live_bytes": live_bytes,
            "chunk_count": len(chunks),
            "records_sha256": snapshot_digest(snapshot),
        }
    )
    if len(metadata) > MAX_META_BYTES:
        raise InvalidInput("Checkpoint metadata exceeds its limit.")
    parts = [MAGIC_V2, encode_frame(metadata, snapshot.revision, magic=META_MAGIC)]
    parts.extend(
        encode_frame(payload, number, magic=CHUNK_MAGIC)
        for number, payload in enumerate(chunks, 1)
    )
    prefix = b"".join(parts)
    result = prefix + BASE_COMMIT + hashlib.sha256(prefix).digest()
    if len(result) > MAX_LOG_BYTES:
        raise InvalidInput("Checkpoint exceeds the log byte limit.")
    return result


def _frame(raw, offset, magic, maximum):
    if len(raw) - offset < HEADER_BYTES:
        raise Corruption(offset, "incomplete checkpoint frame header")
    header = raw[offset : offset + HEADER_BYTES]
    prefix = header[: PREFIX.size]
    if hashlib.sha256(prefix).digest() != header[PREFIX.size :]:
        raise Corruption(offset, "checkpoint header checksum mismatch")
    marker, length, revision = PREFIX.unpack(prefix)
    if marker != magic or not 1 <= length <= maximum or revision > MAX_REVISION:
        raise Corruption(offset, "invalid checkpoint frame fields")
    payload_end = offset + HEADER_BYTES + length
    end = payload_end + TRAILER_BYTES
    if end > len(raw):
        raise Corruption(offset, "incomplete checkpoint payload")
    if (
        hashlib.sha256(raw[offset:payload_end]).digest()
        != raw[payload_end : payload_end + 32]
        or raw[payload_end + 32 : end] != COMMIT_MAGIC
    ):
        raise Corruption(offset, "checkpoint frame checksum or marker mismatch")
    return raw[offset + HEADER_BYTES : payload_end], revision, end


def _metadata(payload, number):
    metadata = _json(payload)
    _exact(
        metadata,
        [
            "version",
            "revision",
            "document_count",
            "live_bytes",
            "chunk_count",
            "records_sha256",
        ],
    )
    if (
        type(metadata["version"]) is not int
        or metadata["version"] != 2
        or _uint(metadata["revision"]) != number
    ):
        raise InvalidInput("Invalid checkpoint metadata version or revision.")
    _uint(metadata["document_count"], MAX_DOCUMENTS)
    _uint(metadata["live_bytes"], MAX_LIVE_BYTES)
    _uint(metadata["chunk_count"], MAX_CHUNKS)
    digest = metadata["records_sha256"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise InvalidInput("Invalid checkpoint record digest.")
    return metadata


def _records(payload, records, revision, live_bytes):
    chunk = _json(payload)
    _exact(chunk, ["version", "records"])
    if (
        type(chunk["version"]) is not int
        or chunk["version"] != 2
        or not isinstance(chunk["records"], list)
        or not 1 <= len(chunk["records"]) <= MAX_OPERATIONS
    ):
        raise InvalidInput("Invalid checkpoint chunk version or count.")
    previous = next(reversed(records), "")
    for row in chunk["records"]:
        _exact(row, ["document", "revision"])
        raw = row["document"]
        _exact(raw, ["id", "title", "text", "tags"])
        if not isinstance(raw["tags"], list):
            raise InvalidInput("Checkpoint tags must be a list.")
        document = Document(raw["id"], raw["title"], raw["text"], tuple(raw["tags"]))
        number = _uint(row["revision"], revision, 1)
        if document.id <= previous:
            raise InvalidInput("Checkpoint IDs must be globally sorted and unique.")
        records[document.id] = Record(document, number)
        previous = document.id
        live_bytes += len(canonical(document.to_dict()))
        if len(records) > MAX_DOCUMENTS or live_bytes > MAX_LIVE_BYTES:
            raise InvalidInput("Checkpoint live state exceeds its limit.")
    return live_bytes


def read_checkpoint(raw):
    """A partial base is corruption, never a repairable transaction tail."""
    offset = len(MAGIC_V2)
    try:
        payload, revision, offset = _frame(raw, offset, META_MAGIC, MAX_META_BYTES)
        metadata = _metadata(payload, revision)
        records, live_bytes = {}, 0
        for number in range(1, metadata["chunk_count"] + 1):
            payload, ordinal, offset = _frame(
                raw, offset, CHUNK_MAGIC, MAX_PAYLOAD_BYTES
            )
            if ordinal != number:
                raise InvalidInput("Checkpoint chunk order is invalid.")
            live_bytes = _records(payload, records, revision, live_bytes)
        end = offset + len(BASE_COMMIT) + 32
        if (
            end > len(raw)
            or raw[offset : offset + len(BASE_COMMIT)] != BASE_COMMIT
            or raw[offset + len(BASE_COMMIT) : end]
            != hashlib.sha256(raw[:offset]).digest()
        ):
            raise Corruption(offset, "incomplete or invalid checkpoint commit")
        snapshot = Snapshot.from_records(revision, records, live_bytes)
        if (
            len(records) != metadata["document_count"]
            or live_bytes != metadata["live_bytes"]
            or snapshot_digest(snapshot) != metadata["records_sha256"]
        ):
            raise InvalidInput("Checkpoint state accounting or digest mismatch.")
        return snapshot, end
    except (
        InvalidInput,
        UnicodeError,
        TypeError,
        ValueError,
        KeyError,
        RecursionError,
    ) as error:
        raise Corruption(offset, "invalid checkpoint semantics") from error
