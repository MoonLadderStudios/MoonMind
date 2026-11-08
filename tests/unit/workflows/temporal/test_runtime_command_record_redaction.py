"""Runtime command evidence preserves records without weakening masking."""

from __future__ import annotations

import json
import sys

import pytest

from moonmind.utils.logging import redact_sensitive_payload, redact_sensitive_text
from moonmind.workflows.temporal.runtime.command_runner import run_runtime_command


async def _emit(payload: bytes, *, streaming=False, limit=None):
    observed: dict[str, list[bytes]] = {"stdout": [], "stderr": []}

    async def on_output(stream, data):
        observed[stream].append(data)

    code, stdout, stderr = await run_runtime_command(
        (
            sys.executable,
            "-c",
            (
                "import sys; data = sys.stdin.buffer.read(); "
                "sys.stdout.buffer.write(data); sys.stderr.buffer.write(data)"
            ),
        ),
        input_bytes=payload,
        timeout_seconds=10,
        output_limit_bytes=limit,
        on_output=on_output if streaming else None,
    )
    assert code == 0
    assert stdout == stderr
    if streaming:
        assert observed["stdout"] == observed["stderr"]
    return stdout, observed["stdout"]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("container", ["object", "array"])
async def test_command_preserves_stamp_and_types_while_masking_ref_contents(
    streaming, container
):
    stamp = {
        "ownerRef": "test:credential-recovery-abc:generic",
        "revision": 1,
        "reservationId": "11111111-1111-1111-1111-111111111111",
        "native": {"uid": "resource-123", "observed": [True, None, 7]},
    }
    safe = {**stamp, "ownerRef": "test:credential-recovery-abc:[REDACTED]"}
    payload = stamp if container == "object" else [stamp]
    expected = safe if container == "object" else [safe]

    output, lines = await _emit(
        (json.dumps(payload) + "\n").encode(), streaming=streaming
    )

    assert json.loads(output) == expected
    if streaming:
        assert len(lines) == 1
        assert json.loads(lines[0]) == expected
    repeated, _ = await _emit(output, streaming=streaming)
    assert repeated == output


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_command_masks_sensitive_strings_numbers_and_containers(streaming):
    record = {
        "password": 482901,
        "authorization": {"opaque": "canyon"},
        "accessToken": [123456, "meadow"],
        "apiKey": "lantern",
        "native": {"uid": "resource-123", "count": 12},
    }

    output, lines = await _emit(
        (json.dumps(record) + "\n").encode(), streaming=streaming
    )

    expected = {
        **dict.fromkeys(
            ("password", "authorization", "accessToken", "apiKey"), "[REDACTED]"
        ),
        "native": record["native"],
    }
    assert json.loads(output) == expected
    assert all(
        value not in output
        for value in (b"482901", b"123456", b"canyon", b"meadow", b"lantern")
    )
    if streaming:
        assert json.loads(lines[0]) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,value,allowed",
    [
        ("resetAuthorization", "", True),
        ("resetAuthorization", "operator at 2026-11-08T12:00:00+00:00", True),
        ("resetAuthorization", "operator[bot] at 2026-11-08T12:00:00", True),
        ("resetAuthorization", "operator at 2026-02-30T12:00:00Z", False),
        ("resetAuthorization", "operator at 2026-11-08T12:00:00Z extra", False),
        ("resetAuthorization", "AKIAABCDEFGHIJKLMNOP at 2026-11-08T12:00:00Z", False),
        ("resetAuthorization", "ghp_" + "x" * 24 + " at 2026-11-08T12:00:00Z", False),
        ("resetAuthorization", "Basic canyon", False),
        ("ResetAuthorization", "operator at 2026-11-08T12:00:00Z", False),
        (" resetAuthorization ", "operator at 2026-11-08T12:00:00Z", False),
        ("reset_authorization", "operator at 2026-11-08T12:00:00Z", False),
        ("proxyAuthorization", "operator at 2026-11-08T12:00:00Z", False),
    ],
    ids=[
        "empty-marker",
        "valid-marker",
        "bot-marker",
        "invalid-date",
        "extra-suffix",
        "cloud-canary",
        "github-canary",
        "basic-value",
        "case-alias",
        "space-alias",
        "snake-alias",
        "proxy-alias",
    ],
)
async def test_command_reset_marker_exception_remains_exact(key, value, allowed):
    source = json.dumps({key: value, "count": 2}).encode()

    output, _ = await _emit(source)

    assert json.loads(output) == {key: value if allowed else "[REDACTED]", "count": 2}
    if allowed:
        assert output == source


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_command_preserves_unchanged_record_bytes(streaming):
    source = (
        b'{\n  "ownerRef": "test:recovery:abc",\n  "count": 2,\n'
        b'  "email": "operator@example.test",\n'
        b'  "credentialRef": "credential:profile-prod",\n'
        b'  "native": [true, null, 4.5]\n}\n'
    )

    output, _ = await _emit(source, streaming=streaming)

    assert output == source


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_command_ndjson_records_remain_parseable_after_aggregation(streaming):
    first = {"ownerRef": "test:credential-recovery-abc:generic", "revision": 1}
    second = {"password": "canyon", "count": 2}
    source = (json.dumps(first) + "\n" + json.dumps(second) + "\n").encode()

    output, lines = await _emit(source, streaming=streaming)

    retained = output.splitlines()
    assert len(retained) == 2
    assert json.loads(retained[0]) == {
        **first,
        "ownerRef": "test:credential-recovery-abc:[REDACTED]",
    }
    assert json.loads(retained[1]) == {"password": "[REDACTED]", "count": 2}
    assert b"canyon" not in output
    if streaming:
        assert b"".join(lines) == output
    repeated, _ = await _emit(output, streaming=streaming)
    assert repeated == output


