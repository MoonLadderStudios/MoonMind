from __future__ import annotations

import pytest
from pydantic import ValidationError

from moonmind.security.outbound_scan import (
    OutboundBundleItem,
    scan_outbound_bundle,
    scan_outbound_text,
)
from moonmind.workflows.executions.control_stop_continuation import (
    ControlStopContinuationContract,
)
from tests.unit.workflows.executions.test_control_stop_continuation import (
    _payload as control_stop_payload,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def test_caller_facing_text_scan_blocks_before_side_effect() -> None:
    raw_secret = "integration-secret-value"
    side_effects: list[str] = []

    result = scan_outbound_text(
        f"comment body includes credential={raw_secret}",
        location="comment.body",
        high_security_mode=True,
    )
    if result.allowed:
        side_effects.append("posted")

    dumped = result.model_dump(by_alias=True)

    assert side_effects == []
    assert dumped["decision"] == "block"
    assert dumped["findings"][0]["category"] == "credential"
    assert dumped["findings"][0]["location"] == "comment.body"
    assert raw_secret not in str(dumped)


def test_caller_facing_text_scan_blocks_quoted_assignments() -> None:
    raw_secret = "quoted-integration-secret"
    side_effects: list[str] = []

    result = scan_outbound_text(
        f'api_key="{raw_secret}"',
        location="comment.body",
        high_security_mode=True,
    )
    if result.allowed:
        side_effects.append("posted")

    dumped = result.model_dump(by_alias=True)

    assert side_effects == []
    assert dumped["decision"] == "block"
    assert dumped["findings"][0]["category"] == "credential"
    assert raw_secret not in str(dumped)


def test_commit_like_bundle_contract_blocks_with_item_location() -> None:
    raw_secret = "integration-bundle-secret"
    result = scan_outbound_bundle(
        [
            OutboundBundleItem(location="commit.message", content="safe summary"),
            OutboundBundleItem(
                location="commit.diff:moonmind/example.py",
                content=f"password={raw_secret}",
            ),
        ],
        high_security_mode=True,
    )

    dumped = result.model_dump(by_alias=True)

    assert dumped["allowed"] is False
    assert dumped["decision"] == "block"
    assert dumped["highSecurityMode"] is True
    assert dumped["findings"] == [
        {
            "category": "credential",
            "location": "commit.diff:moonmind/example.py",
            "redactedPreview": "password=[REDACTED]",
        }
    ]
    assert raw_secret not in str(dumped)


def test_disabled_mode_preserves_exact_outbound_payloads() -> None:
    raw_text = "send token=preserve-this-value exactly"
    bundle = [
        OutboundBundleItem(location="commit.message", content=raw_text),
        OutboundBundleItem(location="commit.diff:file.py", content="safe change"),
    ]

    text_result = scan_outbound_text(raw_text, high_security_mode=False)
    bundle_result = scan_outbound_bundle(bundle, high_security_mode=False)

    assert text_result.model_dump(by_alias=True)["originalContent"] == raw_text
    assert bundle_result.original_bundle == bundle
    assert (
        bundle_result.model_dump(by_alias=True)["originalBundle"][0]["content"]
        == raw_text
    )


def test_control_stop_contract_blocks_secret_before_admission() -> None:
    payload = control_stop_payload()
    payload["remainingWorkRef"] = "artifact://" + "github_" + "pat_" + ("1" * 30)
    admitted: list[str] = []

    with pytest.raises(ValidationError, match="secret scanning"):
        ControlStopContinuationContract.model_validate(payload)
        admitted.append("started")

    assert admitted == []
