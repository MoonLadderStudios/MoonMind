"""Integration-style boundary tests for Claude policy-envelope contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.schemas import ClaudePolicySource, resolve_claude_policy_envelope

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


NOW = datetime(2026, 4, 16, tzinfo=UTC)


def _source(
    source_kind: str,
    settings: dict[str, object],
    *,
    fetch_state: str = "fetched",
    risky_controls: tuple[str, ...] = (),
) -> ClaudePolicySource:
    return ClaudePolicySource(
        sourceKind=source_kind,
        settings=settings,
        fetchState=fetch_state,
        riskyControls=risky_controls,
    )


def test_claude_policy_boundary_covers_mm343_fixture_scenarios() -> None:
    scenarios = [
        (
            "server-managed",
            (
                _source("server_managed", {"permissions": {"mode": "plan"}}),
                _source(
                    "endpoint_managed",
                    {"permissions": {"mode": "bypassPermissions"}},
                ),
            ),
            "server_managed",
            "ready",
        ),
        (
            "endpoint-managed",
            (
                _source("server_managed", {}),
                _source("endpoint_managed", {"permissions": {"mode": "default"}}),
            ),
            "endpoint_managed",
            "ready",
        ),
        (
            "cache-hit",
            (
                _source(
                    "server_managed",
                    {"permissions": {"mode": "default"}},
                    fetch_state="cache_hit",
                ),
            ),
            "server_managed",
            "ready",
        ),
        (
            "fetch-failed",
            (
                _source(
                    "server_managed",
                    {"permissions": {"mode": "default"}},
                    fetch_state="fetch_failed",
                ),
            ),
            "server_managed",
            "ready",
        ),
        (
            "security-dialog",
            (
                _source(
                    "server_managed",
                    {"hooks": {"allowManagedOnly": True}},
                    risky_controls=("managed_hooks",),
                ),
            ),
            "server_managed",
            "security_dialog_required",
        ),
        (
            "bootstrap-template",
            (
                _source(
                    "server_managed",
                    {
                        "bootstrapPreferences": [
                            {"name": "default-style", "value": "concise"}
                        ]
                    },
                ),
            ),
            "server_managed",
            "ready",
        ),
    ]

    for label, sources, managed_source, handshake_state in scenarios:
        envelope, handshake, events = resolve_claude_policy_envelope(
            session_id=f"claude-{label}",
            policy_envelope_id=f"policy-{label}",
            provider_mode="anthropic_api",
            sources=sources,
            interactive=label == "security-dialog",
            occurred_at=NOW,
        )

        assert envelope is not None, label
        assert envelope.managed_source_kind == managed_source, label
        assert envelope.provider_mode == "anthropic_api", label
        assert envelope.policy_trust_level in {
            "server_managed_best_effort",
            "endpoint_enforced",
        }
        assert envelope.version == 1, label
        assert handshake.state == handshake_state, label
        assert events[-1].event_type in {
            "policy.version.changed",
            "policy.dialog.required",
        }

        wire = envelope.model_dump(by_alias=True)
        assert wire["policyEnvelopeId"] == f"policy-{label}"
        assert wire["sessionId"] == f"claude-{label}"
        assert "policy_envelope_id" not in wire
        assert wire["adminVisibility"]["managedSourceKind"] == managed_source
        assert wire["userVisibility"]["status"] in {"managed", "unmanaged"}


def test_claude_policy_boundary_fail_closed_and_non_interactive_blocked_states() -> None:
    envelope, handshake, events = resolve_claude_policy_envelope(
        session_id="claude-fail-closed",
        policy_envelope_id="policy-fail-closed",
        provider_mode="anthropic_api",
        sources=(
            _source(
                "server_managed",
                {"permissions": {"mode": "plan"}},
                fetch_state="fail_closed",
            ),
        ),
        fail_closed_on_refresh_failure=True,
        occurred_at=NOW,
    )

    assert envelope is None
    assert handshake.model_dump(by_alias=True)["state"] == "fail_closed"
    assert events[-1].event_type == "policy.fetch.failed"

    envelope, handshake, _events = resolve_claude_policy_envelope(
        session_id="claude-blocked",
        policy_envelope_id="policy-blocked",
        provider_mode="anthropic_api",
        sources=(
            _source(
                "server_managed",
                {"hooks": {"allowManagedOnly": True}},
                risky_controls=("managed_hooks",),
            ),
        ),
        interactive=False,
        occurred_at=NOW,
    )

    assert envelope is not None
    assert envelope.security_dialog_required is True
    assert handshake.state == "blocked"
