"""Explicit source-log compaction into a new, independent private workspace."""

import os
from pathlib import Path

from .checkpoint import encode_checkpoint
from .errors import CommitUnknown, Conflict, InvalidInput
from .model import MAX_REVISION, snapshot_digest
from .storage import Writer, inspect


def compact_store(source, destination, *, expected_revision):
    if type(expected_revision) is not int or not 0 <= expected_revision <= MAX_REVISION:
        raise InvalidInput("Expected revision is outside the supported range.")
    source_path, destination_path = Path(source).resolve(), Path(destination).resolve()
    if source_path == destination_path or source_path in destination_path.parents:
        raise InvalidInput(
            "Compaction requires a destination outside the source workspace."
        )
    try:
        with Writer(source) as source_writer:
            snapshot = source_writer.snapshot()
            if snapshot.revision != expected_revision:
                raise Conflict("Source revision changed; no destination was created.")
            original_bytes = os.fstat(source_writer._log).st_size
            encoded = encode_checkpoint(snapshot)
            with Writer._create(destination, encoded) as destination_writer:
                copied = destination_writer.snapshot()
                if copied != snapshot:
                    raise InvalidInput(
                        "Compacted state did not match the source snapshot."
                    )
                result = inspect(destination)
            return {
                "format_version": result.format_version,
                "revision": snapshot.revision,
                "document_count": len(snapshot.documents),
                "content_sha256": snapshot_digest(snapshot),
                "source_log_bytes": original_bytes,
                "destination_log_bytes": result.log_bytes,
                "destination_log_sha256": result.log_sha256,
            }
    except FileExistsError:
        raise
    except OSError as error:
        raise CommitUnknown(
            "Compaction outcome is unknown; source was not reset. "
            "Inspect the new destination."
        ) from error