@pytest.mark.asyncio
async def test_streaming_json_records_survive_interleaved_progress_lines():
    source = (
        b'{"ownerRef":"test:credential-recovery-abc:generic","revision":1}\n'
        b'progress complete\n{"password":"canyon","count":2}\n'
    )

    output, lines = await _emit(source, streaming=True)

    retained = output.splitlines()
    assert len(retained) == 3
    assert json.loads(retained[0]) == {
        "ownerRef": "test:credential-recovery-abc:[REDACTED]",
        "revision": 1,
    }
    assert retained[1] == b"progress complete"
    assert json.loads(retained[2]) == {"password": "[REDACTED]", "count": 2}
    assert b"canyon" not in output
    assert b"".join(lines) == output


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_command_masks_credential_text_in_object_keys(streaming):
    source = b'{"password=canyon":"meadow","count":2}\n'

    output, _ = await _emit(source, streaming=streaming)

    record = json.loads(output)
    assert "password=[REDACTED]" in record
    assert record["count"] == 2
    assert b"canyon" not in output and b"meadow" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_command_withholds_colliding_redacted_keys_without_partial_record(
    streaming,
):
    source = b'{"password=canyon":"first","password=meadow":"second"}\n'

    output, lines = await _emit(source, streaming=streaming)

    assert b"withheld" in output.lower()
    assert all(
        value not in output for value in (b"canyon", b"meadow", b"first", b"second")
    )
    if streaming:
        assert b"".join(lines) == output


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("limit", [None, 24])
async def test_command_malformed_output_uses_existing_masking_and_bounds(
    streaming, limit
):
    source = b'prefix {"password":"canyon"\n'

    output, lines = await _emit(source, streaming=streaming, limit=limit)

    expected = redact_sensitive_text(source.decode()).encode()
    if limit is not None:
        expected = expected[:limit]
        assert len(output) <= limit
    assert output == expected
    assert b"canyon" not in output
    if streaming:
        assert b"".join(lines) == output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    ["password=canyon", True, 42, None],
    ids=["string", "boolean", "integer", "null"],
)
async def test_command_json_scalars_remain_json(value):
    source = json.dumps(value).encode()

    output, _ = await _emit(source)

    assert json.loads(output) == (
        "password=[REDACTED]" if isinstance(value, str) else value
    )
    assert b"canyon" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "source",
    [
        b'{"same":"first","same":"second"}\n',
        b'{"value":NaN,"native":"resource"}\n',
        b'{"value":Infinity,"native":"resource"}\n',
        b'{"value":-Infinity,"native":"resource"}\n',
        b"[" * 40 + b'"canyon"' + b"]" * 40 + b"\n",
    ],
    ids=["duplicate-keys", "nan", "infinity", "negative-infinity", "depth-bound"],
)
async def test_command_withholds_invalid_or_unbounded_structured_record(
    source, streaming
):
    output, lines = await _emit(source, streaming=streaming)

    assert b"withheld" in output.lower()
    assert all(
        value not in output for value in (b"first", b"second", b"resource", b"canyon")
    )
    if streaming:
        assert b"".join(lines) == output


