"""Validate named credential-volume mounts at the API and Docker boundary."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_VOLUME_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*", re.ASCII)


def validate_volume_ref(value: str) -> str:
    """Accept Docker volume names only, never host paths or mount options."""
    if not isinstance(value, str) or not _VOLUME_NAME.fullmatch(value):
        raise ValueError("volume_ref must be a Docker named volume, not a host path")
    return value


def validate_volume_mount_path(value: str) -> str:
    """Require a canonical container directory with no Docker option syntax."""
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or value == "/"
        or any(char in value for char in (":", ",", "\\", "\x00", "\n", "\r"))
        or ".." in PurePosixPath(value).parts
        or str(PurePosixPath(value)) != value
    ):
        raise ValueError("volume_mount_path must be an absolute container directory")
    return value


def credential_volume_mount(
    volume_ref: str, mount_path: str, *, read_only: bool = False
) -> str:
    """Build an explicitly typed named-volume mount, never Docker's bind shorthand."""
    validate_volume_ref(volume_ref)
    validate_volume_mount_path(mount_path)
    return f"type=volume,source={volume_ref},target={mount_path}" + (
        ",readonly" if read_only else ""
    )
