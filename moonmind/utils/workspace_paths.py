"""Descriptor-relative filesystem operations for agent-writable workspaces.

Do not resolve untrusted paths before calling these helpers: every directory
component must be opened without following links, including the supplied root.
"""

from __future__ import annotations

import errno
import os
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


@contextmanager
def open_directory(path: str | Path, *, create: bool = False) -> Iterator[int]:
    """Pin a directory without following links in any path component."""
    path = Path(path).expanduser()
    if ".." in path.parts:
        raise OSError(errno.EINVAL, "parent traversal is not allowed", str(path))
    path = path.absolute()
    fd = os.open(path.anchor, _DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=fd)
                except FileExistsError:
                    # The descriptor open below validates the existing entry.
                    pass
            child_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child_fd
        yield fd
    finally:
        os.close(fd)


def ensure_directory(path: str | Path) -> None:
    """Create a directory tree, rejecting existing symlink components."""
    with open_directory(path, create=True):
        pass


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int = 0o600,
    preserve_existing_owner: bool = False,
) -> None:
    """Replace a file inside a pinned parent, never writing through a link.

    A fresh inode also prevents writes through hardlinks. A concurrent rename
    of the parent or replacement of the destination cannot redirect the write.
    Optional ownership preservation applies to the fresh inode before replacing
    an existing file; the requested mode remains authoritative.
    """
    path = Path(path)
    with open_directory(path.parent, create=True) as parent_fd:
        existing = None
        try:
            existing = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            # A missing destination is the normal first-write path.
            pass
        else:
            if not stat.S_ISREG(existing.st_mode):
                raise OSError(
                    errno.EINVAL, "destination must be a regular file", str(path)
                )
        temporary = f".moonmind-write-{uuid.uuid4().hex}"
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            mode,
            dir_fd=parent_fd,
        )
        try:
            with os.fdopen(fd, "w", encoding=encoding) as output:
                output.write(text)
                if preserve_existing_owner and existing is not None:
                    output.flush()
                    created = os.fstat(output.fileno())
                    if (created.st_uid, created.st_gid) != (
                        existing.st_uid, existing.st_gid
                    ):
                        os.fchown(output.fileno(), existing.st_uid, existing.st_gid)
            os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                # A successful replacement already consumed this temporary name.
                pass


def chown_tree(path: str | Path, uid: int, gid: int) -> None:
    """Transfer directory ownership without following workspace symlinks."""

    def visit(directory_fd: int) -> None:
        # Work from pinned descriptors: an agent may rename any entry during
        # traversal. A swapped symlink is never followed by open or chown.
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    child_fd = os.open(
                        entry.name, _DIRECTORY_FLAGS, dir_fd=directory_fd
                    )
                    try:
                        visit(child_fd)
                    finally:
                        os.close(child_fd)
                else:
                    os.chown(
                        entry.name, uid, gid, dir_fd=directory_fd, follow_symlinks=False
                    )
        os.fchown(directory_fd, uid, gid)

    with open_directory(path) as root_fd:
        visit(root_fd)


@contextmanager
def open_regular_file(path: Path | str) -> Iterator[BinaryIO]:
    """Pin every path component and reject links and special-file endpoints."""
    path = Path(path)
    with open_directory(path.parent) as parent_fd:
        fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError(f"workspace input is not a regular file: {path}")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                yield stream
        finally:
            os.close(fd)


def read_regular_file(path: Path | str, *, limit: int) -> bytes:
    """Read a bounded regular file; oversize data never reaches a consumer."""
    if limit < 0:
        raise ValueError("workspace read limit must be non-negative")
    with open_regular_file(path) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise OSError(f"workspace input exceeds {limit} bytes")
    return data


def append_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Append to a pinned, unshared regular file without following symlinks."""
    path = Path(path)
    with open_directory(path.parent, create=True) as parent_fd:
        fd = os.open(
            path.name,
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
            | os.O_CLOEXEC,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError(
                    errno.EINVAL,
                    "append target must be an unshared regular file",
                    str(path),
                )
            with os.fdopen(fd, "a", encoding=encoding, closefd=False) as output:
                output.write(text)
        finally:
            os.close(fd)
