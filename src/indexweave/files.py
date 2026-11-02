"""Private local-file access and advisory coordination."""

import os
import stat
from contextlib import contextmanager
from pathlib import Path

import portalocker

from .codec import MAX_LOG_BYTES
from .errors import Busy, Corruption, InvalidWorkspace

LOG = "documents.iwl"
LEASE = "writer.lock"
GATE = "access.lock"
FILES = {LOG, LEASE, GATE}


def require_platform():
    required = ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK", "O_CLOEXEC", "geteuid")
    if (
        os.name != "posix"
        or not all(hasattr(os, name) for name in required)
        or os.open not in os.supports_dir_fd
        or os.listdir not in os.supports_fd
    ):
        raise InvalidWorkspace(
            "This storage version requires POSIX local-file semantics."
        )


def check_file(fd: int):
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        raise InvalidWorkspace(
            "Workspace files must be private, owned, single-link regular files."
        )


def open_file(root_fd: int, name: str, flags: int) -> int:
    fd = os.open(
        name, flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=root_fd
    )
    try:
        check_file(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextmanager
def workspace(path: str | Path):
    require_platform()
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise InvalidWorkspace(
                "The workspace must be private and owned by this user."
            )
        if set(os.listdir(fd)) != FILES:
            raise InvalidWorkspace(
                "The workspace does not contain exactly the version 1 storage files."
            )
        yield fd
    finally:
        os.close(fd)


@contextmanager
def opened(root_fd: int, name: str, flags: int):
    fd = open_file(root_fd, name, flags)
    try:
        yield fd
    finally:
        os.close(fd)


def acquire(fd: int, *, shared: bool = False):
    flags = (
        portalocker.LOCK_SH if shared else portalocker.LOCK_EX
    ) | portalocker.LOCK_NB
    try:
        portalocker.lock(fd, flags)
    except portalocker.exceptions.LockException as error:
        raise Busy("Workspace is busy; no operation was applied.") from error


@contextmanager
def gated(root_fd: int, *, shared: bool = False):
    with opened(root_fd, GATE, os.O_RDONLY if shared else os.O_RDWR) as fd:
        acquire(fd, shared=shared)
        try:
            yield
        finally:
            portalocker.unlock(fd)


def read_log(fd: int) -> bytes:
    before = os.fstat(fd)
    if before.st_size > MAX_LOG_BYTES:
        raise Corruption(0, "log exceeds 64 MiB")
    os.lseek(fd, 0, os.SEEK_SET)
    parts = []
    total = 0
    while True:
        chunk = os.read(fd, min(65536, MAX_LOG_BYTES + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_LOG_BYTES:
            raise Corruption(0, "log exceeds 64 MiB")
        parts.append(chunk)
    after = os.fstat(fd)
    if (
        before.st_size != after.st_size
        or total != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise Corruption(0, "log changed during read")
    return b"".join(parts)


def write_all(fd: int, value: bytes):
    view = memoryview(value)
    while view:
        try:
            count = os.write(fd, view)
        except InterruptedError:
            continue
        if count <= 0:
            raise OSError("Write made no progress")
        view = view[count:]


def sync_file(fd: int):
    os.fsync(fd)
