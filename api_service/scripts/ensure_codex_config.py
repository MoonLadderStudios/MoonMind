"""Ensure the Codex config enforces managed runtime defaults.

This script merges the baked template at ``/etc/codex/config.toml`` into the
desired Codex configuration target while preserving additional settings.
Managed defaults are enforced for:

- ``approval_policy``
- ``sandbox_mode``
- ``sandbox_workspace_write.network_access``

The merge is idempotent and exits with a non-zero status if the template or
resulting config is invalid so container start-up fails fast.
"""

from __future__ import annotations

import os
import stat
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import toml

TEMPLATE_ENV_VAR = "CODEX_TEMPLATE_PATH"
TARGET_HOME_ENV_VAR = "CODEX_CONFIG_HOME"
TARGET_PATH_ENV_VAR = "CODEX_CONFIG_PATH"
DEFAULT_TEMPLATE_PATH = Path("/etc/codex/config.toml")
CONFIG_SUBDIR = ".codex"
CONFIG_FILENAME = "config.toml"
REQUIRED_TEMPLATE_PATHS: tuple[tuple[str, ...], ...] = (
    ("approval_policy",),
    ("sandbox_mode",),
    ("sandbox_workspace_write", "network_access"),
)


class CodexConfigError(RuntimeError):
    """Raised when the Codex configuration cannot be enforced."""


def _format_path(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _read_required_path(
    config: dict[str, Any],
    *,
    template_path: Path,
    key_path: tuple[str, ...],
) -> Any:
    cursor: Any = config
    for key in key_path:
        if not isinstance(cursor, dict) or key not in cursor:
            raise CodexConfigError(
                f"Codex template at {template_path} must define "
                f"{_format_path(key_path)!r}"
            )
        cursor = cursor[key]
    return deepcopy(cursor)


def _set_nested_path(
    config: dict[str, Any],
    *,
    key_path: tuple[str, ...],
    value: Any,
) -> None:
    cursor: dict[str, Any] = config
    for key in key_path[:-1]:
        current = cursor.get(key)
        if not isinstance(current, dict):
            current = {}
            cursor[key] = current
        cursor = current
    cursor[key_path[-1]] = value


def _load_toml(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return toml.load(handle)
    except FileNotFoundError:
        return {}
    except toml.TomlDecodeError as exc:  # pragma: no cover - defensive branch
        raise CodexConfigError(f"Invalid TOML content in {path}: {exc}") from exc


def _ensure_trailing_newline(content: str) -> str:
    return content if content.endswith("\n") else f"{content}\n"


def _enforce_shared_file_permissions(path: Path) -> None:
    """Ensure config permissions are compatible with shared root/non-root workers."""

    target_mode = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP
    if os.geteuid() == 0:
        try:
            parent_gid = path.parent.stat().st_gid
            os.chown(path, -1, parent_gid)
        except OSError as exc:
            raise CodexConfigError(
                f"Failed to align group ownership for {path}: {exc}"
            ) from exc
    try:
        os.chmod(path, target_mode)
    except OSError as exc:
        raise CodexConfigError(f"Failed to set file mode for {path}: {exc}") from exc


def _write_secure_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` with strict owner-write/group-read permissions."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    finally:
        _enforce_shared_file_permissions(path)


@dataclass(frozen=True)
class CodexConfigResult:
    """Represents the enforced Codex configuration."""

    path: Path
    config: Dict[str, Any]


def ensure_codex_config(
    *, target_path: Optional[Path] = None, target_home: Optional[Path] = None
) -> CodexConfigResult:
    template_path = Path(os.environ.get(TEMPLATE_ENV_VAR, DEFAULT_TEMPLATE_PATH))
    if not template_path.exists():
        raise CodexConfigError(f"Codex template missing at {template_path}")

    template_config = _load_toml(template_path)
    required_values: dict[tuple[str, ...], Any] = {
        key_path: _read_required_path(
            template_config,
            template_path=template_path,
            key_path=key_path,
        )
        for key_path in REQUIRED_TEMPLATE_PATHS
    }

    env_target_path = os.environ.get(TARGET_PATH_ENV_VAR)
    if env_target_path and target_path is None:
        target_path = Path(env_target_path)

    resolved_home = target_home or Path(
        os.environ.get(TARGET_HOME_ENV_VAR) or os.environ.get("HOME") or Path.home()
    )

    if target_path is None:
        target_dir = resolved_home / CONFIG_SUBDIR
        target_path = target_dir / CONFIG_FILENAME
    else:
        target_dir = target_path.parent

    target_dir.mkdir(parents=True, exist_ok=True)

    existing_config = _load_toml(target_path)
    merged_config: Dict[str, Any] = dict(existing_config)
    for key_path, value in required_values.items():
        _set_nested_path(
            merged_config,
            key_path=key_path,
            value=deepcopy(value),
        )

    rendered = _ensure_trailing_newline(toml.dumps(merged_config))

    # Avoid rewriting the file if nothing changed to reduce inode churn.
    if target_path.exists() and target_path.read_text(encoding="utf-8") == rendered:
        _enforce_shared_file_permissions(target_path)
        return CodexConfigResult(target_path, merged_config)

    _write_secure_text(target_path, rendered)
    return CodexConfigResult(target_path, merged_config)


def main() -> int:
    try:
        result = ensure_codex_config()
    except CodexConfigError as exc:
        print(f"[codex-config] enforcement failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - unexpected failure path
        print(
            f"[codex-config] unexpected error: {exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"[codex-config] required settings enforced at {result.path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    sys.exit(main())
