"""Immutable base/delta segments with explicit generation publication and GC."""

import hashlib
import os

from . import index_files as io
from .errors import CommitUnknown, Conflict, InvalidInput, StaleIndex
from .lexical import LexicalIndex
from .model import Snapshot, canonical
from .segment_codec import (
    MANIFEST_MAGIC,
    MAX_MANIFEST_BYTES,
    MAX_SEGMENT_BYTES,
    MAX_SEGMENTS,
    SEGMENT_MAGIC,
    digest,
    encode,
    materialize,
    parse_manifest,
    parse_segment,
    provenance,
    require,
    segment_value,
)


def _copy_state(index=None):
    if index is None:
        return {}, {}, {}
    return (
        dict(index.snapshot.documents),
        dict(index.tokens),
        {term: dict(documents) for term, documents in index.postings.items()},
    )


def _merge(records, tokens, postings, puts, new_tokens, new_postings, deletes):
    replaced = set(puts) | set(deletes)
    require(set(deletes) <= set(records))
    for key in replaced:
        records.pop(key, None)
        tokens.pop(key, None)
    for term in list(postings):
        postings[term] = {
            key: fields for key, fields in postings[term].items() if key not in replaced
        }
        if not postings[term]:
            del postings[term]
    records.update(puts)
    tokens.update(new_tokens)
    for term, documents in new_postings.items():
        postings.setdefault(term, {}).update(documents)


def _load(root):
    raw = io.read(root, io.CURRENT, MAX_MANIFEST_BYTES)
    manifest = parse_manifest(raw)
    records, tokens, postings = _copy_state()
    revision = 0
    for number, sha in enumerate(manifest["segments"]):
        data = io.read(root, "seg-" + sha + ".iws", MAX_SEGMENT_BYTES)
        revision, puts, new_tokens, new_postings, deletes = parse_segment(
            data, sha, revision, base=number == 0
        )
        _merge(records, tokens, postings, puts, new_tokens, new_postings, deletes)
        index = materialize(revision, records, tokens, postings)
    require(
        revision == manifest["source_revision"]
        and index.content_sha256 == manifest["source_sha256"]
    )
    return manifest, index


def _expected(snapshot):
    if not isinstance(snapshot, Snapshot):
        raise InvalidInput("A source storage Snapshot is required.")


def load_index(path, *, expected_snapshot):
    """Never silently serve a stale generation as a fresh source snapshot."""
    _expected(expected_snapshot)
    with io.session(path) as root:
        manifest, index = _load(root)
        if manifest["source_revision"] != expected_snapshot.revision or manifest[
            "source_sha256"
        ] != digest(expected_snapshot):
            raise StaleIndex(
                "Index does not match the supplied source snapshot; refresh explicitly."
            )
        return index


def inspect_index(path):
    with io.session(path) as root:
        manifest, index = _load(root)
        sizes = io.inventory(root)
        referenced = {"seg-" + sha + ".iws" for sha in manifest["segments"]}
        return {
            **manifest,
            "index": index.metadata(),
            "referenced_bytes": sum(sizes[name] for name in referenced),
            "directory_bytes": sum(sizes.values()),
            "unreferenced_segments": len(
                [
                    name
                    for name in sizes
                    if io.SEGMENT_NAME.fullmatch(name) and name not in referenced
                ]
            ),
            "pending_manifest": io.PENDING in sizes,
        }


def _publish(root, index, *, generation, previous=None, hashes=()):
    value = segment_value(index, previous)
    data = encode(value, SEGMENT_MAGIC, MAX_SEGMENT_BYTES)
    sha = hashlib.sha256(data).hexdigest()
    manifest = {
        "version": 1,
        "generation": generation,
        "source_revision": index.snapshot.revision,
        "source_sha256": index.content_sha256,
        "provenance": provenance(),
        "segments": [*hashes, sha],
    }
    manifest_data = encode(manifest, MANIFEST_MAGIC, MAX_MANIFEST_BYTES)
    io.publish(root, "seg-" + sha + ".iws", data, manifest_data)
    return manifest


