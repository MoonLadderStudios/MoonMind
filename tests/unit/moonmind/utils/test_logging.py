import os
from unittest.mock import patch

import pytest

from moonmind.utils.logging import (
    SecretRedactor,
    _is_non_secret_sentinel,
    _is_sensitive_key,
    _secret_variants,
    redact_profile_file_templates,
    scrub_github_tokens,
)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("token", True),
        ("my_secret", True),
        ("auth_key", True),
        ("credential_file", True),
        ("API_PASSWORD", True),
        ("normal_string", False),
        ("not_a_tok", False),
        ("random_word", False),
    ],
)
def test_is_sensitive_key(key, expected):
    assert _is_sensitive_key(key) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("FALSE", True),
        (" None ", True),
        ("null", True),
        ("YES", True),
        ("random_word", False),
        ("true_but_not", False),
        ("", False),
    ],
)
def test_is_non_secret_sentinel(value, expected):
    assert _is_non_secret_sentinel(value) is expected


@pytest.mark.parametrize(
    ("input_text", "expected_text"),
    [
        (
            "Here is my ghp_abc123DEF456ghi789JKL012mno345PQR678 token",
            "Here is my [REDACTED] token",
        ),
        (
            "Multiple: ghp_abc123DEF456ghi789JKL012mno345PQR678 and gho_xyz987UVW654rst321OPQ098lmn765ABC321",
            "Multiple: [REDACTED] and [REDACTED]",
        ),
        (
            "Not a token: gh_abc123DEF456ghi789JKL012mno345PQR678",
            "Not a token: gh_abc123DEF456ghi789JKL012mno345PQR678",
        ),
        ("", ""),
        (None, ""),
    ],
)
def test_scrub_github_tokens(input_text, expected_text):
    assert scrub_github_tokens(input_text) == expected_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [("hello", ["hello", "aGVsbG8="]), ("hello world", ["hello world", "hello+world"])],
)
def test_secret_variants(text, expected):
    variants = _secret_variants(text)
    assert expected[0] in variants
    assert expected[1] in variants


def test_secret_redactor_init():
    redactor = SecretRedactor(secrets=["my_secret", "true", "", "another_secret"])

    # Sentinels and empty strings are ignored.
    assert "true" not in redactor._secrets
    assert "" not in redactor._secrets

    # Base variants and other variants should be in the list
    assert "my_secret" in redactor._secrets
    assert "another_secret" in redactor._secrets
    assert "bXlfc2VjcmV0" in redactor._secrets
    assert "YW5vdGhlcl9zZWNyZXQ=" in redactor._secrets

    # Secrets should be sorted by length descending
    lengths = [len(s) for s in redactor._secrets]
    assert lengths == sorted(lengths, reverse=True)


@patch.dict(
    os.environ,
    {"API_TOKEN": "my_super_secret", "NORMAL_VAR": "just_a_value"},
    clear=True,
)
def test_secret_redactor_from_environ():
    redactor = SecretRedactor.from_environ(extra_secrets=["extra_secret"])

    assert "my_super_secret" in redactor._secrets
    assert "extra_secret" in redactor._secrets
    assert "just_a_value" not in redactor._secrets

    # also checking that variants are generated
    assert "bXlfc3VwZXJfc2VjcmV0" in redactor._secrets  # base64 of my_super_secret


def test_secret_redactor_scrub():
    redactor = SecretRedactor(secrets=["my_secret"])

    assert redactor.scrub("This is my_secret don't tell") == "This is *** don't tell"
    assert redactor.scrub("No secrets here") == "No secrets here"
    assert redactor.scrub("") == ""
    assert redactor.scrub(None) == ""


def test_secret_redactor_scrub_sequence():
    redactor = SecretRedactor(secrets=["my_secret"])

    result = redactor.scrub_sequence(["This is my_secret", "No secrets"])
    assert result == ["This is ***", "No secrets"]


def test_redact_profile_file_templates_redacts_nested_content_fields():
    raw_secret = "sk-template-raw-secret"

    result = redact_profile_file_templates(
        [
            {
                "path": "/tmp/config.toml",
                "content_template": {"api_key": raw_secret, "model": "gpt-test"},
            },
            {
                "path": "/tmp/legacy.json",
                "contentTemplate": {"token": raw_secret},
            },
            {
                "path": "/tmp/plain.txt",
                "content": f"token={raw_secret}",
            },
        ]
    )

    assert raw_secret not in repr(result)
    assert result[0]["content_template"]["api_key"] == "[REDACTED]"
    assert result[0]["content_template"]["model"] == "gpt-test"
    assert result[1]["contentTemplate"]["token"] == "[REDACTED]"
    assert result[2]["content"] == "[REDACTED]"
