# Storage format and recovery

The storage module operates on one private workspace with three fixed entries:
`documents.iwl`, `writer.lock` and `access.lock`. The directory is created with
mode 0700 and files with 0600, subject to the caller's umask. Existing
workspaces require owned, private regular files with one hard link, and exactly
these entries. Document IDs are keys inside the log, never filenames. All file
access uses directory-relative opens and refuses final-component symlinks.

## Log bytes

The eight-byte file header is `49 57 4c 4f 47 00 01 0a` (`IWLOG`, zero, version
1, newline). Unsupported or incomplete file headers are errors, not repair
candidates. Each following frame is one atomic logical batch:

| Field           | Bytes           | Meaning                                                 |
| --------------- | --------------- | ------------------------------------------------------- |
| Frame magic     | 4               | ASCII `IWFR`                                            |
| Payload length  | 4               | Big-endian unsigned integer                             |
| Revision        | 8               | Big-endian positive integer, strictly sequential from 1 |
| Header checksum | 32              | SHA-256 of the preceding 16 bytes                       |
| Payload         | Declared length | Canonical UTF-8 JSON                                    |
| Frame checksum  | 32              | SHA-256 of the 48-byte header and payload               |
| Commit marker   | 8               | ASCII `IWCOMMIT`                                        |

The header checksum is checked before trusting the declared payload size.
Revisions are limited to 2^63−1. Payload objects have exactly `operations` and
`version: 1`. JSON keys are sorted, separators are comma/colon without extra
whitespace and Unicode is encoded directly as UTF-8. Duplicate keys, floats,
nonfinite numbers, unsupported fields/types/versions and noncanonical encodings
are refused.

A put operation has exactly `op: "put"` and `document`; its document has `id`,
`title`, `text` and `tags`. A delete has exactly `op: "delete"` and `id`. A
frame contains 1–100 operations with distinct IDs. Put inserts or replaces;
delete requires an existing record. All changes in a batch share its revision.
Unchanged documents keep their own last-put revision. Replay applies the same
state and capacity rules as a new commit, and refuses impossible histories.

| Boundary                      | Limit                                                                                        |
| ----------------------------- | -------------------------------------------------------------------------------------------- |
| Document ID                   | 1–64 ASCII characters; starts with a letter/digit, then letters/digits/dot/underscore/hyphen |
| Title                         | Nonblank, no control characters, 1–512 UTF-8 bytes                                           |
| Text                          | 1–65,536 UTF-8 bytes; tabs/newlines/carriage returns allowed, other ASCII controls refused   |
| Tags                          | Tuple of at most 16 distinct lowercase ASCII tags, 1–32 characters each                      |
| Transaction payload           | 1 MiB                                                                                        |
| Live documents                | 10,000                                                                                       |
| Live canonical document bytes | 32 MiB, including the document's JSON fields                                                 |
| Entire log                    | 64 MiB                                                                                       |

The log retains previous versions and deletions. The live-byte limit differs
from the log-size limit; updating one document repeatedly can fill the log
without many live documents. These byte/count caps do not establish an exact
memory/RSS limit.

## Commit and reader semantics

Before appending, the writer validates the full batch, expected store revision
and candidate live state. It completes short writes, retries interrupted system
calls and calls `os.fsync` before publishing the new immutable snapshot or
returning success. A writer whose append/synchronization is interrupted is
unusable until closed and reopened. A complete frame may survive an unsuccessful
acknowledgement: `CommitUnknown` means inspect the store before deciding whether
to retry. There is no automatic rollback, retry or idempotency-key protocol.

A writer lease on `writer.lock` excludes another participating writer for that
handle's lifetime. An exclusive `access.lock` gate covers append or repair.
Snapshot readers hold a shared access gate while reading and validating the
whole log. Locks are nonblocking: Busy is an explicit refusal, not a wait/retry
queue. A writer's thread lock also serializes operations on that handle. Retain
handles with a context manager; abandoned open handles can retain their lease
until closed or the process exits. Creation is not supported as a concurrent
discovery/publication operation.

Snapshots contain immutable records and a copied read-only mapping. Old
snapshots remain valid after updates or deletes. Reads from another process use
the complete validated log and do not observe a cooperating writer's in-progress
append. This is not a general multi-writer transaction manager or
network-filesystem protocol.

## Inspecting and repairing an incomplete tail

Strict reads and writer reopening refuse an incomplete trailing frame.
Inspection returns the revision/count/live bytes of the validated prefix plus
total log bytes, valid-prefix bytes, tail bytes and SHA-256 of the entire
observed log. It does not return document contents. If a complete header, frame
checksum, sequence, commit marker or transaction is invalid, inspection fails
with Corruption instead.

A short final header is checked against its available magic, length, revision
and checksum fields. A valid full header followed by insufficient
payload/trailer bytes is an incomplete-tail candidate. This classification
cannot prove why those bytes are missing. Recovery never scans ahead to guess
frame boundaries or reconstruct part of a document.

Back up the original separately and decide whether discarding that candidate is
appropriate. Only then use the current inspection tokens:

```sh
indexweave inspect PATH
indexweave repair-tail PATH --expected-log-sha256 HASH --expected-valid-bytes N --discard-incomplete-tail
```

Repair holds the writer lease and exclusive access gate, rescans and checks both
tokens, truncates only the incomplete suffix, synchronizes and rescans again.
Stale tokens or a clean log are refused without truncation. Complete corruption
is not repaired. A synchronization error after truncation has an unknown outcome
and requires fresh inspection. No online backup, salvage or arbitrary-version
migration interface is provided.

Checksums detect accidental damage, not malicious changes. Removing a complete
valid suffix is undetectable without a trusted external high-water mark.
File/directory synchronization expresses the requested ordering to the OS;
hardware, filesystem and failure-mode guarantees are outside this interface.
Process-exit tests do not emulate power removal, faulty storage controllers or
every filesystem.

Primary references checked 3 November 2026:
[Python os.fsync](https://docs.python.org/3.12/library/os.html#os.fsync) and
[Portalocker](https://portalocker.readthedocs.io/en/latest/). The storage
contract above describes this implementation, not a claim of the guarantees of
another database.
