"""A reproducible synthetic example; never opens a caller's workspace."""

import json
import tempfile
from pathlib import Path

from indexweave import (
    Delete,
    Document,
    Put,
    Writer,
    inspect,
    read_snapshot,
    repair_tail,
)
from indexweave.codec import encode_frame, operations_payload
from indexweave.errors import IncompleteTail
from indexweave.files import LOG


def demonstrate():
    with tempfile.TemporaryDirectory(prefix="indexweave-example-") as tmp:
        path = Path(tmp) / "store"
        with Writer.create(path) as writer:
            before = writer.write(
                [
                    Put(Document("guide", "Guide", "First version", ("example",))),
                    Put(Document("draft", "Draft", "Temporary note")),
                ],
                expected_revision=0,
            )
            writer.write(
                [
                    Put(Document("guide", "Guide", "Revised version", ("example",))),
                    Delete("draft"),
                ],
                expected_revision=1,
            )
            assert before.get("guide").document.text == "First version"
            assert before.get("draft") is not None
            assert read_snapshot(path).get("guide").revision == 2
        complete = inspect(path)
        interrupted = encode_frame(
            operations_payload(
                [Put(Document("third", "Interrupted", "Uncommitted example"))]
            ),
            3,
        )
        with (path / LOG).open("ab") as stream:
            stream.write(interrupted[:70])
        damaged = inspect(path)
        try:
            read_snapshot(path)
        except IncompleteTail:
            pass
        else:
            raise AssertionError("Strict reader accepted an incomplete tail")
        repaired = repair_tail(
            path,
            expected_log_sha256=damaged.log_sha256,
            expected_valid_bytes=damaged.valid_bytes,
        )
        state = read_snapshot(path)
        assert state.revision == 2 and set(state.documents) == {"guide"}
        assert complete == repaired
        return {
            "workload": "synthetic-storage-roundtrip-v1",
            "initial_documents": len(before.documents),
            "initial_revision": before.revision,
            "final_documents": len(state.documents),
            "final_revision": state.revision,
            "remaining_ids": sorted(state.documents),
            "prior_snapshot_unchanged": before.get("guide").document.text
            == "First version",
            "injected_tail_bytes": damaged.tail_bytes,
            "strict_reader_refused_tail": True,
            "repaired_log_sha256": repaired.log_sha256,
            "repaired_log_bytes": repaired.log_bytes,
            "completed": True,
        }


if __name__ == "__main__":
    print(json.dumps(demonstrate(), sort_keys=True))
