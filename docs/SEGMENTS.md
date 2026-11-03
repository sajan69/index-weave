# Persistent lexical index

The document store remains the authority. A derived index lives in a **separate,
new, private directory**; it does not add files to or change the version-1
[document workspace](STORAGE.md). It contains copies of text, positional tokens
and inverted postings. Do not place unrelated files in this managed namespace.
The initial support boundary remains cooperating processes on a trusted local
POSIX filesystem, with CPython 3.12 or later.

## Commands

After creating and populating `collections/example`:

```sh
mkdir -p indexes
.venv/bin/indexweave index-create collections/example --index indexes/example
.venv/bin/indexweave search collections/example --index indexes/example --query 'search tag:notes'
.venv/bin/indexweave index-inspect indexes/example
```

The destination must not exist. Creation never resets an existing directory,
including a source workspace. Failure during initialization can leave a partial
new derived directory; preserve it for inspection and choose a new destination
rather than assuming a retry can reset it.

A document mutation does not also commit an index generation. After changing
source data, search with an outdated index refuses with `StaleIndex`. Refresh
explicitly with the generation observed by `index-inspect`:

```sh
.venv/bin/indexweave index-refresh collections/example --index indexes/example --expected-generation 1
.venv/bin/indexweave index-compact indexes/example --expected-generation 2
.venv/bin/indexweave index-vacuum indexes/example --expected-generation 3
```

These numbers illustrate a create, one refresh and one multi-segment compaction;
use actual inspection results. Every mutating existing-index operation requires
an expected generation. A stale generation is refused before publication or
cleanup. Refreshing identical source state and compacting a single segment are
no-ops that retain the generation. Compaction merges **derived index segments**;
it does not compact the source log or change source revisions/CAS behavior.

The Python functions `create_index`, `refresh_index`, `load_index`,
`compact_index`, `vacuum_index` and `inspect_index` are exported from
`indexweave`. `load_index(path, expected_snapshot=snapshot)` requires a storage
`Snapshot` and returns the same immutable `LexicalIndex` search interface as an
in-memory build.

## Generations and freshness

A manifest generation is distinct from a source revision. Refresh advances the
generation and records a later source snapshot. Compaction advances only the
generation, retaining source revision, document revisions, content digest,
search scores and source spans. Refresh rejects rollback, different content at
the same source revision, and changed records whose revisions are not newer than
the previously indexed source revision.

Every load compares both source revision and canonical live-content SHA-256 to
the supplied snapshot. A revision alone is insufficient. This does not prove the
snapshot's ownership, collection identity or origin; these are trusted local
programming inputs, and fingerprints are not authentication. A concurrent source
write can occur after a caller captures a snapshot. Results describe that
captured revision, not a promise that the source cannot change during a query.

Manifest and segment analyzer/query/scoring provenance must exactly match the
current runtime. This includes Python's normalization Unicode version. After an
incompatible runtime change, build into a new directory from source; files are
not silently reinterpreted or relabelled with a new analyzer version.

## Files and byte format

The index directory contains private `writer.lock`, `access.lock`, `CURRENT`,
zero or more `seg-<64 lowercase hex SHA-256>.iws` files, and possibly
`CURRENT.pending` after an interrupted publication. Unexpected entries,
symlinks, hard links, special files, public modes and wrong owners are refused.
Parent directories and other same-user processes must be trusted. Only managed
file names are constructed; no manifest-provided arbitrary paths are opened.

A manifest file is:

```text
7 bytes: IWMAN1 followed by LF
canonical UTF-8 JSON
32 bytes: SHA-256 of all preceding bytes
```

The exact JSON fields are `version` (integer 1), `generation`,
`source_revision`, `source_sha256`, `provenance` and `segments` (ordered
distinct segment digests). Generation starts at 1. Source revision can be 0 for
an empty source. Revisions and generations are non-Boolean integers within the
source revision range.

A segment has the same framing with `IWSEG1` followed by LF. Its filename digest
covers the **entire** framed file, independently of the embedded checksum. The
exact JSON fields are:

| Field                          | Meaning                                                                     |
| ------------------------------ | --------------------------------------------------------------------------- |
| `version`                      | Integer 1                                                                   |
| `kind`                         | First segment `base`; subsequent segments `delta`                           |
| `from_revision`, `to_revision` | Base starts at 0; delta starts at previous segment end and advances         |
| `provenance`                   | Exact analyzer/query/scoring runtime contract                               |
| `puts`                         | Sorted unique document records, last-put revisions and per-field token rows |
| `deletes`                      | Sorted unique existing IDs removed by this delta                            |
| `postings`                     | Sorted unique `[term, document ID, field, positions]` rows                  |

