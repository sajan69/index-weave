# IndexWeave

A document search application with a custom persistent document store. Its
product scope includes lexical and embedding-based retrieval, an API and a web
interface. The available Python package and CLI provide revisioned storage,
positional lexical search, persisted index segments and revision-preserving
checkpoint copies. Model inference, an HTTP server and a web interface are not
yet provided.

## Storage quickstart

Use CPython 3.12 or newer on a POSIX local filesystem. Workspace parents must be
trusted. Installation needs access to the Python package registry; storage
operations make no network requests and require no API keys.

```sh
python -m venv .venv
.venv/bin/pip install .
mkdir -p collections
.venv/bin/indexweave create collections/example
.venv/bin/indexweave put collections/example --id welcome --title "Welcome" --file examples/welcome.md --tag notes --expected-revision 0
.venv/bin/indexweave get collections/example --id welcome
.venv/bin/indexweave list collections/example
.venv/bin/indexweave inspect collections/example
.venv/bin/indexweave search collections/example --query 'search tag:notes'
```

Creation refuses existing paths. Put inserts or replaces the whole document
under its stable ID. Mutations require the revision the caller expects; stale
revisions fail without appending. Deletion is explicit:

```sh
.venv/bin/indexweave delete collections/example --id welcome --expected-revision 1
```

List returns metadata rather than document bodies, with `--limit 1..100` and an
optional `--after ID` cursor. Get includes the document and store revisions.
Outputs are JSON and may contain sensitive text; errors go to stderr with exit
code 2.

For a complete temporary example with two documents, an update/delete batch and
an explicit incomplete-tail repair:

```sh
.venv/bin/python examples/storage_demo.py
```

It uses only a new temporary directory and fixed fictional text. It never opens
an existing user collection. The normal reader does not silently discard damaged
data. See [storage format and recovery](docs/STORAGE.md) before attempting a
repair.

## Python API

```python
from indexweave import Document, Put, Writer, read_snapshot

with Writer("collections/example") as writer:
    revision = writer.snapshot().revision
    snapshot = writer.write(
        [Put(Document("guide", "Guide", "A searchable note", ("notes",)))],
        expected_revision=revision,
    )

record = read_snapshot("collections/example").get("guide")
assert record.document.text == "A searchable note"
```

One writer holds a lifetime lease. Independent readers can obtain immutable
snapshots between writes; they receive a Busy error while an exclusive write
gate is held. Whole-log replay is bounded and intentionally simple. Source
compaction creates a new revision-preserving workspace; this is not a
general-purpose SQL/relational database.

## Search

[Lexical search semantics](docs/SEARCH.md) defines required terms, quoted
phrases, exact tag filters, Unicode offsets, snapshot consistency and BM25
ranking. Default search builds an in-memory index; `--index` loads a matching
persisted generation. Both modes verify a strict source snapshot without
mutating storage. Results include source passages, score contributions and
revision provenance. For a temporary fictional collection with search and
update/delete checks:

```sh
.venv/bin/python examples/search_demo.py
```

## Persistent index

[Persistent index semantics](docs/SEGMENTS.md) covers explicit index creation,
incremental refresh, stale-snapshot refusal, compaction and cleanup. The index
lives in a separate directory and preserves the source document log.

```sh
.venv/bin/python examples/segments_demo.py
```

## Source compaction

[Checkpoint semantics](docs/CHECKPOINTS.md) describes explicit source-log
compaction into a new workspace while retaining global and document revisions.
The original stays untouched; later writes to the new copy are independent.

```sh
.venv/bin/python examples/compaction_demo.py
```

The [retrieval experiment contract](docs/RETRIEVAL.md) defines public-data
provenance, fixed query splits and evaluation methods.

## Verification

Use Python 3.12 and Node 24 for development tooling:

```sh
.venv/bin/pip install -r requirements-dev.txt
npm ci --ignore-scripts
.venv/bin/ruff check .
.venv/bin/ruff format --check .
npm run format:check
npm run links
.venv/bin/python -m build --wheel --no-isolation
.venv/bin/pip install --force-reinstall --no-deps dist/*.whl
.venv/bin/python -m unittest discover -s tests -v
```

Tests include a reference state machine, frame truncation/corruption cases,
short writes, synchronization failures, abrupt child-process exits,
separate-process locks, capacity boundaries and installed CLI operations.
Formatting uses Ruff for Python and Prettier for supported
documentation/configuration files. Ruff excludes Markdown to keep one formatter
responsible for those files. Portalocker supplies advisory locks; the log codec
and storage semantics are custom. The maintained `regex` package supplies
Unicode property matching; positional indexing, query execution and scoring are
custom.

Document bytes are stored as plaintext. Private file modes and checksums do not
provide encryption, authentication or universal power-loss protection. Read the
[security and filesystem boundaries](docs/SECURITY.md) and
[verification evidence](docs/EVALUATION.md). Engine demonstrations use synthetic fixtures; no
production adoption, storage certification or AI retrieval results are claimed.
Lexical example outcomes check mechanics, not held-out relevance quality.
