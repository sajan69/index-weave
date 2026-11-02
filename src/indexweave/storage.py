import os
import re
from pathlib import Path
from threading import RLock

import portalocker

from .codec import (
    MAGIC,
    MAX_LOG_BYTES,
    Inspection,
    apply_operations,
    encode_frame,
    operations_payload,
    scan,
)
from .errors import (
    CommitUnknown,
    Conflict,
    Corruption,
    IncompleteTail,
    InvalidInput,
    UnusableWriter,
)
from .files import (
    GATE,
    LEASE,
    LOG,
    acquire,
    gated,
    open_file,
    opened,
    read_log,
    require_platform,
    sync_file,
    workspace,
    write_all,
)
from .model import MAX_REVISION, Delete, Put, Snapshot


def inspect(path: str | Path) -> Inspection:
    with (
        workspace(path) as root,
        gated(root, shared=True),
        opened(root, LOG, os.O_RDONLY) as fd,
    ):
        return scan(read_log(fd))[1]


def read_snapshot(path: str | Path) -> Snapshot:
    with (
        workspace(path) as root,
        gated(root, shared=True),
        opened(root, LOG, os.O_RDONLY) as fd,
    ):
        state, report = scan(read_log(fd))
        if report.tail_bytes:
            raise IncompleteTail(
                "Incomplete trailing frame; inspect before explicit tail repair."
            )
        return state


def repair_tail(
    path: str | Path, *, expected_log_sha256: str, expected_valid_bytes: int
) -> Inspection:
    if (
        not isinstance(expected_log_sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected_log_sha256)
        or type(expected_valid_bytes) is not int
        or expected_valid_bytes < len(MAGIC)
    ):
        raise InvalidInput(
            "Repair requires a log SHA-256 and valid-prefix byte count from inspection."
        )
    with workspace(path) as root, opened(root, LEASE, os.O_RDWR) as lease:
        acquire(lease)
        try:
            with gated(root), opened(root, LOG, os.O_RDWR) as fd:
                _, report = scan(read_log(fd))
                if (
                    report.log_sha256 != expected_log_sha256
                    or report.valid_bytes != expected_valid_bytes
                ):
                    raise Conflict(
                        "Inspection tokens no longer match this log; "
                        "nothing was truncated."
                    )
                if not report.tail_bytes:
                    raise InvalidInput("The log has no incomplete tail to discard.")
                try:
                    os.ftruncate(fd, report.valid_bytes)
                    sync_file(fd)
                except OSError as error:
                    raise CommitUnknown(
                        "Tail repair outcome is unknown; close and inspect again."
                    ) from error
                return scan(read_log(fd))[1]
        finally:
            portalocker.unlock(lease)


class Writer:
    """A lifetime writer lease with per-operation exclusive access gates."""

    def __init__(self, path: str | Path):
        self._mutex = RLock()
        self._closed = True
        self._poisoned = False
        self._root_context = workspace(path)
        self._root = self._root_context.__enter__()
        self._lease = None
        self._log = None
        try:
            self._lease = open_file(self._root, LEASE, os.O_RDWR)
            acquire(self._lease)
            with gated(self._root):
                self._log = open_file(self._root, LOG, os.O_RDWR | os.O_APPEND)
                self._state, report = scan(read_log(self._log))
                if report.tail_bytes:
                    raise IncompleteTail(
                        "Incomplete trailing frame; inspect before "
                        "explicit tail repair."
                    )
                self._log_bytes = report.log_bytes
            self._closed = False
        except BaseException:
            self._release()
            raise

    @classmethod
    def create(cls, path: str | Path):
        require_platform()
        target = Path(path)
        os.mkdir(target, 0o700)
        root = os.open(
            target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        try:
            for name in (LEASE, GATE, LOG):
                fd = os.open(
                    name,
                    os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=root,
                )
                try:
                    if name == LOG:
                        write_all(fd, MAGIC)
                    sync_file(fd)
                finally:
                    os.close(fd)
            sync_file(root)
            parent = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                sync_file(parent)
            finally:
                os.close(parent)
        finally:
            os.close(root)
        return cls(target)

    def _release(self):
        if self._log is not None:
            os.close(self._log)
            self._log = None
        if self._lease is not None:
            os.close(self._lease)
            self._lease = None
        self._root_context.__exit__(None, None, None)

    def _usable(self):
        if self._closed or self._poisoned:
            raise UnusableWriter(
                "Writer is closed or has an unknown write outcome; "
                "reopen after inspection."
            )

    def snapshot(self) -> Snapshot:
        with self._mutex:
            self._usable()
            return self._state

    def write(
        self,
        operations: list[Put | Delete] | tuple[Put | Delete, ...],
        *,
        expected_revision: int,
    ) -> Snapshot:
        with self._mutex:
            self._usable()
            if (
                type(expected_revision) is not int
                or not 0 <= expected_revision < MAX_REVISION
            ):
                raise InvalidInput("Expected revision is outside the supported range.")
            if expected_revision != self._state.revision:
                raise Conflict("Expected revision does not match the current store.")
            payload = operations_payload(operations)
            revision = self._state.revision + 1
            candidate = apply_operations(self._state, operations, revision)
            frame = encode_frame(payload, revision)
            if self._log_bytes + len(frame) > MAX_LOG_BYTES:
                raise InvalidInput(
                    "Appending this commit would exceed the 64 MiB log limit."
                )
            try:
                with gated(self._root):
                    if os.fstat(self._log).st_size != self._log_bytes:
                        self._poisoned = True
                        raise Corruption(
                            self._log_bytes, "log changed outside the writer"
                        )
                    self._poisoned = True
                    write_all(self._log, frame)
                    sync_file(self._log)
            except OSError as error:
                if self._poisoned:
                    raise CommitUnknown(
                        "Commit outcome is unknown; close and inspect "
                        "before deciding whether to retry."
                    ) from error
                raise
            self._state = candidate
            self._log_bytes += len(frame)
            self._poisoned = False
            return candidate

    def close(self):
        with self._mutex:
            if not self._closed:
                self._closed = True
                self._release()

    def __enter__(self):
        with self._mutex:
            self._usable()
        return self

    def __exit__(self, *_):
        self.close()
