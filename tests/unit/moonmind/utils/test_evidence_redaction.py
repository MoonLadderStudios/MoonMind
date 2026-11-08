"""Bounded evidence transformations supplement ordinary credential redaction."""

import pytest

from moonmind.utils import logging


def test_evidence_payload_removes_known_personal_fields_and_embedded_credentials():
    original = {
        "message": "Bearer synthetic-evidence-token alice.fixture@example.test",
        "contact": {
            "fullName": "Synthetic Private Person",
            "phone_number": "+1-202-555-0111",
        },
        "path": "src/example.py",
        "line": 42,
        "artifactRef": "art_fixture",
    }
    safe = logging.redact_evidence_payload(original)
    assert "synthetic-evidence-token" not in str(safe)
    assert "alice.fixture@example.test" not in str(safe)
    assert "Synthetic Private Person" not in str(safe)
    assert "+1-202-555-0111" not in str(safe)
    assert (
        safe["path"] == original["path"]
        and safe["line"] == 42
        and safe["artifactRef"] == "art_fixture"
    )
    assert original["contact"]["fullName"] == "Synthetic Private Person"


@pytest.mark.parametrize(
    "options,payload",
    [
        ({"max_depth": 1}, {"a": {"b": {"c": "synthetic-bound-marker"}}}),
        ({"max_items": 2}, ["a", "b", "synthetic-bound-marker"]),
        ({"max_text_chars": 4}, "synthetic-bound-marker"),
    ],
    ids=["depth", "items", "text"],
)
def test_evidence_transformation_bounds_have_safe_errors(options, payload):
    with pytest.raises(ValueError) as raised:
        logging.redact_evidence_payload(payload, **options)
    assert "synthetic-bound-marker" not in str(raised.value)


def test_evidence_text_keeps_useful_details_and_ordinary_logging_contract():
    value = "src/example.py:42 contact alice.fixture@example.test token=synthetic-evidence-value"
    safe = logging.redact_evidence_text(value)
    assert "src/example.py:42" in safe
    assert (
        "alice.fixture@example.test" not in safe
        and "synthetic-evidence-value" not in safe
    )
    assert "alice.fixture@example.test" in logging.redact_sensitive_text(value)


def test_email_redaction_bounds_untrusted_local_parts_without_partial_matches():
    valid = "a" * 64 + "@example.test"
    invalid = "a" * 65 + "@example.test"
    punctuation = "!" * 100000
    text = f"{punctuation} {valid} {invalid} alice.fixture@example.test"
    safe = logging.redact_evidence_text(text)
    assert safe == f"{punctuation} [REDACTED_EMAIL] {invalid} [REDACTED_EMAIL]"


def test_cyclic_evidence_is_rejected_with_bounded_error():
    payload = []
    payload.append(payload)
    with pytest.raises(ValueError, match="bound"):
        logging.redact_evidence_payload(payload)


@pytest.mark.parametrize(
    "value",
    [123456789, False, 12.5, [123456789]],
    ids=["integer", "boolean", "number", "array"],
)
def test_non_string_credential_fields_are_withheld(value):
    assert logging.redact_evidence_payload({"password": value}) == {
        "password": "[REDACTED]"
    }


def test_untrusted_keys_are_transformed_and_collisions_fail_closed():
    assert "alice.fixture@example.test" not in str(
        logging.redact_evidence_payload({"alice.fixture@example.test": "retained"})
    )
    with pytest.raises(ValueError, match="collide"):
        logging.redact_evidence_payload(
            {
                "alice.fixture@example.test": "first",
                "bob.fixture@example.test": "second",
            }
        )


def test_nonfinite_json_values_fail_closed():
    with pytest.raises(ValueError, match="finite"):
        logging.redact_evidence_payload({"result": float("nan")})


def test_public_certificate_survives_but_mixed_private_key_armor_does_not(tmp_path):
    from tests.integration.security.tls_assessment_fixture import certificates

    _, _, ca = certificates(tmp_path)
    public = ca.read_text()
    assert logging.redact_sensitive_text(public) == public
    private = "-----BEGIN RSA PRIVATE KEY-----\nsynthetic-private-material\n-----END RSA PRIVATE KEY-----"
    mixed = logging.redact_evidence_text(public + private + public)
    assert mixed.count("-----BEGIN CERTIFICATE-----") == 2
    assert "synthetic-private-material" not in mixed
    truncated = logging.redact_evidence_text(public + private.split("-----END", 1)[0])
    assert public in truncated and "synthetic-private-material" not in truncated


