"""Redaction keeps registry diagnostics while hiding credential material."""
from conftest import load


def test_redact_text_hides_url_userinfo_and_secret_assignments(controller_path):
    redact = load("redact")
    text = (
        "pull https://user:s3cret@registry.example/v2/ failed; "
        "token=abc123 password: hunter2 ok"
    )
    out = redact.redact_text(text)
    assert "s3cret" not in out and "abc123" not in out and "hunter2" not in out
    assert "registry.example" in out  # diagnostics preserved


def test_redact_mapping_redacts_sensitive_keys_recursively(controller_path):
    redact = load("redact")
    payload = {
        "image": "ghcr.io/org/app:sha-abc",
        "auth": {"registry_password": "s3cret", "nested": [{"api_key": "k"}]},
        "command": ["docker", "pull", "x"],
    }
    out = redact.redact_mapping(payload)
    dumped = str(out)
    assert "s3cret" not in dumped and '"k"' not in dumped
    assert "ghcr.io/org/app:sha-abc" in dumped
    assert payload["auth"]["registry_password"] == "s3cret"  # input untouched


def test_redact_truncates_long_tails_for_records(controller_path):
    redact = load("redact")
    out = redact.tail_text("x" * 5000, max_chars=100)
    assert len(out) == 100
