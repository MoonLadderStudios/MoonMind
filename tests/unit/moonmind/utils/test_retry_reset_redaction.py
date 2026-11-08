"""Public retry audit markers survive without exempting authorization secrets."""

import json

import pytest

from moonmind.utils.logging import redact_evidence_text, redact_sensitive_text


@pytest.mark.parametrize(
    "marker",
    [
        "",
        "moonmind-bot at 2026-09-26T12:00:00+00:00",
        "fixture-app[bot] at 2026-09-26T12:00:00.123456Z",
        "moonmind-bot at 2026-09-26T12:00:00",
        "moonmind-bot at 2026-09-26T12:00:00-05:30",
    ],
    ids=["unset", "operator", "application", "historical-naive", "offset"],
)
@pytest.mark.parametrize("redactor", [redact_sensitive_text, redact_evidence_text])
def test_typed_reset_marker_survives_assignment_redaction(marker, redactor):
    record = json.dumps({"resetAuthorization": marker})
    assert redactor(record) == record


@pytest.mark.parametrize(
    "marker",
    [
        "synthetic-private-value",
        "Bearer synthetic-private-value",
        "moonmind-bot at 2026-09-26T12:00:00+00:00 password=synthetic-private-value",
        "ghp_" + "a" * 30 + " at 2026-09-26T12:00:00+00:00",
        "moonmind-bot at 2026-02-30T12:00:00+00:00",
        "moonmind-bot at not-a-timestamp",
        "moonmind-bot at 2026-09-26T12:00:00+24:00",
        "moonmind-bot at 2026-09-26T12:00:00+00:00 additional-text",
        "a" * 40 + " at 2026-09-26T12:00:00+00:00",
        "moonmind-bot at " + "1" * 10000,
        "-invalid-login at 2026-09-26T12:00:00+00:00",
        "AKIA" + "A" * 16 + " at 2026-09-26T12:00:00+00:00",
    ],
    ids=[
        "opaque",
        "bearer",
        "extra-credential",
        "provider-token",
        "invalid-date",
        "unknown-time",
        "invalid-offset",
        "extra-text",
        "long-login",
        "long-value",
        "invalid-login",
        "cloud-token",
    ],
)
def test_unknown_or_credential_bearing_reset_values_remain_redacted(marker):
    safe = redact_sensitive_text(json.dumps({"resetAuthorization": marker}))
    assert json.loads(safe)["resetAuthorization"] == "[REDACTED]"
    assert "synthetic-private-value" not in safe


@pytest.mark.parametrize(
    "key",
    [
        "authorization",
        "proxy-authorization",
        "resetAuthorizationToken",
        "reset_authorization",
        "ResetAuthorization",
        "prefixresetAuthorization",
    ],
)
def test_reset_marker_does_not_exempt_other_authorization_keys(key):
    safe = redact_sensitive_text(
        json.dumps({key: "moonmind-bot at 2026-09-26T12:00:00+00:00"})
    )
    assert json.loads(safe)[key] == "[REDACTED]"


@pytest.mark.parametrize(
    "record",
    [
        'resetAuthorization="moonmind-bot at 2026-09-26T12:00:00+00:00"',
        "'resetAuthorization': \"moonmind-bot at 2026-09-26T12:00:00+00:00\"",
        '"resetAuthorization"="moonmind-bot at 2026-09-26T12:00:00+00:00"',
        '"resetAuthorization": "moonmind-bot at 2026-09-26T12:00:00+00:00',
        r'{"resetAuthorization": "\u0041KIAAAAAAAAAAAAAAAAA at 2026-09-26T12:00:00+00:00"}',
        r'{"resetAuthorization": "ghp\u005faaaaaaaaaaaaaaaaaaaaaaaaaaaaaa at 2026-09-26T12:00:00+00:00"}',
    ],
    ids=[
        "unquoted-key",
        "single-quote-key",
        "assignment",
        "truncated",
        "escaped-cloud",
        "escaped-provider",
    ],
)
def test_only_complete_canonical_json_reset_values_are_preserved(record):
    safe = redact_sensitive_text(record)
    assert "2026-09-26" not in safe
    assert "[REDACTED]" in safe
