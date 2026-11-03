"""Preserve source revisions while copying fictional live records to a checkpoint."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from indexweave import (
    Delete,
    Document,
    Put,
    Writer,
    compact_store,
    create_index,
    inspect,
    load_index,
    read_snapshot,
)
from indexweave.model import snapshot_digest


def run():
    with TemporaryDirectory() as root:
        source, destination, index_path = [
            Path(root) / name for name in ["source", "copy", "index"]
        ]
        with Writer.create(source) as writer:
            old = writer.write(
                [
                    Put(
                        Document(
                            "guide", "Guide", "Historical draft text. " * 80, ("notes",)
                        )
                    ),
                    Put(
                        Document(
                            "keep", "Keep", "Unchanged source passage.", ("notes",)
                        )
                    ),
                    Put(Document("remove", "Remove", "Removed document.")),
                ],
                expected_revision=0,
            )
            for number in range(5):
                writer.write(
                    [
                        Put(
                            Document(
                                "guide", "Guide", f"Draft {number}. " * 100, ("notes",)
                            )
                        )
                    ],
                    expected_revision=writer.snapshot().revision,
                )
            current = writer.write(
                [
                    Put(
                        Document(
                            "guide",
                            "Guide",
                            "A durable checkpoint preserves revisions.",
                            ("notes",),
                        )
                    ),
                    Delete("remove"),
                ],
                expected_revision=writer.snapshot().revision,
            )
        source_bytes = (source / "documents.iwl").read_bytes()
        create_index(index_path, current)
        before_search = load_index(index_path, expected_snapshot=current).search(
            "checkpoint"
        )
        result = compact_store(source, destination, expected_revision=current.revision)
        copied = read_snapshot(destination)
        assert copied == current
        assert (source / "documents.iwl").read_bytes() == source_bytes
        assert (
            load_index(index_path, expected_snapshot=copied).search("checkpoint")
            == before_search
        )
        copied_before_append = inspect(destination)
        with Writer(destination) as writer:
            advanced = writer.write(
                [Put(Document("next", "Next", "A later independent write."))],
                expected_revision=current.revision,
            )
        assert advanced.revision == current.revision + 1
        assert advanced.get("keep").revision == 1
        assert old.get("remove") is not None and copied.get("remove") is None
        assert read_snapshot(source) == current
        return {
            "completed": True,
            "workload": "synthetic-source-compaction-v1",
            "compaction": result,
            "checkpoint_bytes": copied_before_append.checkpoint_bytes,
            "preserved_document_revisions": {
                key: record.revision for key, record in copied.documents.items()
            },
            "source_revision_after_target_append": read_snapshot(source).revision,
            "target_revision_after_append": advanced.revision,
            "source_unchanged": True,
            "old_snapshot_preserved": True,
            "existing_index_compatible": True,
            "copied_content_sha256": snapshot_digest(copied),
        }


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True, indent=2))
