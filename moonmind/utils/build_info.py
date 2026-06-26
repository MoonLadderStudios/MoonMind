"""Runtime helpers for MoonMind build metadata."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_BUILD_ID_PATH = Path("/app/.moonmind-build-id")

def resolve_moonmind_build_id() -> str | None:
    """Return the operator-visible MoonMind build id.

    Resolution order:
    1. ``MOONMIND_BUILD_ID`` environment variable
    2. Baked image metadata file at ``MOONMIND_BUILD_ID_PATH`` or the default path
    """

    env_build_id = str(os.environ.get("MOONMIND_BUILD_ID", "")).strip()
    if env_build_id:
        return env_build_id

    build_id_path = Path(
        str(os.environ.get("MOONMIND_BUILD_ID_PATH", "")).strip()
        or DEFAULT_BUILD_ID_PATH
    )
    try:
        file_build_id = build_id_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return file_build_id or None
