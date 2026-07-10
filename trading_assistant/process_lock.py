"""Singleton lock: prevents two instances of the engine from running
against the same database/exchange account at once.

This is not a hypothetical risk - it already happened, repeatedly, during
this project's own development: independent `run_system.py` processes were
left running (a leftover `scripts/run_forever.sh` supervisor loop, plus a
manually launched second process) and raced each other against the same
SQLite journal and the same testnet exchange account, each holding its own
independent in-memory PortfolioManager state. The result was directly
observed as wild equity swings in the dashboard (one process's view of
"current equity" alternating with the other's every few minutes) and is
also the exact mechanism by which two engines could each decide to open or
close the same real position without knowing about the other.

Uses the OS's own advisory file lock (fcntl.flock on POSIX, msvcrt.locking
on Windows) rather than a PID file: a PID file can go stale (the recorded
PID gets reused by an unrelated process after a crash, or the file is
never cleaned up), and correctly detecting "is that PID still alive and
still my process" cross-platform needs either a third-party dependency or
brittle platform-specific code. An OS-level lock is released automatically
the instant the holding process exits for ANY reason - including a crash
or SIGKILL - so there is no stale-lock case to handle at all.
"""

from __future__ import annotations

import os
import sys
from typing import IO


class AlreadyRunningError(RuntimeError):
    pass


def acquire_singleton_lock(lock_path: str) -> IO:
    """Acquire an exclusive, non-blocking OS lock on `lock_path`, creating
    it if needed. Raises AlreadyRunningError if another process already
    holds it. The returned file handle must be kept alive (assigned to a
    variable that stays in scope) for the life of the process - garbage
    collecting or closing it releases the lock immediately, defeating the
    whole point. Do not open a second handle to the same path even from
    the SAME process; that would be a spurious self-conflict.
    """
    lock_file = open(lock_path, "w")
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        lock_file.close()
        raise AlreadyRunningError(
            f"another instance already holds the lock at {lock_path!r} - refusing to start a second "
            "engine against the same database/exchange account. If you're certain no other instance "
            "is actually running (e.g. the previous process crashed in a way that left the OS lock "
            "held, which is unusual but not impossible on some network filesystems), delete the lock "
            "file and try again."
        ) from exc

    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file
