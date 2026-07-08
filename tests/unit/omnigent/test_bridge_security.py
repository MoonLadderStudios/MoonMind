"""MM-1154 tests for the OmnigentBridge §16 auth-topology primitives.

Source issue traceability: MM-1140 -> MM-1154.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.bridge_security import (
    BridgeSessionBinding,
    OmnigentAuthorizationError,
    assert_bridge_session_binding,
    authorize_bridge_access,
    enforce_id_only_labels,
    redact_raw_events,
    sanitize_proxy_headers,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


def _request(**overrides: object) -> AgentExecutionRequest:
    payload = {
        "agentKind": "external",
        "agentId": "omnigent",
        "correlationId": "corr-1",
        "idempotencyKey": "idem-1",
    }
    payload.update(overrides)
    return AgentExecutionRequest(**payload)


# --- authorization (§16 rule 1) -------------------------------------------------


def test_authorize_bridge_access_falls_back_to_correlation_identity() -> None:
    context = authorize_bridge_access(_request())

    assert context.principal == "corr-1"
    assert context.workflow_id == "corr-1"
    assert context.agent_run_id == "corr-1"
    assert context.bridge_session_key == "idem-1"
    assert context.correlation_id == "corr-1"


def test_authorize_bridge_access_uses_step_execution_identity() -> None:
    request = _request(
        stepExecution={
            "schemaVersion": "v1",
            "workflowId": "mm:wf-42",
            "runId": "run-7",
            "logicalStepId": "logical-1",
            "executionOrdinal": 1,
            "stepExecutionId": "mm:wf-42:run-7:logical-1:execution:1",
            "runtimeContextPolicy": "fresh_agent_run",
        }
    )

    context = authorize_bridge_access(request)

    assert context.principal == "mm:wf-42"
    assert context.workflow_id == "mm:wf-42"
    assert context.agent_run_id == "run-7"
    assert context.bridge_session_key == "idem-1"


def test_assert_bridge_session_binding_allows_matching_owner() -> None:
    context = authorize_bridge_access(_request())

    # No existing durable row, and a matching binding, both authorize.
    assert_bridge_session_binding(context, None)
    assert_bridge_session_binding(
        context,
        BridgeSessionBinding(workflow_id="corr-1", agent_run_id="corr-1"),
    )


def test_assert_bridge_session_binding_rejects_cross_owner_reuse() -> None:
    context = authorize_bridge_access(_request())

    with pytest.raises(OmnigentAuthorizationError) as exc:
        assert_bridge_session_binding(
            context,
            BridgeSessionBinding(
                workflow_id="other-workflow", agent_run_id="corr-1"
            ),
        )

    assert exc.value.failure_class == "user_error"
    assert "cross-owner" in str(exc.value)


# --- id-only labels (§16 rule 4) ------------------------------------------------


def test_enforce_id_only_labels_keeps_moonmind_ids() -> None:
    labels = {
        "moonmind.correlation_id": "corr-1",
        "moonmind.idempotency_key": "idem-1",
        "moonmind.issue": "MM-1154",
    }

    assert enforce_id_only_labels(labels) == labels


def test_enforce_id_only_labels_rejects_secret_like_key() -> None:
    with pytest.raises(OmnigentAuthorizationError) as exc:
        enforce_id_only_labels({"omnigent.api_token": "value"})

    assert exc.value.failure_class == "user_error"


@pytest.mark.parametrize(
    "key",
    [
        "session_secret",
        "runnerCredential",
        "authorization",
        "x.bearer.marker",
        "api.key",
        "runner.auth",
    ],
)
def test_enforce_id_only_labels_rejects_various_secret_keys(key: str) -> None:
    with pytest.raises(OmnigentAuthorizationError):
        enforce_id_only_labels({key: "value"})


def test_enforce_id_only_labels_rejects_secret_like_value() -> None:
    with pytest.raises(OmnigentAuthorizationError):
        enforce_id_only_labels(
            {"moonmind.note": "Authorization: Bearer sk-abcdef0123456789"}
        )


def test_enforce_id_only_labels_rejects_github_token_value() -> None:
    with pytest.raises(OmnigentAuthorizationError):
        enforce_id_only_labels(
            {"moonmind.note": "ghp_0123456789abcdefghijABCDEFGHIJ012345"}
        )


def test_enforce_id_only_labels_rejects_raw_provider_token_value() -> None:
    with pytest.raises(OmnigentAuthorizationError):
        enforce_id_only_labels(
            {"moonmind.note": "sk-proj-0123456789abcdefghijklmnop"}
        )


# --- proxy header non-leakage (§16 rule 7) --------------------------------------


def test_sanitize_proxy_headers_drops_moonmind_internal_auth_headers() -> None:
    forwarded = sanitize_proxy_headers(
        {
            "Authorization": "Bearer moonmind-user-token",
            "Cookie": "session=abc",
            "X-MoonMind-Auth": "internal",
            "X-Api-Key": "internal-key",
            "Accept": "application/json",
            "X-Trace-Id": "trace-1",
        }
    )

    assert forwarded == {"Accept": "application/json", "X-Trace-Id": "trace-1"}


def test_sanitize_proxy_headers_drops_sensitive_by_fragment() -> None:
    forwarded = sanitize_proxy_headers(
        {
            "X-Runner-Secret": "value",
            "X_Api_Key": "value",
            "X-Api_Key": "value",
        }
    )

    assert forwarded == {}


def test_sanitize_proxy_headers_honors_explicit_allowlist() -> None:
    forwarded = sanitize_proxy_headers(
        {"Authorization": "Bearer configured", "Cookie": "c=1"},
        allowed_upstream_headers=["authorization"],
    )

    assert forwarded == {"Authorization": "Bearer configured"}


# --- raw-event redaction (§16 rule 5) -------------------------------------------


def test_redact_raw_events_scrubs_secret_like_fields() -> None:
    events = [
        {"type": "response.delta", "data": {"text": "editing"}},
        {"type": "host.capabilities", "api_token": "sk-should-be-hidden"},
    ]

    redacted = redact_raw_events(events)

    assert redacted[0] == {"type": "response.delta", "data": {"text": "editing"}}
    assert redacted[1]["type"] == "host.capabilities"
    assert redacted[1]["api_token"] == "[REDACTED]"


def test_redact_raw_events_scrubs_authorization_header_text() -> None:
    events = [{"type": "system", "message": "Authorization: Bearer secret-value"}]

    redacted = redact_raw_events(events)

    assert "secret-value" not in redacted[0]["message"]


def test_redact_raw_events_scrubs_bare_provider_token_text() -> None:
    events = [{"type": "system", "message": "provider said sk-proj-0123456789abc"}]

    redacted = redact_raw_events(events)

    assert "sk-proj-0123456789abc" not in redacted[0]["message"]
    assert "[REDACTED]" in redacted[0]["message"]
