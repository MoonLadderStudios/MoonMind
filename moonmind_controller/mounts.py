"""Daemon-visible bind-mount adapter for the standalone controller.

Stdlib-only. Bind sources are derived from the selected daemon's actual
mounts. Windows drive paths and WSL user-distro ``/mnt/<drive>`` paths map
into the Docker Desktop daemon namespace
(``/run/desktop/mnt/host/<drive>/...``) only through this explicit adapter;
nothing here rewrites paths unconditionally. A missing required host source
raises instead of becoming an empty auto-created bind directory.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

DESKTOP_HOST_MOUNT_ROOT = PurePosixPath("/run/desktop/mnt/host")


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


def desktop_host_path(path: str) -> str | None:
    """Map a Windows/WSL path into the Desktop daemon namespace, else None.

    Returns ``None`` for genuine Linux mounts (including longer
    ``/mnt/<name>`` mounts such as ``/mnt/data``), which pass through
    untouched.
    """
    normalized = (path or "").strip()
    if not normalized:
        return None
    # Windows drive-letter path, e.g. ``C:\\repo`` or ``C:/repo``.
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
    return None


def resolve_bind_source(path: str, *, is_desktop_daemon: bool | None) -> str:
    """Resolve the bind source for the selected daemon without guessing.

    Desktop paths are translated only when the daemon is positively Desktop.
    When the daemon is unreachable (``None``), the Desktop spelling is kept
    so a misclassified source fails loudly instead of mounting an empty
    directory. Non-Windows paths always pass through.
    """
    translated = desktop_host_path(path)
    if translated is None:
        return path
    if is_desktop_daemon is False:
        return path
    return translated


def require_host_source(path: str, *, exists: bool) -> str:
    """Return ``path`` when its host source exists; fail loudly otherwise."""
    if not exists:
        raise FileNotFoundError(
            f"Required host source is missing: {path}; refusing to create "
            "an empty bind directory over it."
        )
    return path


def windows_path_to_posix(path: str) -> str:
    """Render a Windows path with forward slashes for daemon comparison."""
    return PureWindowsPath(path).as_posix()
