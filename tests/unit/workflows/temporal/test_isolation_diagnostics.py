"""Tests for MM-680 isolation diagnostic evidence."""

from __future__ import annotations

from moonmind.workflows.temporal.isolation_diagnostics import (
    IsolationDiagnostic,
    build_isolation_diagnostic,
)


def test_isolation_diagnostic_redacts_secret_like_values() -> None:
    diagnostic = build_isolation_diagnostic(
        reason_code="egress_blocked",
        summary="Blocked token=github_pat_1234567890abcdefghijklmnopqrstuvwxyz",
        surface="https://api.github.com/repos/o/r/pulls?token=abc123",
        metadata={"Authorization": "Bearer ghp_abcdefghijklmnopqrstuvwxyz1234567890"},
    )

    payload = diagnostic.to_payload()
    rendered = str(payload)
    assert "github_pat_1234567890abcdefghijklmnopqrstuvwxyz" not in rendered
    assert "ghp_abcdefghijklmnopqrstuvwxyz1234567890" not in rendered
    assert "token=abc123" not in rendered
    assert payload["reasonCode"] == "egress_blocked"


def test_isolation_diagnostic_accepts_stable_mm680_reason_codes() -> None:
    expected = {
        "egress_blocked",
        "surface_rejected",
        "direct_publish_denied",
        "pull_request_adopted",
        "publish_lease_conflict",
    }

    assert expected.issubset(IsolationDiagnostic.allowed_reason_codes())

