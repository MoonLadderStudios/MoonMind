"""Unit tests for Codex config enforcement defaults."""

from __future__ import annotations

import toml
import pytest

from api_service.scripts.ensure_codex_config import (
    CodexConfigError,
    ensure_codex_config,
)


def _write_text(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_ensure_codex_config_enforces_network_defaults_and_preserves_custom_keys(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template_path = tmp_path / "template.toml"
    target_path = tmp_path / "codex" / "config.toml"

    _write_text(
        template_path,
        """
approval_policy = "never"
sandbox_mode = "danger-full-access"

[sandbox_workspace_write]
network_access = true
""".strip()
        + "\n",
    )
    _write_text(
        target_path,
        """
approval_policy = "on-request"
sandbox_mode = "read-only"
custom_root = "preserve-me"

[sandbox_workspace_write]
network_access = false
writable_roots = ["/work/repo"]

[sandbox_read_only]
network_access = false
""".strip()
        + "\n",
    )

    monkeypatch.setenv("CODEX_TEMPLATE_PATH", str(template_path))
    result = ensure_codex_config(target_path=target_path)

    assert result.path == target_path
    rendered = toml.load(target_path)
    assert rendered["approval_policy"] == "never"
    assert rendered["sandbox_mode"] == "danger-full-access"
    assert rendered["sandbox_workspace_write"]["network_access"] is True

    # Ensure non-enforced user values survive.
    assert rendered["custom_root"] == "preserve-me"
    assert rendered["sandbox_workspace_write"]["writable_roots"] == ["/work/repo"]
    assert rendered["sandbox_read_only"]["network_access"] is False


def test_ensure_codex_config_replaces_invalid_nested_shape(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    template_path = tmp_path / "template.toml"
    target_path = tmp_path / "codex" / "config.toml"

    _write_text(
        template_path,
        """
approval_policy = "never"
sandbox_mode = "danger-full-access"

[sandbox_workspace_write]
network_access = true
""".strip()
        + "\n",
    )
    _write_text(
        target_path,
        """
sandbox_workspace_write = "invalid"
""".strip()
        + "\n",
    )

    monkeypatch.setenv("CODEX_TEMPLATE_PATH", str(template_path))
    ensure_codex_config(target_path=target_path)
    rendered = toml.load(target_path)

    assert rendered["sandbox_workspace_write"]["network_access"] is True


def test_ensure_codex_config_requires_workspace_network_key(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template_path = tmp_path / "template.toml"
    target_path = tmp_path / "codex" / "config.toml"

    _write_text(
        template_path,
        """
approval_policy = "never"
sandbox_mode = "danger-full-access"
""".strip()
        + "\n",
    )
    monkeypatch.setenv("CODEX_TEMPLATE_PATH", str(template_path))

    with pytest.raises(
        CodexConfigError,
        match="sandbox_workspace_write\\.network_access",
    ):
        ensure_codex_config(target_path=target_path)
