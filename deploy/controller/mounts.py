"""Client-resolvable bind-mount adapter for POSIX/Windows/WSL hosts.

A Windows drive path (``C:\\repo``) is not a Linux path; it resolves into
the Docker Desktop daemon namespace at ``/run/desktop/mnt/host/<drive>/...``.
A WSL user-distro ``/mnt/<drive>`` path keeps its distro spelling: Docker
Desktop resolves a distro client's bind sources in that distro (containers
the distro creates inherit this through their socket mount), where
``/run/desktop/mnt/host`` does not exist and would become an empty directory.
Other POSIX paths, including longer ``/mnt/<name>`` mounts such as
``/mnt/data`` and already-namespaced Desktop paths, pass through untouched.
No unconditional string rewriting.
"""
from __future__ import annotations

import os
from pathlib import PurePosixPath

DESKTOP_HOST_MOUNT_ROOT = PurePosixPath("/run/desktop/mnt/host")
# Where a WSL user distro mounts Windows drives.
WSL_DRIVE_ROOT = PurePosixPath("/mnt")


class MissingBindSourceError(RuntimeError):
    """A required host bind source does not exist."""


def daemon_visible_host_path(path: str) -> str:
    """Translate a Windows drive path into the Desktop daemon namespace.

    Every POSIX path, including a WSL distro's ``/mnt/<drive>`` path, passes
    through unchanged; no unconditional rewriting.
    """
    normalized = (path or "").strip()
    if not normalized:
        return normalized
    if len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isalpha():
        tail = normalized[2:].replace("\\", "/").lstrip("/")
        drive = normalized[0].lower()
        if tail:
            return str(DESKTOP_HOST_MOUNT_ROOT / drive / tail)
        return str(DESKTOP_HOST_MOUNT_ROOT / drive)
    return normalized


def wsl_distro_host_path(path: str) -> str:
    """Spell a Desktop daemon-namespace path the way this WSL client sees it.

    ``/run/desktop/mnt/host/<drive>/<tail>`` becomes ``/mnt/<drive>/<tail>``
    when this process sees that path, because its Docker client then
    resolves binds in this distro. A client without the drive mount (for
    example a raw-daemon container) keeps the daemon-namespace spelling.
    """
    try:
        relative = PurePosixPath(path).relative_to(DESKTOP_HOST_MOUNT_ROOT)
    except ValueError:
        return path
    local = WSL_DRIVE_ROOT / relative
    if relative.parts and os.path.exists(local):
        return str(local)
    return path


def resolve_bind_source(source: str, *, required: bool = True) -> str | None:
    """Resolve a host bind source to the form the Docker client resolves.

    Raises :class:`MissingBindSourceError` for a missing required source so a
    missing host path can never become an empty auto-created bind directory.
    """
    translated = daemon_visible_host_path(source)
    if translated != source.strip():
        # Cross-namespace translation: the local host cannot stat the daemon
        # mount, so return the translation and let the daemon enforce it.
        return translated
    if os.path.exists(translated):
        return translated
    if required:
        raise MissingBindSourceError(
            f"Required host bind source is missing: {source!r}; "
            "refusing to let Compose auto-create an empty directory."
        )
    return None
