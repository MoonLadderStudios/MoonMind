import os
from unittest.mock import patch

from moonmind.utils.logging import (
    SecretRedactor,
    _is_non_secret_sentinel,
    _is_sensitive_key,
    _secret_variants,
    scrub_github_tokens,
)


def test_is_sensitive_key():
    assert _is_sensitive_key("token")
    assert _is_sensitive_key("my_secret")
    assert _is_sensitive_key("auth_key")
    assert _is_sensitive_key("credential_file")
    assert _is_sensitive_key("API_PASSWORD")

    assert not _is_sensitive_key("normal_string")
    assert not _is_sensitive_key("not_a_tok")
    assert not _is_sensitive_key("random_word")


def test_is_non_secret_sentinel():
    assert _is_non_secret_sentinel("true")
    assert _is_non_secret_sentinel("FALSE")
    assert _is_non_secret_sentinel(" None ")
    assert _is_non_secret_sentinel("null")
    assert _is_non_secret_sentinel("YES")

    assert not _is_non_secret_sentinel("random_word")
    assert not _is_non_secret_sentinel("true_but_not")
    assert not _is_non_secret_sentinel("")


def test_secret_variants():
    variants = _secret_variants("hello")
    assert "hello" in variants
    assert "aGVsbG8=" in variants  # base64

    variants = _secret_variants("hello world")
    assert "hello world" in variants
    assert "hello+world" in variants  # url encoded


def test_scrub_github_tokens():
    assert (
        scrub_github_tokens("Here is my ghp_abc123DEF456ghi789JKL012mno345PQR678 token")
        == "Here is my [REDACTED] token"
    )
    assert (
        scrub_github_tokens(
            "Multiple: ghp_abc123DEF456ghi789JKL012mno345PQR678 and gho_xyz987UVW654rst321OPQ098lmn765ABC321"
        )
        == "Multiple: [REDACTED] and [REDACTED]"
    )
    assert (
        scrub_github_tokens("Not a token: gh_abc123DEF456ghi789JKL012mno345PQR678")
        == "Not a token: gh_abc123DEF456ghi789JKL012mno345PQR678"
    )
    assert scrub_github_tokens("") == ""
    assert scrub_github_tokens(None) == ""


def test_secret_redactor_init():
    redactor = SecretRedactor(secrets=["my_secret", "true", "", "another_secret"])

    # Sentinels and empty strings are ignored.
    assert "true" not in redactor._secrets
    assert "" not in redactor._secrets

    # Base variants and other variants should be in the list
    assert "my_secret" in redactor._secrets
    assert "another_secret" in redactor._secrets

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