@pytest.mark.asyncio
async def test_command_plain_private_key_output_remains_withheld():
    source = (
        b"provider failed\n-----BEGIN PRIVATE KEY-----\n"
        b"canyon-private-material\n-----END PRIVATE KEY-----\n"
        b"password=meadow\n"
    )

    output, _ = await _emit(source)

    assert output == redact_sensitive_text(source.decode()).encode()
    assert b"canyon" not in output and b"meadow" not in output
    assert b"provider failed" in output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        b"Bearer\ncanyon\n",
        b"password\n=\ncanyon\n",
        b'password\n=\n"canyon"\n',
        b"password\n=\n482901\n",
        b'password=\n{"opaque":"canyon"}\n',
        b'authorization\n:\n{"opaque":"canyon"}\n',
    ],
    ids=[
        "bearer-lines",
        "bare-lines",
        "quoted-lines",
        "number-lines",
        "object-lines",
        "authorization-lines",
    ],
)
async def test_streaming_retained_plaintext_keeps_cross_line_credential_masking(source):
    output, _ = await _emit(source, streaming=True)

    assert output == redact_sensitive_text(source.decode()).encode()
    assert b"canyon" not in output
    assert b"482901" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_changed_command_record_preserves_escaped_native_surrogate(streaming):
    source = b'{"password":"canyon","native":"\\ud800","count":2}\n'

    output, lines = await _emit(source, streaming=streaming)

    assert json.loads(output) == {
        "password": "[REDACTED]",
        "native": "\ud800",
        "count": 2,
    }
    assert b"canyon" not in output
    if streaming:
        assert b"".join(lines) == output


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "record",
    [b'{"opaque": "canyon", "other": "meadow"}', b'["canyon", "meadow"]'],
)
async def test_plain_assignment_owns_entire_following_json_value(streaming, record):
    output, _ = await _emit(b"password=\n" + record + b"\n", streaming=streaming)

    assert output == b"password=\n[REDACTED]\n"
    assert b"canyon" not in output and b"meadow" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        "authorizationBlob",
        "authorizationHeader",
        "secretValues",
        "credentials",
        "passwordValue",
        "accessKeyId",
        "tokenValue",
    ],
)
@pytest.mark.parametrize(
    "value", ["canyon", {"opaque": "canyon"}, ["canyon"], 482901, True]
)
async def test_command_field_policy_preserves_assignment_grammar_masking(key, value):
    source = (json.dumps({key: value, "native": {"count": 2}}) + "\n").encode()

    output, lines = await _emit(source, streaming=True)

    assert json.loads(output) == {key: "[REDACTED]", "native": {"count": 2}}
    assert b"canyon" not in output and b"482901" not in output
    assert b"".join(lines) == output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,value",
    [
        ("credentialRef", "secret://foo"),
        ("secretRef", "secret://prod/token"),
        ("apiKey", "secret://profile/key"),
        ("secret", "env://GITHUB_TOKEN"),
        ("credential_source", "vault://prod/github"),
        ("passwordRef", "ref://profile-a"),
        ("secretRef", "profile-a"),
    ],
)
async def test_command_field_policy_preserves_exact_compact_reference(key, value):
    source = (json.dumps({key: value, "revision": 1}) + "\n").encode()

    output, lines = await _emit(source, streaming=True)

    assert output == source
    assert b"".join(lines) == source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,value",
    [
        ("credentialRef", "secret://ghp_" + "x" * 24),
        ("secretRef", "secret://foo?token=canyon"),
        ("apiKey", "secret://foo?token=canyon"),
        ("secretRef", "profile password=canyon"),
        ("secretRef", "secret://home/.codex/auth.json"),
        ("secretRef", "https://operator:canyon@example.test"),
    ],
    ids=[
        "provider-canary",
        "secret-query",
        "key-query",
        "embedded-assignment",
        "auth-path",
        "userinfo",
    ],
)
async def test_command_reference_exception_never_skips_credential_scans(key, value):
    output, lines = await _emit(
        (json.dumps({key: value}) + "\n").encode(), streaming=True
    )

    assert json.loads(output) == {key: "[REDACTED]"}
    assert b"canyon" not in output and b"ghp_" not in output
    assert b"".join(lines) == output


@pytest.mark.asyncio
async def test_command_field_parity_preserves_nonsecret_diagnostics_and_typed_owner():
    diagnostics = {
        "authEnabled": True,
        "ready": False,
        "revision": 3,
        "count": 2,
        "publicKeyFingerprint": "SHA256:public-fingerprint",
        "public_key": "ssh-ed25519 AAAA public comment",
        "email": "operator@example.test",
    }
    source = json.dumps(diagnostics).encode()

    output, _ = await _emit(source)

    assert output == source
    # Command assignment parity does not alter unrelated typed API policies.
    assert redact_sensitive_payload({"secretValues": False}) == {"secretValues": False}
