"""Unit tests for Jira auth resolution."""

from __future__ import annotations

import pytest

from moonmind.config.settings import AtlassianSettings, JiraSettings
from moonmind.integrations.jira.auth import resolve_jira_connection
from moonmind.integrations.jira.errors import JiraToolError

pytestmark = [pytest.mark.asyncio]

def _build_settings(
    *,
    jira: JiraSettings | None = None,
    **overrides: object,
) -> AtlassianSettings:
    return AtlassianSettings(
        jira=jira or JiraSettings(jira_tool_enabled=True),
        **overrides,
    )

async def test_resolve_service_account_connection_from_secret_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_values = {
        "env://jira-auth-mode": "service_account_scoped",
        "env://jira-api-key": "token-123",
        "env://jira-cloud-id": "cloud-abc",
        "env://jira-service-email": "svc@example.com",
    }

    async def _fake_resolve(ref: str) -> str:
        return secret_values[ref]

    monkeypatch.setattr(
        "moonmind.integrations.jira.auth.resolve_managed_api_key_reference",
        _fake_resolve,
    )

    settings = _build_settings(
        atlassian_auth_mode_secret_ref="env://jira-auth-mode",
        atlassian_api_key_secret_ref="env://jira-api-key",
        atlassian_cloud_id_secret_ref="env://jira-cloud-id",
        atlassian_service_account_email_secret_ref="env://jira-service-email",
        jira=JiraSettings(
            jira_tool_enabled=True,
            jira_connect_timeout_seconds=5.0,
            jira_read_timeout_seconds=20.0,
            jira_retry_attempts=2,
        ),
    )

    connection = await resolve_jira_connection(settings)

    assert connection.auth_mode == "service_account_scoped"
    assert (
        connection.base_url
        == "https://api.atlassian.com/ex/jira/cloud-abc/rest/api/3"
    )
    assert connection.headers["Authorization"] == "Bearer token-123"
    assert connection.connect_timeout_seconds == 5.0
    assert connection.read_timeout_seconds == 20.0
    assert connection.retry_attempts == 2
    assert "token-123" in connection.redaction_values

async def test_resolve_basic_connection_from_raw_values() -> None:
    settings = _build_settings(
        atlassian_auth_mode="basic",
        atlassian_api_key="token-456",
        atlassian_email="bot@example.com",
        atlassian_site_url="https://https://example.atlassian.net/",
    )

    connection = await resolve_jira_connection(settings)

    assert connection.auth_mode == "basic"
    assert connection.base_url == "https://example.atlassian.net/rest/api/3"
    assert connection.headers["Authorization"].startswith("Basic ")
    assert "bot@example.com:token-456" in connection.redaction_values

async def test_secret_ref_resolution_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_resolve(ref: str) -> str:
        raise ValueError(f"secret resolution failed for {ref}: token-999")

    monkeypatch.setattr(
        "moonmind.integrations.jira.auth.resolve_managed_api_key_reference",
        _fake_resolve,
    )

    settings = _build_settings(
        atlassian_auth_mode_secret_ref="env://jira-auth-mode",
    )

    with pytest.raises(JiraToolError) as excinfo:
        await resolve_jira_connection(settings)

    assert excinfo.value.code == "jira_not_configured"
    assert "auth_mode" in str(excinfo.value)
    assert "token-999" not in str(excinfo.value)
