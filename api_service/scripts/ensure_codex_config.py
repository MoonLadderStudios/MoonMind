"""Ensure the Codex config enforces a non-interactive approval policy.

This script merges the baked template at ``/etc/codex/config.toml`` into the
current user's ``~/.codex/config.toml`` while preserving any additional
settings. Only the ``approval_policy`` key is enforced. The merge is idempotent
and exits with a non-zero status if the template or resulting config is
invalid so container start-up fails fast.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

import toml

TEMPLATE_ENV_VAR = "CODEX_TEMPLATE_PATH"
TARGET_HOME_ENV_VAR = "CODEX_CONFIG_HOME"
DEFAULT_TEMPLATE_PATH = Path("/etc/codex/config.toml")
CONFIG_SUBDIR = ".codex"
CONFIG_FILENAME = "config.toml"
REQUIRED_KEY = "approval_policy"


class CodexConfigError(RuntimeError):
    """Raised when the Codex configuration cannot be enforced."""


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


def ensure_codex_config() -> Path:
    template_path = Path(os.environ.get(TEMPLATE_ENV_VAR, DEFAULT_TEMPLATE_PATH))
    if not template_path.exists():
        raise CodexConfigError(f"Codex template missing at {template_path}")

    template_config = _load_toml(template_path)
    if REQUIRED_KEY not in template_config:
        raise CodexConfigError(
            f"Codex template at {template_path} must define {REQUIRED_KEY!r}"
        )

    home_dir = Path(
        os.environ.get(TARGET_HOME_ENV_VAR)
        or os.environ.get("HOME")
        or Path.home()
    )
    target_dir = home_dir / CONFIG_SUBDIR
    target_path = target_dir / CONFIG_FILENAME

    target_dir.mkdir(parents=True, exist_ok=True)

    existing_config = _load_toml(target_path)
    merged_config: Dict[str, Any] = dict(existing_config)
    merged_config[REQUIRED_KEY] = template_config[REQUIRED_KEY]

    rendered = _ensure_trailing_newline(toml.dumps(merged_config))

    # Avoid rewriting the file if nothing changed to reduce inode churn.
    if target_path.exists() and target_path.read_text(encoding="utf-8") == rendered:
        return target_path

    target_path.write_text(rendered, encoding="utf-8")
    target_path.chmod(0o600)
    return target_path


def main() -> int:
    try:
        target = ensure_codex_config()
    except CodexConfigError as exc:
        print(f"[codex-config] enforcement failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - unexpected failure path
        print(
            f"[codex-config] unexpected error: {exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"[codex-config] approval policy enforced at {target}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    sys.exit(main())