def create_index(path, snapshot):
    _expected(snapshot)
    index = LexicalIndex.from_snapshot(snapshot)
    # Serialize and enforce the segment budget before creating the destination.
    encode(segment_value(index), SEGMENT_MAGIC, MAX_SEGMENT_BYTES)
    io.initialize(path)
    try:
        with io.session(path, write=True) as root:
            result = _publish(root, index, generation=1)
        return result
    except OSError as error:
        raise CommitUnknown(
            "Index creation outcome is unknown; inspect without resetting it."
        ) from error


def _cas(manifest, generation):
    if type(generation) is not int or not 1 <= generation < (1 << 63):
        raise InvalidInput("Expected generation must be a positive bounded integer.")
    if manifest["generation"] != generation:
        raise Conflict("Index generation changed; no new generation was published.")
    if generation >= (1 << 63) - 1:
        raise InvalidInput("Index generation limit reached.")


def _updated(old, snapshot):
    changed = {
        key: record
        for key, record in snapshot.documents.items()
        if old.snapshot.documents.get(key) != record
    }
    if any(record.revision <= old.snapshot.revision for record in changed.values()):
        raise StaleIndex(
            "Changed records must have revisions newer than the indexed snapshot."
        )
    partial = Snapshot.from_records(
        snapshot.revision,
        changed,
        sum(len(canonical(r.document.to_dict())) for r in changed.values()),
    )
    fresh = LexicalIndex.from_snapshot(partial)
    records, tokens, postings = _copy_state(old)
    _merge(
        records,
        tokens,
        postings,
        changed,
        fresh.tokens,
        fresh.postings,
        set(old.snapshot.documents) - set(snapshot.documents),
    )
    return materialize(snapshot.revision, records, tokens, postings)


def refresh_index(path, snapshot, *, expected_generation):
    _expected(snapshot)
    try:
        with io.session(path, write=True) as root:
            manifest, old = _load(root)
            _cas(manifest, expected_generation)
            if snapshot.revision < old.snapshot.revision:
                raise StaleIndex("Source revision is older than the published index.")
            if snapshot.revision == old.snapshot.revision:
                if digest(snapshot) != old.content_sha256:
                    raise StaleIndex("Source changed without a newer revision.")
                return manifest
            if len(manifest["segments"]) >= MAX_SEGMENTS:
                raise InvalidInput(
                    "Segment limit reached; compact explicitly before refresh."
                )
            index = _updated(old, snapshot)
            result = _publish(
                root,
                index,
                generation=expected_generation + 1,
                previous=old,
                hashes=manifest["segments"],
            )
        return result
    except OSError as error:
        raise CommitUnknown(
            "Index refresh outcome is unknown; inspect before retrying."
        ) from error


def compact_index(path, *, expected_generation):
    """Publish a merged base without changing the indexed source revision."""
    try:
        with io.session(path, write=True) as root:
            manifest, index = _load(root)
            _cas(manifest, expected_generation)
            if len(manifest["segments"]) == 1:
                return manifest
            result = _publish(root, index, generation=expected_generation + 1)
        return result
    except OSError as error:
        raise CommitUnknown(
            "Index compaction outcome is unknown; inspect before retrying."
        ) from error


def vacuum_index(path, *, expected_generation):
    """Only unreferenced derived files are removed, after active-state validation."""
    try:
        with io.session(path, write=True) as root:
            manifest, _ = _load(root)
            _cas(manifest, expected_generation)
            referenced = {"seg-" + sha + ".iws" for sha in manifest["segments"]}
            removed = []
            for name in sorted(io.inventory(root)):
                if name == io.PENDING or (
                    io.SEGMENT_NAME.fullmatch(name) and name not in referenced
                ):
                    os.unlink(name, dir_fd=root)
                    removed.append(name)
            os.fsync(root)
        return {"generation": expected_generation, "removed_files": len(removed)}
    except OSError as error:
        raise CommitUnknown(
            "Index cleanup may be partial; inspect before retrying."
        ) from error
