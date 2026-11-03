"""Persist, refresh, compact and reopen a fictional search collection."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from indexweave import (
    Delete,
    Document,
    LexicalIndex,
    Put,
    Writer,
    compact_index,
    create_index,
    inspect_index,
    load_index,
    read_snapshot,
    refresh_index,
    vacuum_index,
)
from indexweave.errors import StaleIndex


def run():
    with TemporaryDirectory() as root:
        source, target = Path(root) / "source", Path(root) / "index"
        with Writer.create(source) as writer:
            first = writer.write(
                [
                    Put(
                        Document(
                            "a",
                            "Journal",
                            "A write ahead log supports recovery.",
                            ("notes",),
                        )
                    ),
                    Put(
                        Document("b", "Draft", "Temporary recovery notes.", ("draft",))
                    ),
                ],
                expected_revision=0,
            )
            initial = create_index(target, first)
            old = load_index(target, expected_snapshot=first)
            current = writer.write(
                [
                    Put(
                        Document(
                            "a",
                            "Journal",
                            "A durable journal stores committed batches.",
                            ("notes",),
                        )
                    ),
                    Delete("b"),
                    Put(
                        Document(
                            "c",
                            "Search",
                            "A positional index supports phrase search.",
                            ("notes",),
                        )
                    ),
                ],
                expected_revision=1,
            )
        try:
            load_index(target, expected_snapshot=current)
        except StaleIndex:
            stale_refused = True
        else:
            raise AssertionError("Stale generation was accepted")
        delta = refresh_index(target, current, expected_generation=1)
        loaded = load_index(target, expected_snapshot=read_snapshot(source))
        reference = LexicalIndex.from_snapshot(current)
        for query in ["durable", "recovery", '"phrase search"', "tag:notes"]:
            assert loaded.search(query) == reference.search(query)
        before = inspect_index(target)
        source_bytes = (source / "documents.iwl").read_bytes()
        compacted = compact_index(target, expected_generation=2)
        cleanup = vacuum_index(target, expected_generation=3)
        reopened = load_index(target, expected_snapshot=current)
        assert reopened.search("durable") == loaded.search("durable")
        assert old.search("recovery")["total_matches"] == 2
        assert reopened.search("recovery")["total_matches"] == 0
        assert source_bytes == (source / "documents.iwl").read_bytes()
        final = inspect_index(target)
        return {
            "completed": True,
            "workload": "synthetic-segment-lifecycle-v1",
            "source_revision": current.revision,
            "generations": [
                initial["generation"],
                delta["generation"],
                compacted["generation"],
            ],
            "segment_counts": [
                len(initial["segments"]),
                len(delta["segments"]),
                len(compacted["segments"]),
            ],
            "before_compaction_referenced_bytes": before["referenced_bytes"],
            "after_compaction_referenced_bytes": final["referenced_bytes"],
            "removed_files": cleanup["removed_files"],
            "stale_refused": stale_refused,
            "old_view_preserved": True,
            "source_log_unchanged": True,
            "search_matches_rebuilt_oracle": True,
            "index": reopened.metadata(),
            "remaining_ids": sorted(reopened.snapshot.documents),
        }


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True, indent=2))
