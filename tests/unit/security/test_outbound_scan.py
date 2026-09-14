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
    assert resolve_high_security_mode(True, settings=SecuritySettings()) is True


def test_high_security_mode_explicit_false_cannot_downgrade_operator_true(
    monkeypatch,
) -> None:
    """MoonMind#809: an untrusted explicit=False must not disable operator policy."""

    monkeypatch.setenv("MOONMIND_HIGH_SECURITY_MODE", "true")
    security_settings = SecuritySettings()
    app_settings = AppSettings(security=security_settings)
    assert security_settings.high_security_mode is True

    # Explicit False defers to the operator-required True instead of disabling.
    assert resolve_high_security_mode(False, settings=app_settings) is True
    assert resolve_high_security_mode(False) is True

    scan_result = scan_outbound_text(
        "password=synthetic-secret-value",
        location="comment.body",
        high_security_mode=False,
    )
    assert scan_result.allowed is False
    assert scan_result.decision == "block"


def test_high_security_mode_explicit_true_opts_in_when_operator_false(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MOONMIND_HIGH_SECURITY_MODE", "false")

    assert resolve_high_security_mode(True) is True
    assert resolve_high_security_mode(False) is False
    assert resolve_high_security_mode(None) is False

    clean = scan_outbound_text(
        "hello world",
        location="comment.body",
        high_security_mode=True,
    )
    assert clean.allowed is True


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


def test_high_security_text_scan_blocks_quoted_secret_assignments() -> None:
    double_quoted_secret = "quoted-secret-value"
    single_quoted_secret = "single-quoted-secret"

    result = scan_outbound_text(
        f'api_key="{double_quoted_secret}"\npassword: \'{single_quoted_secret}\'',
        location="config.payload",
        high_security_mode=True,
    )

    dumped = str(result.model_dump())

    assert result.allowed is False
    assert result.decision == "block"
    assert [finding.category for finding in result.findings] == [
        "credential",
        "credential",
    ]
    assert double_quoted_secret not in dumped
    assert single_quoted_secret not in dumped


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


def test_push_scan_coverage_error_fails_closed_on_truncation() -> None:
    from moonmind.security.outbound_scan import push_scan_coverage_error

    assert (
        push_scan_coverage_error(
            commit_range="origin/main..feature",
            commit_metadata_len=10,
            max_commit_metadata_chars=100_000,
            changed_file_count=5,
            max_changed_files=200,
        )
        is None
    )

    metadata_reason = push_scan_coverage_error(
        commit_range="origin/main..feature",
        commit_metadata_len=100_001,
        max_commit_metadata_chars=100_000,
        changed_file_count=1,
        max_changed_files=200,
    )
    assert metadata_reason is not None
    assert "coverage incomplete" in metadata_reason
    assert "origin/main..feature" in metadata_reason

    files_reason = push_scan_coverage_error(
        commit_range="origin/main..feature",
        commit_metadata_len=10,
        max_commit_metadata_chars=100_000,
        changed_file_count=201,
        max_changed_files=200,
    )
    assert files_reason is not None
    assert "changed file list" in files_reason

    diff_reason = push_scan_coverage_error(
        commit_range="origin/main..feature",
        commit_metadata_len=10,
        max_commit_metadata_chars=100_000,
        changed_file_count=1,
        max_changed_files=200,
        oversized_diff_path="app/secret.py",
    )
    assert diff_reason is not None
    assert "diff" in diff_reason


def test_audit_metadata_never_includes_raw_payload() -> None:
    text = "message contains password=left-unchanged"
    text_result = scan_outbound_text(
        text,
        location="message.body",
        high_security_mode=False,
    )
    metadata = text_result.audit_metadata()

    assert "original_content" not in str(metadata).lower()
    assert "originalContent" not in str(metadata)
    assert text not in str(metadata)
    assert metadata["decision"] == "allow"

    blocked = scan_outbound_text(
        "please post password=synthetic-blocked-value",
        location="comment.body",
        high_security_mode=True,
    )
    blocked_metadata = blocked.audit_metadata()
    assert "synthetic-blocked-value" not in str(blocked_metadata)
    assert blocked_metadata["findingCategories"] == ["credential"]
    assert blocked_metadata["findingLocations"] == ["comment.body"]
