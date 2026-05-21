"""Integration contract tests for Create page runtime command preview metadata."""

from __future__ import annotations

import pytest

from api_service.api.routers.workflow_console_view_model import build_runtime_config

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def test_dashboard_boot_payload_exposes_runtime_command_preview_contract() -> None:
    config = build_runtime_config("/workflows/new")

    preview = config["system"]["runtimeCommandPreview"]

    assert preview["capabilityVersion"] == "2026-05-13"
    assert preview["hintCatalogVersion"] == "2026-05-13"
    assert preview["runtimes"]["codex_cli"]["slashCommandPassthrough"] is True
    assert preview["runtimes"]["claude_code"]["slashCommandPassthrough"] is True
    assert preview["runtimes"]["codex_cloud"]["slashCommandPassthrough"] is False
    assert preview["runtimes"]["codex_cloud"]["renderMode"] == "plain_prompt"
    assert preview["knownRuntimeCommandHints"]["review"]["aliases"] == ["/review"]
    assert preview["knownRuntimeCommandHints"]["simplify"]["aliases"] == [
        "/simplify"
    ]
