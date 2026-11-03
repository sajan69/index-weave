# Security and filesystem boundaries

This package is a local POSIX storage engine. It does not expose a network
listener, load a model, fetch document URLs or upload content. Its Python API is
a trusted programming interface, not a sandbox for arbitrary objects or user
code. Supported platforms need directory-relative opens, no-follow flags and
functioning local file locks/synchronization. Windows and network filesystems
are outside this version's support; OS APIs alone do not certify every POSIX
filesystem.

Use a directory owned by the current user under a trusted parent. The engine
refuses public group/other permissions, final-component symlinks, unexpected
entries, nonregular files and hard-linked workspace files. These checks are not
protection against a privileged administrator, a hostile same-user process,
writable ancestors or every filesystem race. Advisory locks work only when
participating processes honour them. Do not edit, replace or truncate workspace
files behind an open writer.

All document versions, including deleted text, remain as plaintext in the log.
Deletion removes a live record and does not securely erase earlier bytes.
Titles, tags, IDs, snapshots, command output and backups can also reveal private
information. There is no application encryption, credential store, tenant
boundary, recovery escrow or durable remote backup. Only the suggested
`collections/` and `indexes/` directories are ignored by Git; this is not a
scanner that prevents committing private files elsewhere.

The CLI accepts bounded regular UTF-8 text files and refuses final-component
input symlinks and special files. It never executes or renders document text.
Store errors include fixed operational messages rather than document contents.
Get deliberately prints content; redirect or share it only when appropriate. A
checksum and a caller's expected revision do not authenticate the origin of a
file or document.

A failed new-store initialization can leave a partial private directory.
Existing paths are never automatically reset, removed or reused. An incomplete
append is not silently truncated; explicit matching-token repair may discard
unacknowledged changes. Complete corruption is refused. Preserve the original
before deciding to repair, and do not copy a live workspace during writes and
assume it is a consistent backup. There is no online backup or in-place
source-log replacement. Explicit checkpoint copies preserve the original, and
derived index compaction does not erase source history.

Pinned libraries provide advisory locking and development tooling. The versioned
log format, state transitions, capacity checks and recovery decisions are
custom. Hypothesis supplies generated operation sequences for a
dictionary-oracle test; see its
[stateful testing guidance](https://hypothesis.readthedocs.io/en/latest/stateful.html).
Tests use synthetic data and private temporary workspaces. No real-user
documents, credentials, public deployment, formal security review or power-loss
certification are claimed.

## Search output

Lexical search is offline and reads a strict snapshot. It does not repair logs
or write index files. Its additional input, index and output bounds are
described in [search semantics](SEARCH.md). Queries, titles, tags and source
passages can be sensitive; protect stdout and exported JSON. Strings and offsets
are evidence, not sanitized HTML. No caller-supplied regex or executable query
is evaluated. Unicode analysis uses the pinned `regex` package (Apache-2.0 AND
CNRI-Python); this is not a general file-content sanitizer or a security
certification.

## Derived index directory

[Persistent index operations](SEGMENTS.md) use a separate private directory and
never migrate or rewrite the version-1 source workspace. Manifests and segments
contain plaintext copies and metadata. Old segments remain until explicit
vacuum, which is not secure erasure. The index namespace must contain only its
managed files; a same-user file deliberately named like an unreferenced segment
is subject to explicit cleanup. Never use an unrelated directory as an index. A
corrupt active generation blocks cleanup instead of selecting an older one. A
failed publication can be visible despite an unsuccessful acknowledgement.
Runtime provenance mismatches require an explicit new index build, not silent
re-analysis or authentication bypass.

## Checkpoint copies

[Source compaction](CHECKPOINTS.md) creates a new independent workspace and
never resets an existing target or writes inside the original source. The
original still contains historical plaintext. A partial checkpoint is refused,
not silently repaired to a smaller state. A new complete but unacknowledged copy
may be visible after failure; inspect it rather than assuming failure means no
output. This is not an encrypted backup or a physical durability certification.
