"""Daemon-visible bind-mount adapter for POSIX/Windows/WSL hosts.

The Linux Docker daemon's host bind namespace is not the same as a Windows
drive path (``C:\\repo``) or a WSL user-distro ``/mnt/<drive>`` path. Those
resolve into the Docker Desktop daemon namespace at
``/run/desktop/mnt/host/<drive>/...``. Longer ``/mnt/<name>`` mounts (for
example ``/mnt/data``) are genuine Linux mounts and pass through untouched,
as do already-namespaced Desktop paths. No unconditional string rewriting.
"""
from __future__ import annotations

import os
from pathlib import PurePosixPath

DESKTOP_HOST_MOUNT_ROOT = PurePosixPath("/run/desktop/mnt/host")


class MissingBindSourceError(RuntimeError):
    """A required host bind source does not exist."""


def _is_wsl_distro_path(path: str) -> bool:
    unified = path.replace("\\", "/")
    parts = unified.split("/")
    return (
        len(parts) > 3
        and parts[0] == ""
        and parts[1] == "mnt"
        and len(parts[2]) == 1
        and parts[2].isalpha()
    )


def daemon_visible_host_path(path: str) -> str:
    """Translate Windows/WSL paths into the daemon namespace.

    Returns the daemon-visible path. Inputs that are already daemon-visible
    POSIX paths pass through unchanged; no unconditional rewriting.
    """
    normalized = (path or "").strip()
    if not normalized:
        return normalized
    if normalized.startswith(str(DESKTOP_HOST_MOUNT_ROOT) + "/") or normalized == str(
        DESKTOP_HOST_MOUNT_ROOT
    ):
        return normalized
    if len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isalpha():
        tail = normalized[2:].replace("\\", "/").lstrip("/")
        drive = normalized[0].lower()
        if tail:
            return str(DESKTOP_HOST_MOUNT_ROOT / drive / tail)
        return str(DESKTOP_HOST_MOUNT_ROOT / drive)
    if _is_wsl_distro_path(normalized):
        unified = normalized.replace("\\", "/")
        drive = unified.split("/")[2].lower()
        tail = "/".join(part for part in unified.split("/")[3:] if part)
        if tail:
            return str(DESKTOP_HOST_MOUNT_ROOT / drive / tail)
        return str(DESKTOP_HOST_MOUNT_ROOT / drive)
    return normalized


def resolve_bind_source(source: str, *, required: bool = True) -> str | None:
    """Resolve a host bind source to its daemon-visible form.

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
