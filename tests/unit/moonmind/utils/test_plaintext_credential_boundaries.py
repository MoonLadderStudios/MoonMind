"""Credential punctuation is data in plaintext and syntax only in URL context."""

import json

import pytest

from moonmind.security.evidence_exports import prepare_evidence_member
from moonmind.utils.logging import redact_evidence_text, redact_sensitive_text


@pytest.mark.parametrize(
    "punctuation",
    list("&<>`,;"),
    ids=["ampersand", "less", "greater", "backtick", "comma", "semicolon"],
)
@pytest.mark.parametrize("position", ["leading", "embedded", "trailing"])
@pytest.mark.parametrize(
    "prefix", ["PASSWORD=", "password: "], ids=["dotenv", "plaintext"]
)
def test_plaintext_credentials_are_fully_masked(punctuation, position, prefix):
    value = {
        "leading": punctuation + "canyon-meadow-lantern",
        "embedded": "canyon" + punctuation + "meadow-lantern",
        "trailing": "canyon-meadow-lantern" + punctuation,
    }[position]
    source = f"src/check.py:42\n{prefix}{value}\nresult=observed"
    expected = f"src/check.py:42\n{prefix}[REDACTED]\nresult=observed"
    assert redact_sensitive_text(source) == expected
    assert redact_evidence_text(source) == expected
    assert redact_evidence_text(expected) == expected
    member, extension, schema = prepare_evidence_member(
        source.encode(), content_type="text/plain", hint=""
    )
    assert member == expected.encode()
    assert (extension, schema) == ("txt", "utf8-text/v1")


@pytest.mark.parametrize(
    "source,expected",
    [
        (
            "https://example.test/check?password=canyon%26meadow&findingId=abc123&token=lantern#result",
            "https://example.test/check?password=[REDACTED]&findingId=abc123&token=[REDACTED]#result",
        ),
        (
            "GET /check?apiKey=canyon%3Cmeadow&findingId=abc123",
            "GET /check?apiKey=[REDACTED]&findingId=abc123",
        ),
        (
            '<input password="canyon&meadow<lantern>" data-public="observed">',
            '<input password=[REDACTED] data-public="observed">',
        ),
        (
            '<a href="https://example.test/check?token=canyon&findingId=abc123">Reproduce</a>',
            '<a href="https://example.test/check?token=[REDACTED]&findingId=abc123">Reproduce</a>',
        ),
        (
            "PASSWORD=https://example.test/canyon?findingId=private&extra=lantern",
            "PASSWORD=[REDACTED]",
        ),
        (
            "password: prefixhttps://example.test/canyon&meadow",
            "password: [REDACTED]",
        ),
        (
            "PASSWORD=<canyon-meadow-lantern>",
            "PASSWORD=[REDACTED]",
        ),
    ],
    ids=[
        "url-query",
        "relative-query",
        "quoted-markup",
        "markup-url",
        "url-as-secret",
        "embedded-url-secret",
        "angle-secret",
    ],
)
def test_context_preserves_public_reproduction_without_exempting_secret_urls(
    source, expected
):
    assert redact_evidence_text(source) == expected
    assert redact_evidence_text(expected) == expected


def test_many_url_contexts_preserve_query_fields_without_exempting_secret_values():
    sources = []
    expected = []
    for index in range(500):
        sources.append(f"https://example.test/{index}?token=canyon&findingId={index}")
        expected.append(
            f"https://example.test/{index}?token=[REDACTED]&findingId={index}"
        )
        sources.append(
            f"PASSWORD=https://example.test/{index}?value=canyon&private=lantern"
        )
        expected.append("PASSWORD=[REDACTED]")
    assert redact_evidence_text("\n".join(sources)) == "\n".join(expected)


