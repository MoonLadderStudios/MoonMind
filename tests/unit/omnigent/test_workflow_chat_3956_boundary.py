"""MoonLadderStudios/MoonMind#3956: upstream-native boundary composition pin.

The facade, compat map, outbound scan, and terminal-evidence behaviors each have
dedicated suites. This module pins their #3956 composition in one hermetic
place: unknown routes/transports fail closed before any mutation, terminal
state denies mutation authority, changed-content retries cannot reuse a scan
allowance, secret blocks redact values, and the `embedded=1` presentation flag
stays distinct from the experimental embedded-host transport (#3955).
"""

from __future__ import annotations

import pytest

from moonmind.omnigent import native_ui_compat as compat
from moonmind.omnigent.native_outbound_scan import (
    NativeScanBlockedError,
    NativeScanSurface,
    canonical_payload_digest,
    scan_native_outbound,
)
from moonmind.omnigent.workflow_chat_facade import (
    CODE_ROUTE_NOT_ALLOWLISTED,
    CODE_SESSION_READ_ONLY,
    is_read_only,
    match_facade_operation,
    recompute_capabilities,
)


def test_unknown_route_and_unknown_transport_fail_closed() -> None:
    assert match_facade_operation("GET", "v1/sessions/x/admin/backdoor") is None
    assert match_facade_operation("DELETE", "v1/sessions") is None
    assert compat.classify_native_ui_http("GET", "v1/sessions/x/admin/backdoor") is None
    assert compat.classify_native_ui_websocket("v1/global/secret-stream") is None
    assert CODE_ROUTE_NOT_ALLOWLISTED == "omnigent_chat_route_not_allowlisted"


def test_unsupported_websocket_subprotocol_is_rejected() -> None:
    with pytest.raises(Exception):
        compat.negotiate_ws_subprotocol(["evil-protocol"])


def test_terminal_state_denies_mutation_capability() -> None:
    assert is_read_only("completed")
    assert is_read_only("failed")
    assert is_read_only("stopped")
    assert not is_read_only("active")
    caps = recompute_capabilities(status="completed")
    assert caps["sendMessage"] is False
    assert caps["interruptTurn"] is False
    assert caps["viewTranscript"] is True
    assert CODE_SESSION_READ_ONLY == "omnigent_chat_session_read_only"


def _text_body(text: str) -> dict:
    return {
        "type": "message",
        "data": {"content": [{"type": "text", "text": text}]},
    }


def test_changed_content_retry_cannot_reuse_scan_allowance() -> None:
    first = _text_body("hello world")
    changed = _text_body("hello world!")
    assert canonical_payload_digest(first) != canonical_payload_digest(changed)
    allow = scan_native_outbound(
        surface=NativeScanSurface.MESSAGE,
        body=first,
        high_security_mode=False,
    )
    assert allow.allowed is True
    # The allow evidence is digest-bound to the exact first payload.
    assert allow.payload_digest == canonical_payload_digest(first)
    assert allow.payload_digest != canonical_payload_digest(changed)


def test_secret_block_redacts_values_and_reports_location_only() -> None:
    body = _text_body("deploy with ghp_" + "a" * 36 + " now")
    with pytest.raises(NativeScanBlockedError) as excinfo:
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE,
            body=body,
            high_security_mode=True,
        )
    metadata = excinfo.value.evidence.audit_metadata()
    dumped = str(metadata)
    assert "ghp_" not in dumped
    assert "findingLocations" in metadata or "findingCategories" in metadata
    assert excinfo.value.evidence.allowed is False


def test_embedded_presentation_flag_is_not_embedded_host_transport() -> None:
    # `embedded=1` is a presentation query flag on the MoonMind-scoped chatUrl
    # (docs/UI/WorkflowChatPanel.md §4); the experimental embedded-host channel
    # in #3955 is a separate transport negotiated elsewhere. The compat map must
    # not contain a route keyed on the presentation flag.
    cmap = compat.compatibility_map()
    serialized = str(cmap)
    assert "embedded=1" not in serialized
