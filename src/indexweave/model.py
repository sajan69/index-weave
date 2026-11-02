import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .errors import InvalidInput

MAX_TEXT_BYTES = 64 * 1024
MAX_LIVE_BYTES = 32 * 1024 * 1024
MAX_DOCUMENTS = 10_000
MAX_OPERATIONS = 100
MAX_REVISION = (1 << 63) - 1
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
TAG_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\Z")


def valid_id(value: object) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise InvalidInput(
            "Document IDs require 1–64 ASCII letters, digits, dots, underscores "
            "or hyphens; start with a letter or digit."
        )
    return value


def utf8(value: object, maximum: int, label: str) -> bytes:
    if not isinstance(value, str):
        raise InvalidInput(f"{label} must be text.")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise InvalidInput(f"{label} must be valid UTF-8 text.") from error
    if not 1 <= len(encoded) <= maximum:
        raise InvalidInput(f"{label} is outside its byte limit.")
    return encoded


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class Document:
    id: str
    title: str
    text: str
    tags: tuple[str, ...] = ()

    def __post_init__(self):
        valid_id(self.id)
        utf8(self.title, 512, "Title")
        utf8(self.text, MAX_TEXT_BYTES, "Document text")
        if not self.title.strip() or any(
            ord(c) < 32 or ord(c) == 127 for c in self.title
        ):
            raise InvalidInput(
                "Title must be nonblank and contain no control characters."
            )
        if (
            any(ord(c) < 32 and c not in "\n\r\t" for c in self.text)
            or "\x7f" in self.text
        ):
            raise InvalidInput("Document text contains unsupported control characters.")
        if not isinstance(self.tags, tuple) or len(self.tags) > 16:
            raise InvalidInput("Tags must be a tuple of at most 16 unique ASCII tags.")
        if any(
            not isinstance(tag, str) or not TAG_PATTERN.fullmatch(tag)
            for tag in self.tags
        ):
            raise InvalidInput(
                "Tags require 1–32 lowercase ASCII letters, digits, "
                "underscores or hyphens."
            )
        if len(set(self.tags)) != len(self.tags):
            raise InvalidInput("Repeated tags are not allowed.")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "tags": list(self.tags),
        }


@dataclass(frozen=True, slots=True)
class Put:
    document: Document

    def __post_init__(self):
        if not isinstance(self.document, Document):
            raise InvalidInput("Put requires a Document.")


@dataclass(frozen=True, slots=True)
class Delete:
    id: str

    def __post_init__(self):
        valid_id(self.id)


@dataclass(frozen=True, slots=True)
class Record:
    document: Document
    revision: int


@dataclass(frozen=True, slots=True)
class Snapshot:
    revision: int
    documents: Mapping[str, Record]
    live_bytes: int

    @classmethod
    def from_records(cls, revision: int, records: dict[str, Record], live_bytes: int):
        return cls(revision, MappingProxyType(records.copy()), live_bytes)

    def get(self, document_id: str) -> Record | None:
        return self.documents.get(valid_id(document_id))