A token row is
`[normalized term, start code-point offset, end code-point offset]`. Title and
text fields remain separate. The decoder checks complete word spans,
normalization, ordering, coverage and exact correspondence between token
positions and inverted posting rows. It rejects missing, duplicate or invented
postings and invalid field offsets, even when a checksum has been recomputed. It
uses the fixed analyzer pattern for validation; loading does not invoke
`analyze` or rebuild postings with `LexicalIndex.from_snapshot`.

The JSON parser rejects duplicate keys, floats, nonfinite values, unknown
fields, noncanonical serialization, invalid UTF-8, excessive depth and excessive
value visits. Digests detect accidental changes, not a malicious actor who can
rewrite data and recompute them. No pickle, executable query or downloaded code
is deserialized. The format is intentionally explicit JSON, not a compressed or
space-optimal index representation.

## Refresh and compaction

Refresh compares immutable document records with the last published snapshot.
Only changed/new document fields are tokenized; deleted IDs become tombstones.
The new segment persists those records, tokens and their inverted postings. When
loading a chain, replaced or deleted IDs first lose every earlier posting; new
rows are merged, and corpus lengths, tags and document frequencies describe only
the resulting live snapshot. Each intermediate state is checked against index
capacity limits. BM25 uses the same [search contract](SEARCH.md).

Compaction loads and validates the complete active chain, then writes one base
segment containing only its live records and persisted postings. It does not
retokenize text or silently update source state. Old files remain unreferenced
until explicit vacuum. A returned index is fully materialized and immutable;
previously loaded views continue working after compaction and vacuum.

## Publication, failures and cleanup

Mutations acquire the lifetime-of-operation writer lease and an exclusive access
gate. Readers hold a shared gate through manifest and segment loading. A
cooperating writer/compactor/vacuum cannot delete files during that read. After
loading, a reader holds only in-memory data and needs no file lease. Conflicting
operations receive `Busy` rather than waiting indefinitely.

Publication follows this order:

1. Write a new segment with exclusive creation and private modes; complete short
   writes and synchronize the file. An existing content-addressed file is reused
   only if its complete bytes match.
2. Synchronize the directory before publishing a reference to that segment.
3. Exclusively create `CURRENT.pending`, write the complete checksummed manifest
   and synchronize it.
4. Replace `CURRENT` with the pending file in the same directory, then
   synchronize the directory before returning an acknowledgement and releasing
   the gate.

The atomic namespace replacement uses Python's documented
[`os.replace`](https://docs.python.org/3.12/library/os.html#os.replace)
behavior. Atomic replacement and `fsync` calls do not certify every filesystem,
storage controller or physical power-loss scenario. Tests exercise process exits
and controlled I/O failures, not hardware failure.

An interrupted operation can leave an unreferenced partial segment or pending
manifest. If replacement did not occur, the prior active generation remains the
reader's choice. After replacement, an unacknowledged new generation may already
be visible. Publication/synchronization failures raise `CommitUnknown`; inspect
before deciding whether to retry. There is no automatic rollback, fallback to an
older generation, truncation, deletion or corrupt-active-manifest repair.

Vacuum first validates the **complete active generation**, verifies the caller's
expected generation, then unlinks only unreferenced managed segment files and
`CURRENT.pending`, and synchronizes the directory. A corrupt active generation
blocks cleanup. A cleanup I/O failure can leave partial cleanup; the active
manifest remains the reference set. Pending files are never promoted as a
recovery guess. Do not copy a live index during publication as though it were a
transactional backup; rebuilding from a preserved source snapshot is separate.

## Bounds and limitations

Bounds are 32 MiB per segment, 16 KiB per manifest, 128 MiB total managed file
bytes, 16 referenced segments and 64 managed files including unreferenced files
and locks. Serialization is bounded before publishing, and publication budgets
include temporary pending-manifest space. The decoder caps JSON depth at 32 and
value visits at four million after native parsing. Existing document, live-byte,
token and unique-term limits also apply. These are not exact RAM limits.

Refresh refuses a full segment chain; compact explicitly. Publication refuses an
exhausted directory budget; inspect and vacuum unreferenced files. It may be
necessary to build a new derived directory if there is insufficient temporary
space to compact safely. No automatic deletion is used to make room.

All active segment bytes are loaded and validated into memory. Source snapshot
verification still replays the document log. This is not a memory-mapped, lazy
posting reader, a network service or a measured speed improvement. Old document
text can remain in delta segments, unreferenced files, memory and the source
log. Vacuum is not secure erasure. Source-log compaction, online backup, model
inference, semantic retrieval and a web interface are not provided by this API.
