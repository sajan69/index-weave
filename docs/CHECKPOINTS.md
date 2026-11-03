# Source-log checkpoints and compaction

`compact_store` copies the live source state into a **new** private workspace
with a version-2 checkpoint. The original workspace is never rewritten, renamed
or deleted. This is an explicit independent fork, not an in-place storage
replacement, replication, secure erasure or a general backup guarantee.

```sh
.venv/bin/indexweave inspect collections/example
.venv/bin/indexweave compact-store collections/example --out collections/compacted --expected-revision 2
.venv/bin/indexweave inspect collections/compacted
```

The revision above is illustrative: supply the actual source revision from
inspection. The destination must not exist and must be outside the source
workspace, including resolved nested aliases. Both workspaces contain the same
three managed files: `documents.iwl`, `writer.lock` and `access.lock`. There is
no new source-directory entry or relaxation of file permissions, link checks or
advisory locking. See [filesystem boundaries](SECURITY.md).

The Python API is `compact_store(source, destination, expected_revision=N)`.
Close any existing writer for the source before calling it. Compaction obtains
the source's writer lease and retains it through destination verification. A
busy source or a revision conflict is refused before destination creation.
Cooperating readers can still read the unchanged source between access gates.
All input snapshot and byte-budget checks occur before destination creation.

The result reports the preserved revision, document count/content digest and
actual source/destination log byte counts. An empty or short source may produce
a **larger** checkpoint because of fixed framing overhead. The original file
still occupies space, so making the copy does not itself free disk space. Switch
consumers to the verified new path deliberately; later writes on either path are
independent and are not synchronized.

## Revision and index compatibility

The checkpoint preserves the global source revision and each live document's
last-put revision. It omits earlier versions and deleted records. A write to a
compacted workspace uses the preserved global revision as its expected revision
and commits at the next revision. It does not restart at 0 or assign the copy
operation as every document's revision. Existing immutable snapshots remain
unchanged. There is no historical time-travel API or promise that omitted source
history can be recovered from the checkpoint.

Canonical live-content SHA-256 is unchanged. A [derived index](SEGMENTS.md) can
therefore be used with the compacted workspace only when its exact captured
revision, content digest and analyzer provenance still match. Compaction does
not copy, rewrite or silently refresh an index. Subsequent independent writes
can make the derived index stale in the ordinary way. Fingerprints are not
collection-identity or ownership authentication.

Version-1 workspaces remain readable and writable, and normal `create` still
creates version 1. Opening a workspace never migrates it. The new reader
supports both formats; older package versions do not understand version 2. Keep
the original if compatibility with an older reader is needed. There is no silent
version downgrade or in-place upgrade.

## Version-2 byte format

The log begins with the eight bytes `IWLOG`, NUL, byte 2, LF. They are followed
by one metadata frame, zero or more record-chunk frames, a complete checkpoint
commit footer, then zero or more ordinary transaction frames.

Metadata and chunk frames reuse the [transaction framing](STORAGE.md):

```text
4-byte marker, 4-byte big-endian payload length, 8-byte big-endian number
32-byte SHA-256 of those 16 prefix bytes
canonical UTF-8 JSON payload
32-byte SHA-256 of header plus payload
8-byte IWCOMMIT trailer
```

The metadata marker is `IWCP`; its frame number is the preserved global
revision, which may be zero. Its payload is capped at 16 KiB and has exactly
these fields:

| Field            | Contract                                                                        |
| ---------------- | ------------------------------------------------------------------------------- |
| `version`        | Integer 2                                                                       |
| `revision`       | Non-Boolean integer 0 through 2^63−1, equal to the frame number                 |
| `document_count` | 0–10,000 live documents                                                         |
| `live_bytes`     | Sum of canonical live document JSON bytes, at most 32 MiB                       |
| `chunk_count`    | 0–256 record chunks                                                             |
| `records_sha256` | SHA-256 of sorted canonical `{document, revision}` records, each followed by LF |

A chunk's marker is `IWCR`; its frame number is its ordinal, starting at 1.
Payloads have exactly `version` (integer 2) and `records` (1–100 records). Each
record has exactly `document` in the existing document schema and `revision`
from 1 through the preserved global revision. IDs must be sorted and unique
across **all** chunks. Each chunk payload is at most 1 MiB. The encoder packs
records up to either the count or byte limit; it never splits a document. An
empty checkpoint has zero chunks, including a later revision whose documents
have all been deleted.

The checkpoint footer is `IWBASEOK` (eight bytes), then a 32-byte SHA-256
covering all earlier checkpoint bytes, including the version magic, metadata and
chunks. The reader also recomputes live counts, byte accounting and the record
digest. Header checksums are verified before trusting payload lengths. The
parser rejects duplicate keys, floats, nonfinite values, unknown fields,
noncanonical JSON, invalid UTF-8, invalid record revisions and excessive
structure. It caps JSON depth at 32 and value visits at 100,000 **per payload
after native parsing**. These and the source file's 64 MiB limit are not exact
resident-memory limits.

Only after the whole checkpoint validates can ordinary `IWFR` frames be
replayed. Their first revision is the checkpoint's global revision plus 1;
normal sequential revision, batch semantics, capacity and corruption checks
remain. A maximum-revision checkpoint can be read but cannot accept another
commit.

## Incomplete data and failure handling

A partial or corrupt metadata frame, record chunk or checkpoint footer is a
`Corruption` error. It is **not** a recoverable transaction tail. No checkpoint
prefix is returned as a partial live state, and `repair-tail` cannot turn a
partial checkpoint into an empty or reduced workspace.

After a complete checkpoint, the existing inspection and explicit tail-repair
protocol applies to an incomplete ordinary transaction suffix. Version-2
inspection adds `checkpoint_revision` and `checkpoint_bytes`; its valid-prefix
byte count never precedes the complete checkpoint. Version-1 inspection output
is unchanged. Complete transaction corruption still refuses repair.

Creation uses exclusive new files and private modes. The complete checkpoint
file, new directory and parent directory are synchronized before reopening and
verifying the destination. The destination writer lease remains held through
verification; the source lease is released after the copy operation. The
implementation uses unbuffered descriptor writes and Python's documented
[`os.fsync`](https://docs.python.org/3.12/library/os.html#os.fsync), including
retries for short writes through the existing storage writer.

An I/O or process failure can leave a partial new directory or a complete but
unacknowledged checkpoint. Inspect before deciding what to do. Existing paths
are never reset or reused automatically, and the original source stays intact.
Synchronization calls and process-kill tests do not prove hardware power-loss
safety, correct behavior on every filesystem, protection from a hostile
same-user process or a production recovery-point/recovery-time objective.

Document text remains plaintext. The old source retains omitted history; memory,
exports, derived segments and backups may also retain it. No old workspace is
automatically deleted, and compaction is not secure erasure or an encrypted
backup mechanism.

For a reproducible temporary example with historical versions, deletion,
revision preservation, derived-index compatibility and a later independent
write:

```sh
.venv/bin/python examples/compaction_demo.py
```
