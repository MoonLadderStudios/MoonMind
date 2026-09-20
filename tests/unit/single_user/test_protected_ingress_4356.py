"""MoonLadderStudios/MoonMind#4356 R4: synthetic protected-ingress matrix (hermetic).

Exercises the real admission boundaries against forged and bypass attempts:
proxy-header sanitization drops caller-controlled credential headers, the
bridge authorizer fails closed on missing identity, cross-owner session
reuse is refused, invalid machine credentials never verify, and raw
provider events are redacted before persistence.

MFA preservation is asserted as an ingress/cutover contract only (no new
MoonMind account implementation). Transport-level SSE/WebSocket/forged-Host
coverage against built artifacts belongs to the integrated-candidate
evidence run; this matrix proves the in-process admission primitives
hermetically with synthetic fixtures.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.omnigent.bridge_security import (
    OmnigentAuthorizationError,
    assert_bridge_session_binding,
    authorize_bridge_access,
    BridgeSessionBinding,
    enforce_id_only_labels,
    redact_raw_events,
    sanitize_proxy_headers,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.security.container_job_capabilities import (
    ContainerJobCapabilityError,
    verify_container_job_session_capability,
)


def _request(**overrides) -> AgentExecutionRequest:
    base = {
        "agent_kind": "managed",
        "agent_id": "agent-4356",
        "correlation_id": "corr-4356",
        "idempotency_key": "bridge-4356",
    }
    base.update(overrides)
    return AgentExecutionRequest(**base)


def test_forged_credential_headers_never_forwarded_upstream() -> None:
    """Caller-controlled auth/credential headers are dropped, not forwarded."""
    forged = {
        "Authorization": "Bearer forged-operator",
        "Cookie": "session=forged",
        "X-Api-Key": "forged-key",
        "X-Moonmind-Auth": "forged",
        "X-Forwarded-Authorization": "forged",
        "X-Custom-Token": "forged",
        "Content-Type": "application/json",
    }
    assert sanitize_proxy_headers(forged) == {}


def test_allowlisted_end_to_end_header_forwards_without_credentials() -> None:
    """An explicit non-credential allowlist entry still forwards."""
    forwarded = sanitize_proxy_headers(
        {"X-Request-Id": "req-4356", "Authorization": "Bearer forged"},
        allowed_upstream_headers=["X-Request-Id"],
    )
    assert forwarded == {"X-Request-Id": "req-4356"}


def test_bridge_authorizer_fails_closed_on_missing_identity() -> None:
    """Missing correlation/idempotency identity denies before provider calls."""
    # Blank identity fails closed at the schema boundary or the authorizer;
    # either denial precedes any provider call.
    with pytest.raises((OmnigentAuthorizationError, ValidationError)):
        authorize_bridge_access(_request(correlation_id="   "))
    with pytest.raises((OmnigentAuthorizationError, ValidationError)):
        authorize_bridge_access(_request(idempotency_key="   "))


def test_bridge_session_reuse_across_owners_refused() -> None:
    """An idempotency-key session bound to one workflow rejects another owner."""
    context = authorize_bridge_access(_request())
    with pytest.raises(OmnigentAuthorizationError, match="different MoonMind"):
        assert_bridge_session_binding(
            context,
            BridgeSessionBinding(workflow_id="other-workflow", agent_run_id="other-run"),
        )
    # Same-owner reuse is admitted.
    assert_bridge_session_binding(
        context,
        BridgeSessionBinding(
            workflow_id=context.workflow_id, agent_run_id=context.agent_run_id
        ),
    )


def test_secret_like_session_labels_rejected() -> None:
    """Session labels carry ids only; secret-like keys/values fail closed."""
    with pytest.raises(OmnigentAuthorizationError):
        enforce_id_only_labels({"api_key": "not-a-secret-just-a-key-name"})
    with pytest.raises(OmnigentAuthorizationError):
        enforce_id_only_labels({"owner": "sk-live-synthetic-4356-abcdef"})
    assert enforce_id_only_labels({"moonmind.idempotency_key": "bridge-4356"}) == {
        "moonmind.idempotency_key": "bridge-4356"
    }


def test_invalid_machine_credential_never_grants_operator_access() -> None:
    """An invalid/foreign machine token fails verification (no fallback)."""
    with pytest.raises(ContainerJobCapabilityError):
        verify_container_job_session_capability(
            "forged.invalid", secret="test-secret-4356", now=100
        )


def test_raw_provider_events_redacted_before_persistence() -> None:
    """Credential-shaped provider tokens never reach stored artifacts."""
    events = [{"text": "call with sk-live-synthetic-4356-abcdef done"}]
    (redacted,) = redact_raw_events(events)
    assert "sk-live-synthetic-4356-abcdef" not in str(redacted)


@pytest.mark.asyncio
async def test_websocket_blank_token_refused_before_store_access() -> None:
    """An empty WebSocket token is denied at the transport without a store.

    Exercises the real terminal-socket principal boundary
    (api_service.api.websockets.get_current_user_ws): ``db=None`` proves
    the refusal happens before any account/revocation store is touched.
    """
    from fastapi import HTTPException

    from api_service.api.websockets import get_current_user_ws

    for blank in ("", "   "):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_ws(blank, None, None)
        assert exc.value.status_code == 401
        assert exc.value.detail == {"code": "auth_required"}


def test_host_header_never_forwarded_even_when_allowlisted() -> None:
    """A forged Host header cannot be smuggled upstream via the allowlist.

    Exercises the real proxy-sanitization boundary: ``Host`` is a routing
    header, so even an explicit allowlist entry must not forward it --
    direct container/host-gateway bypass material stays inside the hop.
    """
    forged = {"Host": "evil-4356.example", "X-Request-Id": "req-4356"}
    forwarded = sanitize_proxy_headers(
        forged, allowed_upstream_headers=["Host", "X-Request-Id"]
    )
    assert "Host" not in forwarded
    assert "host" not in {key.lower() for key in forwarded}
    assert forwarded == {"X-Request-Id": "req-4356"}


def test_forged_routing_and_identity_headers_dropped_by_default() -> None:
    """Spoofed forwarding/identity headers never forward without allowlist."""
    forged = {
        "X-Forwarded-Host": "evil-4356.example",
        "X-Forwarded-For": "10.9.9.9",
        "Forwarded": "for=10.9.9.9;host=evil-4356.example",
        "X-Forwarded-Authorization": "Bearer forged",
        "Origin": "https://evil-4356.example",
    }
    assert sanitize_proxy_headers(forged) == {}


def test_nested_provider_secret_redacted_before_persistence() -> None:
    """Credential-shaped values at nesting depth never reach stored events."""
    synthetic = "sk-live-synthetic-4356-nested-abcdef"
    events = [{"nested": {"token": synthetic}, "list": [synthetic]}]
    (redacted,) = redact_raw_events(events)
    assert synthetic not in str(redacted)
