"""Streaming evidence must not expose credentials before final redaction."""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from moonmind.workflows.temporal.runtime.command_runner import run_runtime_command


async def _stream(payload: bytes, *, limit=None):
    observed = {"stdout": [], "stderr": []}

    async def on_output(stream, chunk):
        observed[stream].append(chunk)

    code, stdout, stderr = await run_runtime_command(
        (
            sys.executable,
            "-c",
            (
                "import sys; payload = sys.stdin.buffer.read(); "
                "sys.stdout.buffer.write(payload); sys.stderr.buffer.write(payload)"
            ),
        ),
        input_bytes=payload,
        on_output=on_output,
        timeout_seconds=10,
        output_limit_bytes=limit,
    )
    assert code == 0
    assert stdout == stderr
    assert observed["stdout"] == observed["stderr"]
    return stdout, observed["stdout"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        b"Bearer\ncanyon\n",
        b"Authorization: Bearer\ncanyon\n",
        b"Proxy-Authorization:\nBasic\ncanyon\n",
        b"password\n=\ncanyon\n",
        b'"password"\n:\n"canyon"\n',
        b"password=\n\n\ncanyon\n",
        b'password="canyon\nmeadow"\n',
        b"password='canyon\nmeadow'\n",
        b"-----BEGIN PRIVATE KEY-----\ncanyon\nmeadow\n-----END PRIVATE KEY-----\n",
        b"-----BEGIN RSA PRIVATE KEY-----\ncanyon\nmeadow\n-----END RSA PRIVATE KEY-----\n",
        b'password=\n{"opaque": "canyon", "count": 2}\n',
        b'password=\n["canyon", "meadow"]\n',
        b'password=\n{\n  "opaque": "canyon",\n  "count": 2\n}\n',
        b'password=\n[\n  "canyon",\n  "meadow"\n]\n',
        b'"password"\n:\n{\n  "opaque": "canyon"\n}\n',
    ],
    ids=[
        "bearer-lines",
        "authorization-lines",
        "proxy-lines",
        "bare-lines",
        "quoted-key-lines",
        "blank-lines",
        "double-quoted",
        "single-quoted",
        "private-key",
        "rsa-key",
        "object-value",
        "array-value",
        "pretty-object",
        "pretty-array",
        "quoted-key-object",
    ],
)
async def test_streamed_credential_fragments_are_masked_before_callback(source):
    output, chunks = await _stream(b"starting\n" + source + b"finished\n")

    assert chunks[0] == b"starting\n"
    assert chunks[-1] == b"finished\n"
    assert b"canyon" not in output
    assert b"meadow" not in output
    assert all(b"canyon" not in chunk and b"meadow" not in chunk for chunk in chunks)
    assert b"REDACTED" in b"".join(chunks)


@pytest.mark.asyncio
async def test_streamed_multiline_json_keeps_sensitive_containers_owned():
    payload = {
        "authorization": {"opaque": "canyon"},
        "accessToken": ["meadow", 812345],
        "native": {"uid": "resource-123", "count": 7},
    }
    output, chunks = await _stream((json.dumps(payload, indent=2) + "\n").encode())

    expected = {
        "authorization": "[REDACTED]",
        "accessToken": "[REDACTED]",
        "native": payload["native"],
    }
    assert json.loads(output) == expected
    assert json.loads(b"".join(chunks)) == expected
    assert all(b"canyon" not in chunk and b"meadow" not in chunk for chunk in chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        b'password="canyon\nmeadow',
        b"-----BEGIN PRIVATE KEY-----\ncanyon\nmeadow",
        b'{"authorization": {\n"opaque": "canyon"',
        b"Bearer\n",
        b"password\n=\n",
        b'password=\n{\n"opaque": "canyon"',
        b'password=\n[\n"canyon"',
    ],
    ids=[
        "quoted-value",
        "private-key",
        "json-record",
        "bearer-prefix",
        "assignment-prefix",
        "object-value",
        "array-value",
    ],
)
async def test_streamed_unclosed_secrets_fail_closed_at_eof(source):
    output, chunks = await _stream(b"starting\n" + source)

    assert chunks[0] == b"starting\n"
    assert b"canyon" not in output
    assert b"meadow" not in output
    assert b"canyon" not in b"".join(chunks)
    assert b"meadow" not in b"".join(chunks)
    assert b"withheld" in b"".join(chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        b'password="' + b"canyon\n" * 10000 + b'meadow"\n',
        b"-----BEGIN PRIVATE KEY-----\n" + b"canyon\n" * 10000 + b"meadow\n",
        b'password="canyon' + b"x" * 70000 + b'\nmeadow"\n',
    ],
    ids=["quoted-lines", "private-key-lines", "oversized-line"],
)
async def test_streamed_oversized_secrets_stay_closed_without_retaining_payload(source):
    output, chunks = await _stream(b"starting\n" + source, limit=128)

    assert chunks[0] == b"starting\n"
    assert len(output) <= 128
    assert b"canyon" not in output
    assert b"meadow" not in output
    assert b"canyon" not in b"".join(chunks)
    assert b"meadow" not in b"".join(chunks)
    assert len(b"".join(chunks)) < 256
    assert b"withheld" in b"".join(chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "progress", ["working", "[build] running", "{status} ready", "", " \t"]
)
async def test_streamed_safe_progress_does_not_wait_for_exit(progress):
    observed = asyncio.Event()
    chunks = []

    async def on_output(stream, chunk):
        chunks.append((stream, chunk))
        observed.set()

    task = asyncio.create_task(
        run_runtime_command(
            (
                sys.executable,
                "-c",
                f"import time; print({progress!r}, flush=True); time.sleep(30)",
            ),
            on_output=on_output,
        )
    )
    try:
        await asyncio.wait_for(observed.wait(), timeout=2)
        assert chunks == [("stdout", (progress + "\n").encode())]
        assert not task.done()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_streamed_unclosed_secret_is_not_published_on_cancellation():
    ready = asyncio.Event()
    observed = {"stdout": [], "stderr": []}

    async def on_output(stream, chunk):
        observed[stream].append(chunk)
        if stream == "stderr":
            ready.set()

    task = asyncio.create_task(
        run_runtime_command(
            (
                sys.executable,
                "-c",
                (
                    "import sys, time; print('password=\"canyon', flush=True); "
                    "print('meadow', flush=True); "
                    "print('ready', file=sys.stderr, flush=True); time.sleep(30)"
                ),
            ),
            on_output=on_output,
        )
    )
    try:
        await asyncio.wait_for(ready.wait(), timeout=2)
        assert observed["stderr"] == [b"ready\n"]
        assert b"canyon" not in b"".join(observed["stdout"])
        assert b"meadow" not in b"".join(observed["stdout"])
        assert not task.done()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
    assert b"canyon" not in b"".join(observed["stdout"])


