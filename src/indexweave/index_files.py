"""Private derived-index directory operations and publication ordering."""

import os
import re
import stat
from contextlib import contextmanager

from .errors import InvalidInput, InvalidWorkspace
from .files import (
    GATE,
    LEASE,
    acquire,
    check_file,
    gated,
    opened,
    require_platform,
    write_all,
)
from .segment_codec import MAX_MANIFEST_BYTES, MAX_SEGMENT_BYTES, require

CURRENT = "CURRENT"
PENDING = "CURRENT.pending"
SEGMENT_NAME = re.compile(r"seg-[0-9a-f]{64}\.iws\Z")
MAX_DIRECTORY_BYTES = 128 * 1024 * 1024
MAX_FILES = 64


def inventory(root):
    names = os.listdir(root)
    require(len(names) <= MAX_FILES)
    sizes = {}
    for name in names:
        require(name in {CURRENT, PENDING, LEASE, GATE} or SEGMENT_NAME.fullmatch(name))
        info = os.stat(name, dir_fd=root, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise InvalidWorkspace(
                "Derived index files must be private owned regular files."
            )
        maximum = (
            MAX_SEGMENT_BYTES if SEGMENT_NAME.fullmatch(name) else MAX_MANIFEST_BYTES
        )
        require(info.st_size <= maximum)
        if name in {LEASE, GATE}:
            require(info.st_size == 0)
        sizes[name] = info.st_size
    require(sum(sizes.values()) <= MAX_DIRECTORY_BYTES)
    require({LEASE, GATE} <= set(names))
    return sizes


@contextmanager
def directory(path):
    require_platform()
    root = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(root)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise InvalidWorkspace("Derived index directory must be private and owned.")
        inventory(root)
        yield root
    finally:
        os.close(root)


@contextmanager
def session(path, *, write=False):
    with directory(path) as root:
        if write:
            with opened(root, LEASE, os.O_RDWR) as lease:
                acquire(lease)
                with gated(root):
                    inventory(root)
                    yield root
        else:
            with gated(root, shared=True):
                inventory(root)
                yield root


def read(root, name, maximum):
    with opened(root, name, os.O_RDONLY) as fd:
        before = os.fstat(fd)
        require(before.st_size <= maximum)
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, min(65536, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            require(total <= maximum)
            chunks.append(chunk)
        after = os.fstat(fd)
        require(
            total == before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
        )
        return b"".join(chunks)


def write_new(root, name, data):
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=root,
    )
    try:
        check_file(fd)
        write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(root, segment_name, segment_data, manifest_data):
    sizes = inventory(root)
    require(PENDING not in sizes)
    extra_files = int(segment_name not in sizes) + 1 + int(CURRENT not in sizes)
    extra_bytes = (0 if segment_name in sizes else len(segment_data)) + len(
        manifest_data
    )
    if (
        len(sizes) + extra_files > MAX_FILES
        or sum(sizes.values()) + extra_bytes > MAX_DIRECTORY_BYTES
    ):
        raise InvalidInput(
            "Derived index directory budget exhausted; inspect and vacuum."
        )
    if segment_name in sizes:
        require(read(root, segment_name, MAX_SEGMENT_BYTES) == segment_data)
    else:
        write_new(root, segment_name, segment_data)
    os.fsync(root)  # Make the segment durable before publishing its reference.
    write_new(root, PENDING, manifest_data)
    os.replace(PENDING, CURRENT, src_dir_fd=root, dst_dir_fd=root)
    os.fsync(root)


def initialize(path):
    from pathlib import Path

    require_platform()
    path = Path(path)
    os.mkdir(
        path, 0o700
    )  # Existing paths are never reset, including a source workspace.
    root = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for name in [LEASE, GATE]:
            write_new(root, name, b"")
        os.fsync(root)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        os.close(root)
