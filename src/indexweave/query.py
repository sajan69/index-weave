"""A small required-clause query language; no executable expressions."""

from dataclasses import dataclass

from .analysis import analyze
from .errors import InvalidInput
from .model import TAG_PATTERN, utf8

QUERY_LANGUAGE = "required-clauses-v1"
MAX_QUERY_BYTES = 2048
MAX_CLAUSES = 16
MAX_QUERY_TOKENS = 32
MAX_PHRASE_TOKENS = 8


@dataclass(frozen=True, slots=True)
class Clause:
    kind: str
    terms: tuple[str, ...]


def parse_query(text: str) -> tuple[Clause, ...]:
    utf8(text, MAX_QUERY_BYTES, "Query")
    if any(ord(c) < 32 and c not in "\t\n\r" for c in text) or "\x7f" in text:
        raise InvalidInput("Query contains unsupported control characters.")
    clauses = []
    cursor = 0
    total_tokens = 0
    while cursor < len(text):
        if text[cursor].isspace():
            cursor += 1
            continue
        if len(clauses) >= MAX_CLAUSES:
            raise InvalidInput("Query exceeds 16 clauses.")
        quoted = text[cursor] == '"'
        if quoted:
            end = text.find('"', cursor + 1)
            if end < 0 or (end + 1 < len(text) and not text[end + 1].isspace()):
                raise InvalidInput("Quoted phrases require closing quotes and spacing.")
            value = text[cursor + 1 : end]
            cursor = end + 1
        else:
            end = cursor
            while end < len(text) and not text[end].isspace():
                end += 1
            value = text[cursor:end]
            cursor = end
        if not quoted and value.startswith("tag:"):
            tag = value[4:]
            if not TAG_PATTERN.fullmatch(tag):
                raise InvalidInput("Tag filters require a valid lowercase ASCII tag.")
            clauses.append(Clause("tag", (tag,)))
            continue
        if not value or any(c in value for c in '\\"():*?'):
            raise InvalidInput("Query clause uses empty or unsupported syntax.")
        if not quoted and value in {"AND", "OR", "NOT"}:
            raise InvalidInput("Boolean operators are not supported; clauses use AND.")
        tokens = analyze(value)
        if not tokens or (
            not quoted
            and (
                len(tokens) != 1 or tokens[0].start != 0 or tokens[0].end != len(value)
            )
        ):
            raise InvalidInput(
                "Bare terms must be single words; use quotes for phrases."
            )
        if quoted and len(tokens) > MAX_PHRASE_TOKENS:
            raise InvalidInput("A phrase exceeds eight tokens.")
        total_tokens += len(tokens)
        if total_tokens > MAX_QUERY_TOKENS:
            raise InvalidInput("Query exceeds 32 analyzed tokens.")
        clauses.append(
            Clause("phrase" if quoted else "term", tuple(t.term for t in tokens))
        )
    if not clauses:
        raise InvalidInput("Query requires at least one clause.")
    return tuple(clauses)
