"""MoonLadderStudios/MoonMind#4356 R4: hermetic protected-ingress matrix (integration).

Accurate scope: proxy-header sanitization primitives (what MoonMind
forwards upstream to the Omnigent server via ``sanitize_proxy_headers``)
plus real ingress refusals at owned boundaries (container capability
verification with forged material, blank WebSocket token refused before
any store, provider-secret redaction before persistence).

This matrix pins the admission primitives hermetically in the integration
lane with synthetic fixtures only. A full operator-facing ASGI/deployed
transport ingress journey against built artifacts (exposed backend route,
hostile-origin acceptance, ingress credential bypass over SSE/WebSocket)
remains cohort-owned and is explicitly unverified here rather than
claimed through the outbound proxy helper.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.bridge_security import (
    redact_raw_events,
    sanitize_proxy_headers,
)
from moonmind.security.container_job_capabilities import (
    ContainerJobCapabilityError,
    verify_container_job_session_capability,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def test_sse_shaped_request_drops_forged_origin_and_credentials() -> None:
    inbound = {
        "Accept": "text/event-stream",
        "Origin": "https://evil-4356.example",
        "Authorization": "Bearer forged-operator",
        "Cookie": "session=forged",
        "X-Request-Id": "req-4356",
    }
    forwarded = sanitize_proxy_headers(
        inbound, allowed_upstream_headers=["Accept", "X-Request-Id"]
    )
    assert forwarded == {"Accept": "text/event-stream", "X-Request-Id": "req-4356"}
    assert "Origin" not in forwarded
    assert "Authorization" not in forwarded


def test_websocket_shaped_request_drops_forged_host_and_forwarding() -> None:
    inbound = {
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Host": "evil-4356.example",
        "X-Forwarded-Host": "evil-4356.example",
        "X-Forwarded-For": "10.9.9.9",
        "Forwarded": "for=10.9.9.9;host=evil-4356.example",
        "Sec-WebSocket-Key": "forged-key-4356",
        "X-Request-Id": "req-4356",
    }
    forwarded = sanitize_proxy_headers(
        inbound,
        allowed_upstream_headers=[
            "Upgrade",
            "Host",
            "X-Request-Id",
            "Sec-WebSocket-Key",
        ],
    )
    # Host/routing material never forwards even when allowlisted; the
    # WebSocket protocol key is an end-to-end handshake header and forwards
    # only under an explicit allowlist entry (caller-controlled credential
    # headers such as Authorization/Cookie are still dropped by default).
    assert "Host" not in forwarded
    assert "host" not in {key.lower() for key in forwarded}
    assert forwarded == {
        "X-Request-Id": "req-4356",
        "Sec-WebSocket-Key": "forged-key-4356",
    }


def test_direct_container_bypass_token_fails_verification() -> None:
    """Foreign-secret machine material never grants operator access."""
    with pytest.raises(ContainerJobCapabilityError):
        verify_container_job_session_capability(
            "forged.invalid", secret="test-secret-4356", now=100
        )


@pytest.mark.asyncio
async def test_websocket_blank_token_refused_before_store_access() -> None:
    from fastapi import HTTPException

    from api_service.api.websockets import get_current_user_ws

    for blank in ("", "   "):
        with pytest.raises(HTTPException) as exc:
            await get_current_user_ws(blank, None, None)
        assert exc.value.status_code == 401
        assert exc.value.detail == {"code": "auth_required"}


def test_nested_provider_secret_redacted_before_persistence() -> None:
    synthetic = "sk-live-synthetic-4356-nested-abcdef"
    events = [{"nested": {"token": synthetic}, "list": [synthetic]}]
    (redacted,) = redact_raw_events(events)
    assert synthetic not in str(redacted)
