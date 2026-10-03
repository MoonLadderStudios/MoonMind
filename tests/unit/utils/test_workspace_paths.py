"""Real-filesystem regressions for operations crossing the workspace boundary."""

import os
from pathlib import Path

import pytest

from moonmind.utils.workspace_paths import (
    atomic_write_text,
    chown_tree,
    ensure_directory,
    open_regular_file,
    read_regular_file,
)


def test_atomic_write_survives_parent_swap(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "file"
    protected.write_text("protected")
    original_replace = os.replace

    def race_replace(src, dst, **kwargs):
        parent.rename(tmp_path / "detached")
        parent.symlink_to(outside, target_is_directory=True)
        return original_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", race_replace)
    atomic_write_text(parent / "file", "new content")
    assert protected.read_text() == "protected"
    assert (tmp_path / "detached" / "file").read_text() == "new content"


def test_chown_tree_skips_nested_links(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    child = root / "child"
    child.mkdir()
    (child / "file").write_text("ok")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (child / "escape").symlink_to(outside, target_is_directory=True)
    changed = []

    def fchown(fd, uid, gid):
        changed.append(Path(os.readlink(f"/proc/self/fd/{fd}")))

    def chown(path, uid, gid, *, dir_fd, follow_symlinks):
        assert follow_symlinks is False
        changed.append(Path(os.readlink(f"/proc/self/fd/{dir_fd}")) / path)

    monkeypatch.setattr(os, "fchown", fchown)
    monkeypatch.setattr(os, "chown", chown)
    chown_tree(root, 1000, 1000)
    assert set(changed) == {root, child, child / "file"}


def test_chown_tree_rejects_symlink_ancestor(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "child").mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        chown_tree(link / "child", os.getuid(), os.getgid())


def test_directory_creation_rejects_traversal(tmp_path):
    with pytest.raises(OSError):
        ensure_directory(tmp_path / ".." / "escape")


@pytest.mark.parametrize("kind", ["leaf_link", "parent_link", "fifo", "directory"])
def test_read_rejects_unsafe_workspace_input(tmp_path, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data").write_bytes(b"private")
    root = tmp_path / "workspace"
    if kind == "parent_link":
        root.symlink_to(outside, target_is_directory=True)
    else:
        root.mkdir()
        path = root / "data"
        if kind == "leaf_link":
            path.symlink_to(outside / "data")
        elif kind == "fifo":
            os.mkfifo(path)
        else:
            path.mkdir()
    with pytest.raises(OSError):
        read_regular_file(root / "data", limit=64)


def test_read_bounds_regular_file(tmp_path):
    path = tmp_path / "data"
    path.write_bytes(b"abc")
    assert read_regular_file(path, limit=3) == b"abc"
    with pytest.raises(OSError, match="exceeds"):
        read_regular_file(path, limit=2)


def test_open_file_remains_pinned_across_path_replacement(tmp_path):
    path = tmp_path / "data"
    path.write_bytes(b"allowed")
    outside = tmp_path / "private"
    outside.write_bytes(b"private")
    with open_regular_file(path) as stream:
        path.unlink()
        path.symlink_to(outside)
        assert stream.read() == b"allowed"


def test_read_rejects_negative_bound(tmp_path):
    path = tmp_path / "data"
    path.write_bytes(b"data")
    with pytest.raises(ValueError):
        read_regular_file(path, limit=-2)
