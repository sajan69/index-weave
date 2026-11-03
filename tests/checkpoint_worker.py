"""Crash points use only the test's newly created private destination."""

import os
import signal
import stat
import sys
from unittest.mock import patch

from indexweave import compact_store
from indexweave.checkpoint import MAGIC_V2

mode, source, destination = sys.argv[1:]
real_sync = os.fsync


def write(fd, data):
    if data.startswith(MAGIC_V2):
        if mode == "before":
            os._exit(31)
        os.write(fd, data[:70])
        os._exit(32)
    raise AssertionError("Unexpected payload")


def sync(fd):
    real_sync(fd)
    info = os.fstat(fd)
    if stat.S_ISREG(info.st_mode) and info.st_size > 8:
        os.kill(os.getpid(), signal.SIGKILL)


with (
    patch("indexweave.storage.sync_file", side_effect=sync)
    if mode == "synced"
    else patch("indexweave.storage.write_all", side_effect=write)
):
    compact_store(source, destination, expected_revision=2)
