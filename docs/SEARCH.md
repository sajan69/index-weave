# Lexical search

`indexweave search WORKSPACE --query '"write ahead" tag:storage' --limit 10`
loads a strict storage snapshot, builds a positional index in memory and returns
JSON. It writes no index files and makes no network requests. Each CLI
invocation replays the log and rebuilds the index. A corrupt or incomplete
storage log is refused under the existing [storage contract](STORAGE.md).

```python
from indexweave import LexicalIndex, read_snapshot

index = LexicalIndex.from_snapshot(read_snapshot("collections/example"))
result = index.search('"write ahead" tag:storage', limit=10)
```

An index copies the snapshot's record mapping and freezes its tokens, postings
and tag sets. Later writes do not change it. Build another index from a fresh
snapshot to search new content. Deleted documents and replaced terms are absent
from that new index; the old index remains a deliberately older search view.
There is no background refresh, persistent segment format or compaction here.

## Query language

The version is `required-clauses-v1`. Every whitespace-separated clause is
required (implicit AND):

| Input                   | Meaning                                   |
| ----------------------- | ----------------------------------------- |
| `journal recovery`      | Both terms anywhere in title or body      |
| `"write ahead"`         | Adjacent analyzed tokens within one field |
| `recovery tag:storage`  | Required term and exact lowercase tag     |
| `tag:storage tag:notes` | Both tags; no textual ranking             |

Bare terms must occupy one complete lexical token. Use quotes for punctuation
separators or multiple words, for example `"write-ahead"`. Phrases match token
adjacency, so commas, whitespace and line breaks between words do not prevent a
match. A phrase never crosses from title to body. Stopwords remain significant.
No stemming, substring, fuzzy, wildcard, Boolean, exclusion or fielded text
operators are implemented. Uppercase bare `AND`, `OR`, `NOT` are rejected;
lowercase words or quoted uppercase words search their literal analyzed terms.

Quotes must close and be followed by whitespace or the end of the query. Escape
syntax is unsupported. Backslash, parentheses, colon, asterisk and question mark
are refused in text clauses, including phrases. Colon has meaning only in an
unquoted `tag:` filter. Other punctuation inside quotes is a token separator;
this does not execute a regular expression. Empty and punctuation-only text
clauses are errors. A tag-only query is allowed.

Bounds are 2,048 UTF-8 query bytes, 16 clauses, eight tokens per phrase, 32 text
tokens across clauses and `limit` from 1 to 100. Duplicate terms still require
the same match and count against these bounds, but contribute to ranking once.
Tags must follow the storage tag syntax; they are not normalized on lookup.
Invalid input is refused rather than simplified into another query.

## Analyzer and offsets

`unicode-nfc-casefold-v1` uses the maintained `regex` package's fixed Unicode
pattern: a letter or number followed by zero or more letters, marks or numbers.
The package version is pinned in `pyproject.toml`. It supplies Unicode property
matching, not the postings, query execution or ranking engine.

Each original token is normalized with Python's NFC, case folding, then NFC
again. `Café` and `CAFÉ` match; `Straße` matches `STRASSE`. There is no
compatibility folding: fullwidth letters remain different from ASCII. Emoji and
punctuation are not tokens. A contiguous Chinese sequence is one token; this
analyzer does not implement language-specific word segmentation. Leading
standalone combining marks are skipped. These choices are not universal
linguistic equivalence.

Positions are consecutive token numbers within each field. Source offsets are
half-open **Python Unicode code-point** offsets into the original title or body,
not UTF-8 bytes, JavaScript UTF-16 indices or grapheme indices. Normalization
can change token length; returned spans still slice the unchanged original
source. Consumers must convert offsets if their string representation differs.

Index metadata records the analyzer, query and scoring contract IDs, `regex`
version and Python normalization Unicode version. Different Python releases can
use different normalization databases; the metadata makes that difference
visible. Cross-version equality for arbitrary new Unicode text is not promised.

## Ranking

The scorer `bm25-positive-idf-v1` uses `k1 = 1.2`, `b = 0.75` and:

```text
idf(t) = log(1 + (N - df(t) + 0.5) / (df(t) + 0.5))
score(d) = sum over unique query text terms t of:
  idf(t) * tf(t,d) * (k1 + 1)
  / (tf(t,d) + k1 * (1 - b + b * length(d) / average_length))
```

The positive IDF and parameter values are described in the
[Lucene BM25 API](https://lucene.apache.org/core/10_3_1/core/org/apache/lucene/search/similarities/BM25Similarity.html).
This is our stated formula, not a claim of byte-for-byte Lucene score
equivalence. There are no quantized norms, field boosts or phrase bonuses. Title
and body frequencies and lengths are combined. A term's document frequency
counts a document once even if it occurs in both fields. `N` includes all live
documents, including token-free documents; average length includes them too.
Statistics come from the full snapshot, before tag or phrase filtering.

Matching enforces every clause first. The scorer sums unique normalized text
terms from both term and phrase clauses. Tags contribute no score. Tag-only
matches have score zero. Results sort by descending computed score, then ASCII
document ID for exact score ties. Floating-point scores are not probabilities,
relevance judgments or a ranking-quality guarantee.

## Results and resource boundaries

The result includes the original query, total matching documents before the
limit, snapshot revision, canonical content SHA-256, corpus counts and analyzer
provenance. The content digest covers sorted live document objects and their
last-put revisions, each encoded as canonical JSON plus a newline. It is an
integrity/provenance identifier, not authenticated source ownership or proof of
query relevance. Snapshot revision is separately recorded.

Each hit includes per-term `tf`, `df`, IDF and score contributions. Matches
retain the first occurrence per clause per field, at most 32 spans, rather than
every occurrence. A passage is up to 240 original code points, starting up to 60
before the first retained match; for tag-only results it starts at body offset
zero. A long phrase can extend beyond that passage. Match offsets refer to the
whole field, not the shortened passage. Titles, queries and passages are plain
strings, not safe HTML. A web client must render them as text.

The index refuses more than one million token occurrences, 100,000 distinct
terms, 32,768 tokens per field or 256 UTF-8 bytes in a normalized term. These
are additional search bounds; a valid stored document can therefore be
unindexable. A failed build leaves storage and previously built indexes
unchanged. No tokens are silently dropped to fit a budget. Byte/token limits are
not exact resident memory limits. Temporary construction structures also consume
memory.

Candidate intersections use term postings and exact tag sets; positional checks
verify phrases. All matching documents are scored and sorted before returning
the bounded top results. Full evidence is assembled only for returned hits. This
is exact bounded retrieval, without an approximate search index. There is no
hosted service, authorization boundary or hostile-code sandbox. The Python
snapshot/index constructors are trusted programming APIs, not arbitrary object
validation interfaces.
