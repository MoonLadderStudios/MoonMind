"""Kernel-owned per-stack lock: one writer, no PID/age stealing, no inode deletion."""
from conftest import load

import pytest


def test_second_acquire_fails_while_first_is_held(controller_path, tmp_path):
    lock = load("lock")
    first = lock.StackLock(tmp_path, "moonmind")
    second = lock.StackLock(tmp_path, "moonmind")
    with first.acquire():
        with pytest.raises(lock.LockBusyError):
            with second.acquire():
                pass


def test_lock_released_after_context_exit(controller_path, tmp_path):
    lock = load("lock")
    with lock.StackLock(tmp_path, "moonmind").acquire():
        pass
    with lock.StackLock(tmp_path, "moonmind").acquire():
        pass


def test_lock_rejects_path_traversal_stack_names(controller_path, tmp_path):
    lock = load("lock")
    with pytest.raises(ValueError):
        lock.StackLock(tmp_path, "../escape")


def test_lock_files_are_installation_local(controller_path, tmp_path):
    lock = load("lock")
    first = lock.StackLock(tmp_path / "a", "moonmind")
    second = lock.StackLock(tmp_path / "b", "moonmind")
    assert first.path != second.path
    with first.acquire():
        with second.acquire():
            pass


def test_release_never_deletes_a_live_lock_inode(controller_path, tmp_path):
    lock = load("lock")
    lock_file = tmp_path / "locks" / "moonmind.lock"
    with lock.StackLock(tmp_path, "moonmind").acquire():
        inode_before = lock_file.stat().st_ino
    assert lock_file.stat().st_ino == inode_before
