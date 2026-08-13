"""Unit tests for the versioned native Omnigent outbound-scan contract.

MoonLadderStudios/MoonMind#3637: the binding-scoped facade must preserve the
canonical ``MOONMIND_HIGH_SECURITY_MODE`` outbound-scan guarantee for native
messages and commands. These tests exercise the runtime-neutral extractor /
normalizer in isolation from the FastAPI router.
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.native_outbound_scan import (
    NATIVE_OUTBOUND_SCAN_CONTRACT_VERSION,
    NativeScanBlockedError,
    NativeScanEnforcementError,
    NativeScanOutcome,
    NativeScanSurface,
    canonical_payload_digest,
    scan_native_outbound,
)
from moonmind.security import OUTBOUND_SCAN_POLICY_REF

_SECRET_TEXT = "ghp_" + "a" * 36
_CLEAN_MESSAGE = {
    "type": "message",
    "data": {"content": [{"type": "text", "text": "hello there"}]},
}


def _secret_message() -> dict:
    return {
        "type": "message",
        "data": {"content": [{"type": "text", "text": _SECRET_TEXT}]},
    }


def test_high_security_disabled_returns_allow_without_scanning() -> None:
    evidence = scan_native_outbound(
        surface=NativeScanSurface.MESSAGE,
        body=_secret_message(),
        high_security_mode=False,
    )

    assert evidence.allowed
    assert evidence.outcome == NativeScanOutcome.ALLOW.value
    assert evidence.high_security_mode is False
    # Evidence still binds a digest even when the scan is a no-op.
    assert evidence.payload_digest


def test_clean_message_allowed_in_high_security() -> None:
    evidence = scan_native_outbound(
        surface=NativeScanSurface.MESSAGE,
        body=_CLEAN_MESSAGE,
        high_security_mode=True,
    )

    assert evidence.allowed
    assert evidence.contract_version == NATIVE_OUTBOUND_SCAN_CONTRACT_VERSION
    assert evidence.scanner_policy_ref == OUTBOUND_SCAN_POLICY_REF


def test_secret_message_blocks_with_redacted_finding() -> None:
    with pytest.raises(NativeScanBlockedError) as exc_info:
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE,
            body=_secret_message(),
            high_security_mode=True,
        )

    evidence = exc_info.value.evidence
    assert evidence.outcome == NativeScanOutcome.BLOCK.value
    assert [(f.category, f.location) for f in evidence.findings] == [
        ("token", "body.data.content[0].text")
    ]
    # Bounded evidence never carries the detected value.
    assert _SECRET_TEXT not in str(evidence.audit_metadata())


def test_nested_and_queued_message_shapes_are_scanned() -> None:
    # A steered/queued shape nests the secret deeper; every string leaf is still
    # extracted with a stable location.
    body = {
        "type": "message",
        "queued": True,
        "data": {"segments": [{"reply": {"quote": _SECRET_TEXT}}]},
    }
    with pytest.raises(NativeScanBlockedError) as exc_info:
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE, body=body, high_security_mode=True
        )
    assert exc_info.value.evidence.findings[0].location == (
        "body.data.segments[0].reply.quote"
    )


def test_slash_command_arguments_are_scanned() -> None:
    with pytest.raises(NativeScanBlockedError) as exc_info:
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE,
            body={"type": "command", "command": "/review", "args": [_SECRET_TEXT]},
            high_security_mode=True,
        )
    assert exc_info.value.evidence.findings[0].location == "body.args[0]"


def test_textual_attachment_and_upload_metadata_are_scanned() -> None:
    with pytest.raises(NativeScanBlockedError) as exc_info:
        scan_native_outbound(
            surface=NativeScanSurface.NATIVE_MUTATION,
            body={
                "type": "upload",
                "metadata": {"filename": "notes.txt", "description": _SECRET_TEXT},
                "content": [{"type": "text", "text": "bounded attachment"}],
            },
            high_security_mode=True,
        )
    assert exc_info.value.evidence.findings[0].location == (
        "body.metadata.description"
    )


def test_unknown_top_level_schema_fails_closed() -> None:
    with pytest.raises(NativeScanEnforcementError) as exc_info:
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE,
            body=["not", "an", "object"],
            high_security_mode=True,
        )
    assert exc_info.value.evidence.reason == "unknown_schema"
    assert exc_info.value.evidence.outcome == (
        NativeScanOutcome.ENFORCEMENT_UNAVAILABLE.value
    )


def test_binary_part_is_outside_text_scan_contract() -> None:
    body = {
        "type": "message",
        "data": {"content": [{"type": "input_image", "image_url": "blob://x"}]},
    }
    with pytest.raises(NativeScanEnforcementError) as exc_info:
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE, body=body, high_security_mode=True
        )
    assert exc_info.value.evidence.reason == "uninspectable_binary_part"


def test_undecodable_text_field_fails_closed() -> None:
    body = {
        "type": "message",
        "data": {"content": [{"type": "text", "text": {"unexpected": "object"}}]},
    }
    with pytest.raises(NativeScanEnforcementError) as exc_info:
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE, body=body, high_security_mode=True
        )
    assert exc_info.value.evidence.reason == "undecodable_field"


def test_scanner_error_fails_closed(monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise RuntimeError("scanner unavailable")

    monkeypatch.setattr(
        "moonmind.omnigent.native_outbound_scan.scan_outbound_bundle", _boom
    )
    with pytest.raises(NativeScanEnforcementError) as exc_info:
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE,
            body=_CLEAN_MESSAGE,
            high_security_mode=True,
        )
    assert exc_info.value.evidence.reason == "scanner_error"


def test_scanner_timeout_fails_closed(monkeypatch) -> None:
    def _timeout(*_a, **_k):
        raise TimeoutError("scanner deadline exceeded")

    monkeypatch.setattr(
        "moonmind.omnigent.native_outbound_scan.scan_outbound_bundle", _timeout
    )
    with pytest.raises(NativeScanEnforcementError) as exc_info:
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE,
            body=_CLEAN_MESSAGE,
            high_security_mode=True,
        )
    assert exc_info.value.evidence.reason == "scanner_error"


def test_elicitation_response_text_is_scanned() -> None:
    with pytest.raises(NativeScanBlockedError):
        scan_native_outbound(
            surface=NativeScanSurface.ELICITATION_RESPONSE,
            body={"decision": "approve", "note": "token=" + "z" * 24},
            high_security_mode=True,
        )


def test_digest_binds_decision_to_exact_payload() -> None:
    first = scan_native_outbound(
        surface=NativeScanSurface.MESSAGE,
        body=_CLEAN_MESSAGE,
        idempotency_key="k-1",
        high_security_mode=True,
    )
    changed = {"type": "message", "data": {"content": [{"type": "text", "text": "x"}]}}
    # A changed payload yields a different digest, so an allow result bound to the
    # first digest cannot be reused for the second under the same key.
    assert first.payload_digest != canonical_payload_digest(changed)
    assert first.idempotency_key == "k-1"
    assert first.audit_metadata()["payloadDigest"] == first.payload_digest


def test_empty_text_message_is_allowed() -> None:
    evidence = scan_native_outbound(
        surface=NativeScanSurface.MESSAGE,
        body={"type": "interrupt"},
        high_security_mode=True,
    )
    assert evidence.allowed


def test_text_part_missing_text_field_fails_closed() -> None:
    # A declared text part with no ``text`` member must not be forwarded with
    # only its literal type string scanned; it fails closed as undecodable.
    body = {
        "type": "message",
        "data": {"content": [{"type": "text"}]},
    }
    with pytest.raises(NativeScanEnforcementError) as exc_info:
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE, body=body, high_security_mode=True
        )
    assert exc_info.value.evidence.reason == "undecodable_field"


def test_secret_split_across_text_parts_is_blocked() -> None:
    # A credential split across adjacent text parts never appears whole in any
    # single leaf, but the composed message the provider receives contains it.
    head = _SECRET_TEXT[:6]
    tail = _SECRET_TEXT[6:]
    body = {
        "type": "message",
        "data": {
            "content": [
                {"type": "text", "text": head},
                {"type": "text", "text": tail},
            ]
        },
    }
    with pytest.raises(NativeScanBlockedError) as exc_info:
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE, body=body, high_security_mode=True
        )
    locations = {f.location for f in exc_info.value.evidence.findings}
    assert "body.data.content[composed]" in locations
    # The individual fragments alone did not trip the scanner.
    assert "body.data.content[0].text" not in locations
    assert "body.data.content[1].text" not in locations


def test_caller_controlled_key_is_sanitized_in_finding_location() -> None:
    # A caller-controlled object key carrying credential text and a newline must
    # not be copied verbatim into an exposed/audited finding location.
    malicious_key = "token=LEAKME\ninjected"
    body = {
        "type": "message",
        "data": {malicious_key: _SECRET_TEXT},
    }
    with pytest.raises(NativeScanBlockedError) as exc_info:
        scan_native_outbound(
            surface=NativeScanSurface.MESSAGE, body=body, high_security_mode=True
        )
    evidence = exc_info.value.evidence
    location = evidence.findings[0].location
    assert "\n" not in location
    assert "LEAKME" not in location
    assert "token=" not in location
    # The bounded evidence never carries the detected value either.
    assert _SECRET_TEXT not in str(evidence.audit_metadata())
