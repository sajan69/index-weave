"""Version 1 log framing; no filesystem operations occur in this module."""

import hashlib
import json
import struct
from dataclasses import dataclass

from .errors import Corruption, InvalidInput, MissingDocument
from .model import (
    MAX_DOCUMENTS,
    MAX_LIVE_BYTES,
    MAX_OPERATIONS,
    MAX_REVISION,
    Delete,
    Document,
    Put,
    Record,
    Snapshot,
    canonical,
)

MAGIC = b"IWLOG\x00\x01\n"
FRAME_MAGIC = b"IWFR"
COMMIT_MAGIC = b"IWCOMMIT"
PREFIX = struct.Struct(">4sIQ")
HEADER_BYTES = PREFIX.size + 32
TRAILER_BYTES = 32 + len(COMMIT_MAGIC)
MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_LOG_BYTES = 64 * 1024 * 1024


def operations_payload(operations: list | tuple) -> bytes:
    if (
        not isinstance(operations, (list, tuple))
        or not 1 <= len(operations) <= MAX_OPERATIONS
    ):
        raise InvalidInput("A commit requires 1–100 operations in a list or tuple.")
    seen = set()
    parts = []
    size = len(b'{"operations":[],"version":1}')
    for operation in operations:
        if isinstance(operation, Put):
            value = {"op": "put", "document": operation.document.to_dict()}
            document_id = operation.document.id
        elif isinstance(operation, Delete):
            value = {"op": "delete", "id": operation.id}
            document_id = operation.id
        else:
            raise InvalidInput("Unsupported operation.")
        if document_id in seen:
            raise InvalidInput("Each document ID may occur only once per commit.")
        seen.add(document_id)
        part = canonical(value)
        size += len(part) + (1 if parts else 0)
        if size > MAX_PAYLOAD_BYTES:
            raise InvalidInput("Transaction payload exceeds 1 MiB.")
        parts.append(part)
    return b'{"operations":[' + b",".join(parts) + b'],"version":1}'


def apply_operations(
    base: Snapshot, operations: list | tuple, revision: int
) -> Snapshot:
    records = dict(base.documents)
    live_bytes = base.live_bytes
    for operation in operations:
        key = operation.document.id if isinstance(operation, Put) else operation.id
        previous = records.get(key)
        if isinstance(operation, Delete) and previous is None:
            raise MissingDocument("Cannot delete a missing document.")
        if previous is not None:
            live_bytes -= len(canonical(previous.document.to_dict()))
        if isinstance(operation, Put):
            records[key] = Record(operation.document, revision)
            live_bytes += len(canonical(operation.document.to_dict()))
        else:
            del records[key]
    if len(records) > MAX_DOCUMENTS or live_bytes > MAX_LIVE_BYTES:
        raise InvalidInput("The live document count or byte limit would be exceeded.")
    return Snapshot.from_records(revision, records, live_bytes)


def encode_frame(payload: bytes, revision: int) -> bytes:
    prefix = PREFIX.pack(FRAME_MAGIC, len(payload), revision)
    header = prefix + hashlib.sha256(prefix).digest()
    return header + payload + hashlib.sha256(header + payload).digest() + COMMIT_MAGIC


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidInput("Repeated JSON field.")
        result[key] = value
    return result


def _not_number(_):
    raise InvalidInput("Unexpected JSON number.")


def _exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise InvalidInput("Unexpected payload fields.")


def decode_payload(payload: bytes) -> tuple[Put | Delete, ...]:
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_float=_not_number,
            parse_constant=_not_number,
        )
        _exact(value, ["operations", "version"])
        if type(value["version"]) is not int or value["version"] != 1:
            raise InvalidInput("Unsupported payload version.")
        if (
            not isinstance(value["operations"], list)
            or not 1 <= len(value["operations"]) <= MAX_OPERATIONS
        ):
            raise InvalidInput("Invalid operation count.")
        result = []
        for operation in value["operations"]:
            if not isinstance(operation, dict):
                raise InvalidInput("Invalid operation.")
            if operation.get("op") == "put":
                _exact(operation, ["op", "document"])
                doc = operation["document"]
                _exact(doc, ["id", "title", "text", "tags"])
                if not isinstance(doc["tags"], list):
                    raise InvalidInput("Invalid tags.")
                result.append(
                    Put(
                        Document(
                            doc["id"], doc["title"], doc["text"], tuple(doc["tags"])
                        )
                    )
                )
            elif operation.get("op") == "delete":
                _exact(operation, ["op", "id"])
                result.append(Delete(operation["id"]))
            else:
                raise InvalidInput("Unsupported operation.")
        if operations_payload(result) != payload:
            raise InvalidInput("Payload is not canonical.")
        return tuple(result)
    except (UnicodeError, ValueError, TypeError, KeyError, RecursionError) as error:
        raise InvalidInput("Invalid transaction payload.") from error


