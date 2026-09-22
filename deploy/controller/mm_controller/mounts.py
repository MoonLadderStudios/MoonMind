"""Daemon-visible bind-mount adapter (POSIX / Windows / WSL).

Carried forward from the ``deployment_execution`` mount helpers without
importing MoonMind code. Rules:

* Bind sources derive from the selected daemon's actual mounts (evidence),
  never from unconditional string rewriting of the configured path.
* ``/run/desktop/mnt/host/<drive>/...`` is supported where applicable.
* A missing required host source must fail loudly; it must never become an
  empty auto-created bind directory (``create_host_path: false``).
* Project name, profiles, ``.env``, overrides, ports, ingress, secrets,
  stable volumes, and externally attached sandbox/job workloads are preserved
  by the caller; orphan cleanup here is scoped so it cannot claim those
  workloads. Stdlib only.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

DOCKER_DESKTOP_HOST_MOUNT_ROOT = PurePosixPath("/run/desktop/mnt/host")

_WSL_DISTRO_PATH = re.compile(r"^/mnt/[A-Za-z](?:/.*)?$")


def is_wsl_distro_path(path: str) -> bool:
    """True for WSL user-distro ``/mnt/<drive>`` paths only.

    Longer ``/mnt/<name>`` mounts (e.g. ``/mnt/data``) are genuine Linux
    mounts and keep their POSIX namespace.
    """
    return _WSL_DISTRO_PATH.match(path.strip().replace("\\", "/")) is not None


def docker_desktop_host_path(path: str) -> str | None:
    """Translate Windows / WSL paths into Docker Desktop's daemon namespace.

    Windows drive paths (``C:\\repo``) and WSL ``/mnt/<drive>`` paths resolve
    to ``/run/desktop/mnt/host/<drive>/...``. Other paths pass through
    (return None = no rewrite).
    """
    normalized = path.strip()
    if len(normalized) >= 2 and normalized[1] == ":" and normalized[0].isalpha():
        tail = normalized[2:].replace("\\", "/").lstrip("/")
        drive = normalized[0].lower()
        if tail:
            return str(DOCKER_DESKTOP_HOST_MOUNT_ROOT / drive / tail)
        return str(DOCKER_DESKTOP_HOST_MOUNT_ROOT / drive)
    if is_wsl_distro_path(normalized):
        unified = normalized.replace("\\", "/")
        drive = unified.split("/")[2].lower()
        tail = "/".join(part for part in unified.split("/")[3:] if part)
        if tail:
            return str(DOCKER_DESKTOP_HOST_MOUNT_ROOT / drive / tail)
        return str(DOCKER_DESKTOP_HOST_MOUNT_ROOT / drive)
    return None


def normalize_bind_path(path: str) -> str:
    text = str(path or "").strip().replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    return text.rstrip("/") or "/"


def resolve_bind_source(
    *,
    configured: str,
    daemon_evidence: str | None,
    desktop_daemon: bool | None = None,
) -> str:
    """Resolve the bind source Compose receives.

    ``daemon_evidence`` is the daemon-recorded host source for the same mount
    (proven resolvable); it always wins when present so Compose keeps the
    deployment's existing spelling family. Otherwise Windows drive-letter
    paths rewrite unconditionally (a Linux Compose client cannot use them),
    WSL ``/mnt/<drive>`` paths rewrite only on a confirmed Desktop daemon
    (a confirmed non-Desktop daemon keeps POSIX), and unknown platforms
    rewrite so a misclassified source fails loudly instead of mounting an
    empty directory.
    """
    if daemon_evidence and daemon_evidence.strip():
        return daemon_evidence
    text = configured.strip()
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        rewritten = docker_desktop_host_path(text)
        return rewritten or configured
    if is_wsl_distro_path(text):
        if desktop_daemon is False:
            return configured
        rewritten = docker_desktop_host_path(text)
        return rewritten or configured
    return configured


def bind_spec(*, source: str, target: str, readonly: bool = False) -> dict:
    """Build a long-form bind spec that never auto-creates host paths."""
    return {
        "type": "bind",
        "source": source,
        "target": target,
        "read_only": readonly,
        "bind": {"create_host_path": False},
    }


def orphan_cleanup_scoped(*, project_name: str, protected_labels: tuple[str, ...] = ()) -> dict:
    """Describe scoped orphan cleanup: only ``--remove-orphans`` for the owned
    project; externally attached sandbox/job workloads (labels/volumes given
    in ``protected_labels``) are never claimed. No global prune."""
    return {
        "projectName": project_name,
        "removeOrphans": True,
        "globalPrune": False,
        "protectedLabels": list(protected_labels),
    }