@pytest.mark.asyncio
async def test_streamed_unclosed_secret_is_not_published_on_timeout():
    chunks = []

    async def on_output(stream, chunk):
        chunks.append(chunk)

    with pytest.raises(TimeoutError):
        await run_runtime_command(
            (
                sys.executable,
                "-c",
                (
                    "import time; print('password=\"canyon', flush=True); "
                    "print('meadow', flush=True); time.sleep(30)"
                ),
            ),
            on_output=on_output,
            timeout_seconds=0.5,
        )
    assert b"canyon" not in b"".join(chunks)
    assert b"meadow" not in b"".join(chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        b"Bearer\ncanyon",
        b'password="secret"',
        b"password=secret",
        b"password=\ncanyon",
        b'password="canyon\nmeadow"',
        b"-----BEGIN PRIVATE KEY-----\ncanyon\n-----END PRIVATE KEY-----",
    ],
    ids=[
        "bearer-lines",
        "quoted-value",
        "bare-value",
        "split-value",
        "quoted-lines",
        "private-key",
    ],
)
async def test_streamed_complete_secret_at_eof_is_redacted_not_withheld(source):
    output, chunks = await _stream(source)

    assert b"canyon" not in output
    assert b"meadow" not in output
    assert b"withheld" not in output
    assert b"REDACTED" in output
    assert b"".join(chunks) == output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "head,tail",
    [
        (b"password=\n", b"canyon\n"),
        (b"{\n", b'"password": "canyon"\n}\n'),
    ],
    ids=["assignment", "json"],
)
@pytest.mark.parametrize("ending", ["complete", "eof", "overflow"])
async def test_streamed_blank_continuations_require_linear_classification(
    monkeypatch, head, tail, ending
):
    from moonmind.workflows.temporal.runtime import command_runner

    scanned = {"credential": 0, "json": 0}
    classify = command_runner.command_secret_continuation
    decode = json.loads

    def count_credential_text(text):
        scanned["credential"] += len(text)
        return classify(text)

    def count_json_text(text, *args, **kwargs):
        scanned["json"] += len(text)
        return decode(text, *args, **kwargs)

    monkeypatch.setattr(
        command_runner, "command_secret_continuation", count_credential_text
    )
    monkeypatch.setattr(json, "loads", count_json_text)
    line_count = 17000 if ending == "overflow" else 1000
    source = head + b" \t\r\n" * line_count + (b"" if ending == "eof" else tail)

    output, chunks = await _stream(source)

    assert b"canyon" not in output
    assert b"canyon" not in b"".join(chunks)
    assert (b"REDACTED" if ending == "complete" else b"withheld") in output
    if ending != "complete":
        assert chunks == [output]
        assert len(output) < 256
    # Both subprocess streams and final canonical sanitization are counted.
    # Re-reading each growing prefix makes this quadratic in blank lines.
    assert scanned["credential"] <= 16 * len(source)
    assert scanned["json"] <= 16 * len(source)
