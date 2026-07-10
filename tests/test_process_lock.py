import os

import pytest

from trading_assistant.process_lock import AlreadyRunningError, acquire_singleton_lock


def test_acquire_singleton_lock_creates_the_file_with_the_pid(tmp_path):
    """Windows' msvcrt.locking is mandatory (blocks other handles from even
    reading the locked region), unlike POSIX advisory locks - read back
    only after releasing, which is portable across both."""
    lock_path = str(tmp_path / "run_system.lock")

    handle = acquire_singleton_lock(lock_path)
    handle.close()

    with open(lock_path) as f:
        assert f.read().strip() == str(os.getpid())


def test_second_acquire_on_the_same_path_raises_while_the_first_is_held(tmp_path):
    """The core guarantee: a second engine instance must be refused, not
    silently allowed to race the first one against the same database and
    exchange account - the exact failure mode this project's own history
    already produced (multiple run_system.py processes left running,
    diverging in-memory portfolio state, wild equity swings)."""
    lock_path = str(tmp_path / "run_system.lock")
    first = acquire_singleton_lock(lock_path)
    try:
        with pytest.raises(AlreadyRunningError):
            acquire_singleton_lock(lock_path)
    finally:
        first.close()


def test_acquire_succeeds_again_after_the_first_holder_releases(tmp_path):
    """Releasing (closing) the handle must free the lock immediately -
    matters for the crash-restart supervisor: a fresh process launched
    right after a previous one exits must not be spuriously refused."""
    lock_path = str(tmp_path / "run_system.lock")
    first = acquire_singleton_lock(lock_path)
    first.close()

    second = acquire_singleton_lock(lock_path)
    second.close()


def test_error_message_names_the_lock_path(tmp_path):
    lock_path = str(tmp_path / "run_system.lock")
    first = acquire_singleton_lock(lock_path)
    try:
        with pytest.raises(AlreadyRunningError, match="run_system.lock"):
            acquire_singleton_lock(lock_path)
    finally:
        first.close()
