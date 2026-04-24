"""Unit tests for Claude policy-envelope contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.managed_session_models import (
    ClaudePolicyEnvelope,
    ClaudePolicyEvent,
    ClaudePolicyHandshake,
    ClaudePolicySource,
    resolve_claude_policy_envelope,
)

NOW = datetime(2026, 4, 16, tzinfo=UTC)

def _source(
    source_kind: str,
    *,
    settings: dict[str, object] | None = None,
    fetch_state: str = "fetched",
    supported: bool = True,
    risky_controls: tuple[str, ...] = (),
    version: str | None = None,
) -> ClaudePolicySource:
    return ClaudePolicySource(
        sourceKind=source_kind,
        settings=settings or {},
        fetchState=fetch_state,
        supported=supported,
        riskyControls=risky_controls,
        version=version,
    )

def _resolve(
    *sources: ClaudePolicySource,
    interactive: bool = False,
    fail_closed_on_refresh_failure: bool = False,
) -> tuple[ClaudePolicyEnvelope | None, ClaudePolicyHandshake, tuple[ClaudePolicyEvent, ...]]:
    return resolve_claude_policy_envelope(
        session_id="claude-session-1",
        policy_envelope_id="policy-1",
        provider_mode="anthropic_api",
        sources=sources,
        version=1,
        interactive=interactive,
        fail_closed_on_refresh_failure=fail_closed_on_refresh_failure,
        occurred_at=NOW,
    )

def test_server_managed_non_empty_settings_win_over_endpoint_managed() -> None:
    envelope, handshake, events = _resolve(
        _source(
            "server_managed",
            settings={"permissions": {"mode": "plan"}},
            version="server-v1",
        ),
        _source(
            "endpoint_managed",
            settings={"permissions": {"mode": "bypassPermissions"}},
            version="endpoint-v1",
        ),
    )

    assert envelope is not None
    assert envelope.managed_source_kind == "server_managed"
    assert envelope.managed_source_version == "server-v1"
    assert envelope.permissions.mode == "plan"
    assert handshake.state == "ready"
    assert tuple(event.event_type for event in events) == (
        "policy.fetch.started",
        "policy.fetch.succeeded",
        "policy.compiled",
        "policy.version.changed",
    )

def test_endpoint_managed_applies_when_server_managed_empty_or_unsupported() -> None:
    envelope, handshake, _events = _resolve(
        _source("server_managed", settings={}, version="server-empty"),
        _source(
            "endpoint_managed",
            settings={"permissions": {"mode": "acceptEdits"}},
            version="endpoint-v1",
        ),
    )

    assert envelope is not None
    assert envelope.managed_source_kind == "endpoint_managed"
    assert envelope.managed_source_version == "endpoint-v1"
    assert envelope.permissions.mode == "acceptEdits"
    assert handshake.state == "ready"

    envelope, _handshake, _events = _resolve(
        _source(
            "server_managed",
            settings={"permissions": {"mode": "plan"}},
            supported=False,
            version="unsupported-server",
        ),
        _source(
            "endpoint_managed",
            settings={"permissions": {"mode": "default"}},
            version="endpoint-v2",
        ),
    )

    assert envelope is not None
    assert envelope.managed_source_kind == "endpoint_managed"
    assert envelope.permissions.mode == "default"

def test_lower_scope_sources_are_observability_only() -> None:
    envelope, _handshake, _events = _resolve(
        _source(
            "server_managed",
            settings={"permissions": {"mode": "plan"}},
            version="server-v1",
        ),
        _source(
            "local_project",
            settings={"permissions": {"mode": "bypassPermissions"}},
            version="local-v1",
        ),
        _source(
            "user",
            settings={"permissions": {"mode": "dontAsk"}},
            version="user-v1",
        ),
    )

    assert envelope is not None
    assert envelope.permissions.mode == "plan"
    assert envelope.observability_sources == ("local_project", "user")
    assert envelope.admin_visibility["observabilitySources"] == [
        "local_project",
        "user",
    ]

def test_fail_closed_refresh_failure_blocks_startup_without_permissive_envelope() -> None:
    envelope, handshake, events = _resolve(
        _source(
            "server_managed",
            settings={"permissions": {"mode": "plan"}},
            fetch_state="fail_closed",
        ),
        fail_closed_on_refresh_failure=True,
    )

    assert envelope is None
    assert handshake.state == "fail_closed"
    assert handshake.policy_envelope_id is None
    assert "fail-closed" in (handshake.reason or "")
    assert tuple(event.event_type for event in events) == (
        "policy.fetch.started",
        "policy.fetch.failed",
    )

def test_lower_scope_fail_closed_source_cannot_block_managed_policy() -> None:
    envelope, handshake, events = _resolve(
        _source(
            "server_managed",
            settings={"permissions": {"mode": "plan"}},
            version="server-v1",
        ),
        _source(
            "local_project",
            settings={"permissions": {"mode": "bypassPermissions"}},
            fetch_state="fail_closed",
            version="local-v1",
        ),
        fail_closed_on_refresh_failure=True,
    )

    assert envelope is not None
    assert envelope.managed_source_kind == "server_managed"
    assert envelope.policy_fetch_state == "fetched"
    assert envelope.permissions.mode == "plan"
    assert envelope.observability_sources == ("local_project",)
    assert handshake.state == "ready"
    assert tuple(event.event_type for event in events) == (
        "policy.fetch.started",
        "policy.fetch.succeeded",
        "policy.compiled",
        "policy.version.changed",
    )

def test_endpoint_fail_closed_does_not_override_non_empty_server_policy() -> None:
    envelope, handshake, _events = _resolve(
        _source(
            "server_managed",
            settings={"permissions": {"mode": "plan"}},
            version="server-v1",
        ),
        _source(
            "endpoint_managed",
            settings={"permissions": {"mode": "default"}},
            fetch_state="fail_closed",
            version="endpoint-v1",
        ),
        fail_closed_on_refresh_failure=True,
    )

    assert envelope is not None
    assert envelope.managed_source_kind == "server_managed"
    assert envelope.permissions.mode == "plan"
    assert handshake.state == "ready"

def test_endpoint_fail_closed_blocks_when_server_policy_is_empty() -> None:
    envelope, handshake, events = _resolve(
        _source("server_managed", settings={}, version="server-empty"),
        _source(
            "endpoint_managed",
            settings={},
            fetch_state="fail_closed",
            version="endpoint-v1",
        ),
        fail_closed_on_refresh_failure=True,
    )

    assert envelope is None
    assert handshake.state == "fail_closed"
    assert events[-1].metadata == {
        "fetchState": "fail_closed",
        "sourceKind": "endpoint_managed",
    }

def test_fetch_failed_without_fail_closed_preserves_fetch_state() -> None:
    envelope, handshake, _events = _resolve(
        _source(
            "server_managed",
            settings={"permissions": {"mode": "default"}},
            fetch_state="fetch_failed",
        ),
        fail_closed_on_refresh_failure=False,
    )

    assert envelope is not None
    assert envelope.policy_fetch_state == "fetch_failed"
    assert handshake.state == "ready"

def test_interactive_risky_managed_controls_require_security_dialog() -> None:
    envelope, handshake, events = _resolve(
        _source(
            "server_managed",
            settings={"hooks": {"allowManagedOnly": True}},
            risky_controls=("managed_hooks", "managed_environment"),
        ),
        interactive=True,
    )

    assert envelope is not None
    assert envelope.security_dialog_required is True
    assert handshake.state == "security_dialog_required"
    assert any(event.event_type == "policy.dialog.required" for event in events)

def test_non_interactive_risky_managed_controls_are_blocked() -> None:
    envelope, handshake, _events = _resolve(
        _source(
            "server_managed",
            settings={"hooks": {"allowManagedOnly": True}},
            risky_controls=("managed_hooks",),
        ),
        interactive=False,
    )

    assert envelope is not None
    assert envelope.security_dialog_required is True
    assert handshake.state == "blocked"
    assert "non-interactive" in (handshake.reason or "")

def test_bootstrap_preferences_are_templates_not_managed_defaults() -> None:
    envelope, _handshake, _events = _resolve(
        _source(
            "server_managed",
            settings={
                "bootstrapPreferences": [
                    {"name": "default-output-style", "value": "concise"}
                ],
                "managedDefaults": {"theme": "dark"},
            },
        )
    )

    assert envelope is not None
    assert envelope.bootstrap_templates[0].kind == "bootstrap_template"
    assert envelope.bootstrap_templates[0].name == "default-output-style"
    assert "managedDefaults" not in envelope.effective_settings

def test_envelope_records_provider_trust_visibility_and_version_metadata() -> None:
    envelope, _handshake, _events = _resolve(
        _source("server_managed", settings={"permissions": {"mode": "default"}})
    )

    assert envelope is not None
    assert envelope.provider_mode == "anthropic_api"
    assert envelope.policy_trust_level == "server_managed_best_effort"
    assert envelope.policy_fetch_state == "fetched"
    assert envelope.version == 1
    assert envelope.admin_visibility["policyTrustLevel"] == "server_managed_best_effort"
    assert envelope.user_visibility == {"status": "managed"}

def test_policy_payloads_reject_unsupported_values() -> None:
    with pytest.raises(ValidationError):
        ClaudePolicyEnvelope(
            policyEnvelopeId="policy-1",
            sessionId="claude-session-1",
            providerMode="unknown",
            managedSourceKind="server_managed",
            policyFetchState="fetched",
            policyTrustLevel="server_managed_best_effort",
            version=1,
        )
