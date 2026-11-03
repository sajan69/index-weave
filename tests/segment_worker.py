"""Subprocess crash points affect only the test's synthetic derived index."""

import os
import signal
import sys
from unittest.mock import patch

from indexweave import index_files as io
from indexweave import read_snapshot
from indexweave.errors import Busy, Conflict
from indexweave.segments import refresh_index

mode, target, source = sys.argv[1:]
if mode in {"hold-reader", "hold-writer"}:
    with io.session(target, write=mode == "hold-writer"):
        print("READY", flush=True)
        sys.stdin.readline()
elif mode == "race-refresh":
    print("READY", flush=True)
    sys.stdin.readline()
    try:
        refresh_index(target, read_snapshot(source), expected_generation=1)
    except (Busy, Conflict):
        print("REFUSED", flush=True)
    else:
        print("PUBLISHED", flush=True)
else:
    real_replace = os.replace

    def crash(*args, **kwargs):
        if mode == "before-replace":
            os._exit(31)
        real_replace(*args, **kwargs)
        os.kill(os.getpid(), signal.SIGKILL)

    with patch("indexweave.index_files.os.replace", side_effect=crash):
        refresh_index(target, read_snapshot(source), expected_generation=1)
