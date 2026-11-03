"""Versioned lexical analysis with offsets into the unchanged source string."""

import unicodedata
from dataclasses import dataclass
from importlib.metadata import version

import regex

from .errors import InvalidInput
from .model import utf8

ANALYZER = "unicode-nfc-casefold-v1"
MAX_TERM_BYTES = 256
MAX_FIELD_TOKENS = 32768
_WORD = regex.compile(r"[\p{L}\p{N}][\p{L}\p{M}\p{N}]*", regex.VERSION1)


@dataclass(frozen=True, slots=True)
class Token:
    term: str
    start: int
    end: int


def analyze(text: str) -> tuple[Token, ...]:
    """Analyze bounded source text, never a caller-supplied regular expression."""
    utf8(text, 65536, "Analyzed text")
    result = []
    for match in _WORD.finditer(text):
        term = unicodedata.normalize(
            "NFC", unicodedata.normalize("NFC", match.group()).casefold()
        )
        if len(term.encode("utf-8")) > MAX_TERM_BYTES:
            raise InvalidInput("A normalized term exceeds 256 UTF-8 bytes.")
        if len(result) >= MAX_FIELD_TOKENS:
            raise InvalidInput("A field exceeds its token limit.")
        result.append(Token(term, match.start(), match.end()))
    return tuple(result)


def analyzer_metadata() -> dict:
    return {
        "analyzer": ANALYZER,
        "regex_version": version("regex"),
        "normalization_unicode_version": unicodedata.unidata_version,
    }