@pytest.mark.parametrize("key", ["apiKey", "accessKey", "privateKey", "secretKey"])
def test_camel_case_credential_values_are_redacted_but_refs_survive(key):
    safe = logging.redact_evidence_payload({key: "synthetic-private-value"})
    assert safe[key] == "[REDACTED]"
    assert logging.redact_evidence_payload({key: "vault://fixture/value"})[key] == "vault://fixture/value"
    assert logging.redact_evidence_payload({key + "Ref": "profile:fixture"})[key + "Ref"] == "profile:fixture"


@pytest.mark.parametrize(
    "value",
    [
        'settings={"password": "synthetic-private-value"}',
        'Evidence: `{"apiKey": "synthetic-private-value"}`',
        '{"password": "synthetic-private-value",',
        '{"password": "synthetic-private-value',
        "Authorization: Basic c3ludGhldGljOnNlY3JldA==",
        "Authorization: Token synthetic-private-value",
    ],
    ids=["nested-json", "markdown-json", "partial-object", "partial-value", "basic", "token"],
)
def test_text_credential_variants_are_redacted(value):
    safe = logging.redact_evidence_text(value)
    assert "synthetic-private-value" not in safe
    assert "c3ludGhldGljOnNlY3JldA==" not in safe
    assert "synthetic-private-value" not in str(logging.redact_evidence_payload({"message": value}))


@pytest.mark.parametrize("scheme,host", [("https", "127.0.0.1:8443"), ("postgresql", "localhost:5432"), ("https", "example.test")])
def test_url_credentials_are_removed_without_losing_reproduction(scheme, host):
    value = f"{scheme}://svc:synthetic-private-value@{host}/reproduce?apiKey=synthetic-query-value&findingId=abc123"
    safe = logging.redact_evidence_text(value)
    assert "synthetic-private-value" not in safe and "synthetic-query-value" not in safe
    assert host in safe and "/reproduce?" in safe and "findingId=abc123" in safe


def test_public_keys_fingerprints_and_credential_references_are_preserved():
    payload = {
        "publicKey": "ssh-ed25519 fixture-public-material",
        "public_key": "ssh-ed25519 fixture-public-material",
        "publicKeyFingerprint": "sha256:fixture-public-identity",
        "privateKeyRef": "profile:fixture",
        "credentialRef": "credential:fixture",
        "tokenRef": "artifact:fixture",
        "path": "src/example.py",
    }
    assert logging.redact_evidence_payload(payload) == payload


def test_reference_named_prose_does_not_bypass_credentials():
    value = "src/check.py:42 token=synthetic-private-value contact synthetic-person@example.test"
    safe = logging.redact_evidence_payload({"configRef": value})
    assert "synthetic-private-value" not in str(safe)
    assert "synthetic-person@example.test" not in str(safe)
    assert "src/check.py:42" in safe["configRef"]


def test_reference_assignments_cannot_hide_nested_credential_text():
    value = '{"credentialRef": "token=synthetic-private-value"}'
    assert "synthetic-private-value" not in logging.redact_evidence_text(value)


@pytest.mark.parametrize(
    "assignment,secret",
    [
        ("Authorization=Basic c3ludGhldGljOmNhbmFyeQ==", "c3ludGhldGljOmNhbmFyeQ=="),
        ("AUTHORIZATION = Token synthetic-canary", "synthetic-canary"),
        ("Proxy-Authorization=Basic c3ludGhldGljOmNhbmFyeQ==", "c3ludGhldGljOmNhbmFyeQ=="),
    ],
    ids=["basic-assignment", "token-assignment", "proxy-assignment"],
)
def test_authorization_assignment_removes_scheme_and_value(assignment, secret):
    value = f"https://example.test/reproduce?{assignment}&findingId=abc123 src/check.py:42"
    for safe in (
        logging.redact_sensitive_text(value),
        logging.redact_evidence_text(value),
        logging.redact_evidence_payload({"message": value})["message"],
    ):
        assert secret not in safe
        assert "https://example.test/reproduce?" in safe
        assert "&findingId=abc123 src/check.py:42" in safe


@pytest.mark.parametrize(
    "key,value",
    [
        ("Authorization", "Basic c3ludGhldGljOnNlY3JldA=="),
        ("proxy-authorization", "Token synthetic-credential-canary"),
        ("authorization", "synthetic-credential-canary"),
        ("proxyAuthorization", "Basic c3ludGhldGljOnNlY3JldA=="),
        ("proxy_authorization", "synthetic-credential-canary"),
    ],
    ids=["basic", "proxy-token", "opaque", "camel-proxy", "snake-proxy"],
)
def test_structured_authorization_fields_are_redacted(key, value):
    payload = {"headers": {key: value}, "status": "observed"}
    assert logging.redact_evidence_payload(payload) == {
        "headers": {key: "[REDACTED]"},
        "status": "observed",
    }
    assert logging.redact_sensitive_payload(payload)["headers"][key] == "[REDACTED]"
