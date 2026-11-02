import argparse
import json
import os
import stat
import sys

from . import (
    Delete,
    Document,
    Put,
    Writer,
    __version__,
    inspect,
    read_snapshot,
    repair_tail,
)
from .errors import MissingDocument, StoreError
from .files import require_platform
from .model import MAX_TEXT_BYTES, valid_id


def _text_file(path: str) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Input must be a regular text file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(MAX_TEXT_BYTES + 1)
        if len(data) > MAX_TEXT_BYTES:
            raise ValueError("Input exceeds 64 KiB")
        return data.decode("utf-8", errors="strict")
    finally:
        os.close(fd)


def parser():
    result = argparse.ArgumentParser(
        prog="indexweave",
        description="Revisioned local document storage; no search or AI inference yet.",
    )
    result.add_argument("--version", action="version", version=__version__)
    commands = result.add_subparsers(dest="command", required=True)
    for command in ["create", "inspect", "list", "get", "put", "delete", "repair-tail"]:
        child = commands.add_parser(command)
        child.add_argument("workspace")
        if command in {"get", "put", "delete"}:
            child.add_argument("--id", required=True)
        if command == "put":
            child.add_argument("--title", required=True)
            child.add_argument("--file", required=True)
            child.add_argument("--tag", action="append", default=[])
        if command in {"put", "delete"}:
            child.add_argument("--expected-revision", type=int, required=True)
        if command == "list":
            child.add_argument(
                "--limit", type=int, choices=range(1, 101), default=20, metavar="1..100"
            )
            child.add_argument("--after", default="")
        if command == "repair-tail":
            child.add_argument("--expected-log-sha256", required=True)
            child.add_argument("--expected-valid-bytes", type=int, required=True)
            child.add_argument(
                "--discard-incomplete-tail", action="store_true", required=True
            )
    return result


def execute(args):
    require_platform()
    if args.command == "create":
        with Writer.create(args.workspace) as writer:
            return {"revision": writer.snapshot().revision, "document_count": 0}
    if args.command == "inspect":
        return inspect(args.workspace).to_dict()
    if args.command == "repair-tail":
        return repair_tail(
            args.workspace,
            expected_log_sha256=args.expected_log_sha256,
            expected_valid_bytes=args.expected_valid_bytes,
        ).to_dict()
    if args.command in {"put", "delete"}:
        operation = (
            Put(Document(args.id, args.title, _text_file(args.file), tuple(args.tag)))
            if args.command == "put"
            else Delete(args.id)
        )
        with Writer(args.workspace) as writer:
            snapshot = writer.write(
                [operation], expected_revision=args.expected_revision
            )
            return {
                "revision": snapshot.revision,
                "document_count": len(snapshot.documents),
            }
    snapshot = read_snapshot(args.workspace)
    if args.command == "get":
        record = snapshot.get(args.id)
        if record is None:
            raise MissingDocument("Document was not found.")
        return {
            "store_revision": snapshot.revision,
            "document_revision": record.revision,
            "document": record.document.to_dict(),
        }
    after = valid_id(args.after) if args.after else ""
    keys = sorted(key for key in snapshot.documents if key > after)
    page = keys[: args.limit]
    return {
        "revision": snapshot.revision,
        "documents": [
            {
                "id": key,
                "title": snapshot.documents[key].document.title,
                "tags": list(snapshot.documents[key].document.tags),
                "document_revision": snapshot.documents[key].revision,
            }
            for key in page
        ],
        "next_after": page[-1] if page and len(keys) > len(page) else None,
    }


def main():
    args = parser().parse_args()
    try:
        result = execute(args)
    except StoreError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, UnicodeError):
        print(
            "Storage operation failed. Check paths, permissions and bounded "
            "UTF-8 inputs; existing records were not reset.",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
