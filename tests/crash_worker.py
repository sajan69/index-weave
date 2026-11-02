"""Test-only abrupt child exits on private synthetic workspaces."""

import os
import signal
import sys
from unittest.mock import patch

from indexweave import Document, Put, Writer


def main():
    path, mode = sys.argv[1:]
    with Writer(path) as writer:
        if mode == "synced":

            def stop_after_sync(fd):
                os.fsync(fd)
                os.kill(os.getpid(), signal.SIGKILL)

            target = "indexweave.storage.sync_file"
            action = stop_after_sync
        else:

            def stop_during_write(fd, value):
                if mode == "partial":
                    os.write(fd, value[:70])
                os._exit(73)

            target = "indexweave.storage.write_all"
            action = stop_during_write
        with patch(target, side_effect=action):
            writer.write(
                [Put(Document("crash", "Crash fixture", "Synthetic body"))],
                expected_revision=0,
            )


if __name__ == "__main__":
    main()