@pytest.mark.parametrize(
    "value",
    [
        "'canyon-meadow-lantern'",
        '"canyon-meadow-lantern"',
        "canyon'meadow-lantern",
        'canyon"meadow-lantern',
    ],
    ids=["single-quoted", "double-quoted", "embedded-single", "embedded-double"],
)
@pytest.mark.parametrize(
    "prefix",
    ["https://example.test/check?password=", "GET /check?apiKey="],
    ids=["absolute", "relative"],
)
def test_query_credential_quotes_do_not_split_the_redaction_context(value, prefix):
    assert (
        redact_evidence_text(prefix + value + "&findingId=abc123")
        == prefix + "[REDACTED]&findingId=abc123"
    )


@pytest.mark.parametrize(
    "value",
    ["canyon-meadow-lantern", "'canyon-meadow-lantern'", "canyon'meadow-lantern"],
    ids=["unquoted", "quoted", "embedded"],
)
def test_final_query_credential_preserves_quoted_markup_boundary(value):
    source = f'<a href="https://example.test/check?password={value}">Reproduce</a>'
    assert (
        redact_evidence_text(source)
        == '<a href="https://example.test/check?password=[REDACTED]">Reproduce</a>'
    )


@pytest.mark.parametrize(
    "value",
    [
        "<canyon-meadow-lantern>",
        "canyon<meadow>lantern",
        "`canyon-meadow-lantern`",
        "canyon`meadow-lantern",
        "'canyon-meadow-lantern",
        '"canyon-meadow-lantern',
    ],
    ids=[
        "angle-quoted",
        "angle-embedded",
        "tick-quoted",
        "tick-embedded",
        "single-incomplete",
        "double-incomplete",
    ],
)
@pytest.mark.parametrize(
    "prefix",
    ["https://example.test/check?password=", "GET /check?apiKey="],
    ids=["absolute", "relative"],
)
def test_nonstandard_query_punctuation_is_masked_fail_closed(value, prefix):
    expected = prefix + "[REDACTED]&findingId=abc123"
    assert redact_evidence_text(prefix + value + "&findingId=abc123") == expected
    assert redact_evidence_text(expected) == expected


def test_adjacent_quoted_urls_preserve_each_public_query_and_json_boundary():
    source = '{"a":"https://example.test/one?token=canyon&findingId=1","b":"https://example.test/two?password=meadow&findingId=2"}'
    expected = '{"a":"https://example.test/one?token=[REDACTED]&findingId=1","b":"https://example.test/two?password=[REDACTED]&findingId=2"}'
    transformed = redact_evidence_text(source)
    assert transformed == expected
    assert json.loads(transformed) == json.loads(expected)
    assert redact_evidence_text(transformed) == transformed


def test_adjacent_url_does_not_exempt_a_subsequent_url_valued_secret():
    source = '{"a":"https://example.test/one?token=canyon&findingId=1","PASSWORD":"https://example.test/two?value=meadow&private=lantern"}'
    expected = '{"a":"https://example.test/one?token=[REDACTED]&findingId=1","PASSWORD":"[REDACTED]"}'
    assert redact_evidence_text(source) == expected


def test_many_adjacent_url_fields_keep_typed_reproduction_context():
    source = json.dumps(
        {
            str(i): f"https://example.test/{i}?token=canyon&findingId={i}"
            for i in range(500)
        },
        separators=(",", ":"),
    )
    expected = {
        str(i): f"https://example.test/{i}?token=[REDACTED]&findingId={i}"
        for i in range(500)
    }
    transformed = redact_evidence_text(source)
    assert json.loads(transformed) == expected
    assert redact_evidence_text(transformed) == transformed


def test_path_heavy_evidence_preserves_paths_and_masks_following_credentials():
    paths = "/tmp/reproduction/path/file.txt\n" * 2000
    source = (
        paths
        + "PASSWORD=/tmp/canyon-meadow-lantern\nhttps://example.test/check?token=canyon&findingId=abc123"
    )
    expected = (
        paths
        + "PASSWORD=[REDACTED]\nhttps://example.test/check?token=[REDACTED]&findingId=abc123"
    )
    assert redact_evidence_text(source) == expected
    assert redact_evidence_text(expected) == expected
