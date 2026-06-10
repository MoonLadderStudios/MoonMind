from __future__ import annotations

from moonmind.config.settings import AppSettings, SecuritySettings
from moonmind.security.outbound_scan import (
    OutboundBundleItem,
    OutboundFinding,
    OutboundScanResult,
    resolve_high_security_mode,
    scan_outbound_bundle,
    scan_outbound_text,
)


def test_high_security_mode_settings_and_precedence(monkeypatch) -> None:
    monkeypatch.setenv("MOONMIND_HIGH_SECURITY_MODE", "true")

    security_settings = SecuritySettings()
    app_settings = AppSettings(security=security_settings)

    assert security_settings.high_security_mode is True
    assert resolve_high_security_mode(settings=app_settings) is True
    assert resolve_high_security_mode(settings=security_settings) is True
    assert resolve_high_security_mode(False, settings=app_settings) is False
    assert resolve_high_security_mode(True, settings=SecuritySettings()) is True


def test_scan_result_and_finding_serialize_with_stable_aliases() -> None:
    finding = OutboundFinding(
        category="credential",
        location="comment.body",
        redacted_preview="password=[REDACTED]",
    )
    result = OutboundScanResult(
        allowed=False,
        decision="block",
        high_security_mode=True,
        findings=[finding],
        sanitized_diagnostics=["Blocked outbound content: credential at comment.body"],
    )

    dumped = result.model_dump(by_alias=True)

    assert dumped["highSecurityMode"] is True
    assert dumped["findings"][0]["redactedPreview"] == "password=[REDACTED]"
    assert dumped["sanitizedDiagnostics"] == [
        "Blocked outbound content: credential at comment.body"
    ]


def test_high_security_text_scan_blocks_with_redacted_diagnostics() -> None:
    raw_secret = "not-a-real-secret-value"
    result = scan_outbound_text(
        f"please post password={raw_secret}",
        location="comment.body",
        high_security_mode=True,
    )

    dumped = str(result.model_dump())

    assert result.allowed is False
    assert result.decision == "block"
    assert result.findings[0].category == "credential"
    assert result.findings[0].location == "comment.body"
    assert raw_secret not in dumped
    assert "password=[REDACTED]" in dumped


def test_high_security_bundle_scan_blocks_with_item_location() -> None:
    raw_secret = "bundle-secret-value"
    result = scan_outbound_bundle(
        [
            OutboundBundleItem(location="commit.message", content="normal message"),
            {
                "location": "diff:service.py",
                "content": f"api_key={raw_secret}",
            },
        ],
        high_security_mode=True,
    )

    dumped = str(result.model_dump())

    assert result.allowed is False
    assert result.decision == "block"
    assert [(finding.category, finding.location) for finding in result.findings] == [
        ("credential", "diff:service.py")
    ]
    assert raw_secret not in dumped
    assert "api_key=[REDACTED]" in dumped


def test_disabled_mode_allows_and_preserves_original_content() -> None:
    text = "message contains password=left-unchanged"
    bundle = [
        OutboundBundleItem(location="commit.message", content=text),
        OutboundBundleItem(location="diff:app.py", content="token=left-unchanged"),
    ]

    text_result = scan_outbound_text(
        text,
        location="message.body",
        high_security_mode=False,
    )
    bundle_result = scan_outbound_bundle(bundle, high_security_mode=False)

    assert text_result.allowed is True
    assert text_result.decision == "allow"
    assert text_result.findings == []
    assert text_result.original_content == text
    assert bundle_result.allowed is True
    assert bundle_result.findings == []
    assert bundle_result.original_bundle == bundle