@dataclass(frozen=True, slots=True)
class Inspection:
    revision: int
    document_count: int
    live_bytes: int
    valid_bytes: int
    log_bytes: int
    log_sha256: str

    @property
    def tail_bytes(self) -> int:
        return self.log_bytes - self.valid_bytes

    def to_dict(self):
        return {
            "format_version": 1,
            "status": "incomplete_tail" if self.tail_bytes else "clean",
            "revision": self.revision,
            "document_count": self.document_count,
            "live_bytes": self.live_bytes,
            "valid_bytes": self.valid_bytes,
            "log_bytes": self.log_bytes,
            "tail_bytes": self.tail_bytes,
            "log_sha256": self.log_sha256,
        }


def _partial_header(data: bytes, revision: int, offset: int):
    if data[:4] != FRAME_MAGIC[: min(len(data), 4)]:
        raise Corruption(offset, "invalid trailing frame magic")
    if len(data) >= 8:
        length = struct.unpack(">I", data[4:8])[0]
        if not 1 <= length <= MAX_PAYLOAD_BYTES:
            raise Corruption(offset, "invalid trailing payload length")
    if (
        len(data) > 8
        and data[8:16] != struct.pack(">Q", revision)[: min(len(data) - 8, 8)]
    ):
        raise Corruption(offset, "unexpected trailing revision")
    if len(data) > PREFIX.size:
        expected = hashlib.sha256(data[: PREFIX.size]).digest()
        if data[PREFIX.size :] != expected[: len(data) - PREFIX.size]:
            raise Corruption(offset, "invalid partial header checksum")


def scan(raw: bytes) -> tuple[Snapshot, Inspection]:
    if not len(MAGIC) <= len(raw) <= MAX_LOG_BYTES or not raw.startswith(MAGIC):
        raise Corruption(0, "unsupported header or log size")
    state = Snapshot.from_records(0, {}, 0)
    offset = len(MAGIC)
    while offset < len(raw):
        remainder = len(raw) - offset
        revision = state.revision + 1
        if revision > MAX_REVISION:
            raise Corruption(offset, "revision limit exceeded")
        if remainder < HEADER_BYTES:
            _partial_header(raw[offset:], revision, offset)
            break
        header = raw[offset : offset + HEADER_BYTES]
        prefix = header[: PREFIX.size]
        if hashlib.sha256(prefix).digest() != header[PREFIX.size :]:
            raise Corruption(offset, "header checksum mismatch")
        marker, length, number = PREFIX.unpack(prefix)
        if (
            marker != FRAME_MAGIC
            or not 1 <= length <= MAX_PAYLOAD_BYTES
            or number != revision
        ):
            raise Corruption(offset, "invalid frame fields")
        frame_bytes = HEADER_BYTES + length + TRAILER_BYTES
        if remainder < frame_bytes:
            break
        payload_end = offset + HEADER_BYTES + length
        digest = raw[payload_end : payload_end + 32]
        if (
            hashlib.sha256(header + raw[offset + HEADER_BYTES : payload_end]).digest()
            != digest
        ):
            raise Corruption(offset, "payload checksum mismatch")
        if raw[payload_end + 32 : payload_end + TRAILER_BYTES] != COMMIT_MAGIC:
            raise Corruption(offset, "commit marker mismatch")
        try:
            operations = decode_payload(raw[offset + HEADER_BYTES : payload_end])
            state = apply_operations(state, operations, revision)
        except (InvalidInput, MissingDocument) as error:
            raise Corruption(offset, "invalid transaction semantics") from error
        offset += frame_bytes
    report = Inspection(
        state.revision,
        len(state.documents),
        state.live_bytes,
        offset,
        len(raw),
        hashlib.sha256(raw).hexdigest(),
    )
    return state, report
