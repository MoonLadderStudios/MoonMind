"""Bound entire SSE frames independently of transport and line boundaries."""

from __future__ import annotations

import json
from contextlib import aclosing

import httpx
import pytest

from moonmind.workflows.adapters.omnigent_client import (
    OmnigentClientError,
    OmnigentHttpClient,
)


class _CountingWire(httpx.AsyncByteStream):
    def __init__(self, body: bytes, *, fragment_bytes: int | None = 4093):
        self.body = body
        self.fragment_bytes = fragment_bytes
        self.bytes_delivered = 0

    async def __aiter__(self):
        size = self.fragment_bytes or len(self.body)
        for start in range(0, len(self.body), size):
            chunk = self.body[start : start + size]
            self.bytes_delivered += len(chunk)
            yield chunk


def _client(wire: _CountingWire) -> OmnigentHttpClient:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=wire
        )

    return OmnigentHttpClient(
        base_url="https://omnigent.test", transport=httpx.MockTransport(handle)
    )


def _data_line(*, delta_bytes: int) -> tuple[dict, bytes]:
    event = {"type": "response.output_text.delta", "delta": "x" * delta_bytes}
    return event, ("data: " + json.dumps(event)).encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("line_kind", ["data", "metadata"])
async def test_stream_rejects_aggregate_frame_before_consuming_full_body(
    line_kind: str,
) -> None:
    _, line = _data_line(delta_bytes=100_000)
    if line_kind == "metadata":
        # Comments and SSE metadata contribute to the same wire frame even
        # though the current single-JSON data-line parser does not emit them.
        line = b":" + b"x" * 100_000 + b"\nevent: response.heartbeat\nid: pending"
    body = (line + b"\n") * 85
    assert len(body) > 8 * 1024 * 1024
    wire = _CountingWire(body)
    emitted = []
    with pytest.raises(OmnigentClientError, match="exceeds bounded frame size"):
        async for event in _client(wire).stream_events("frame-overflow"):
            emitted.append(event)
    assert wire.bytes_delivered < 8 * 1024 * 1024
    assert wire.bytes_delivered < len(body)
    if line_kind == "data":
        assert len(emitted) < 85
    else:
        assert emitted == []


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix_kind", ["data", "comment"])
async def test_terminal_event_is_not_yielded_before_oversized_frame_is_validated(
    suffix_kind: str,
) -> None:
    terminal = b'data: {"type": "response.completed"}\n'
    _, suffix = _data_line(delta_bytes=100_000)
    if suffix_kind == "comment":
        suffix = b":" + b"x" * 100_000
    body = terminal + (suffix + b"\n") * 85 + b"\n"
    wire = _CountingWire(body)
    # Execution stops reading after the first terminal event. Its first anext
    # must validate the entire frame, including suffixes arriving later.
    with pytest.raises(OmnigentClientError, match="exceeds bounded frame size"):
        async with aclosing(_client(wire).stream_events("terminal-overflow")) as events:
            await anext(events)
    assert wire.bytes_delivered < len(body)


@pytest.mark.asyncio
async def test_bounded_frame_at_true_eof_preserves_data_line_compatibility() -> None:
    event, line = _data_line(delta_bytes=100_000)
    wire = _CountingWire(line + b"\n" + line)
    events = [event async for event in _client(wire).stream_events("bounded-eof")]
    assert events == [event, event]


@pytest.mark.asyncio
@pytest.mark.parametrize("delimiter", [b"\n", b""], ids=["blank-delimited", "eof"])
async def test_terminal_event_is_not_yielded_before_malformed_data_in_same_frame(
    delimiter: bytes,
) -> None:
    body = b'data: {"type": "response.completed"}\ndata: not-json\n' + delimiter
    wire = _CountingWire(body, fragment_bytes=7)
    with pytest.raises(OmnigentClientError, match="Malformed Omnigent SSE frame"):
        async with aclosing(
            _client(wire).stream_events("terminal-malformed")
        ) as events:
            await anext(events)


@pytest.mark.asyncio
async def test_stream_counts_unterminated_tail_with_earlier_frame_lines() -> None:
    _, line = _data_line(delta_bytes=3_200_000)
    # Each JSON data line fits individually, including the final EOF tail;
    # their combined frame crosses the fixed 6,358,192-byte wire bound.
    wire = _CountingWire(line + b"\n" + line)
    with pytest.raises(OmnigentClientError, match="exceeds bounded frame size"):
        _ = [event async for event in _client(wire).stream_events("frame-eof")]


@pytest.mark.asyncio
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["lf", "crlf"])
async def test_blank_delimiters_reset_budget_for_coalesced_valid_frames(
    newline: bytes,
) -> None:
    event, line = _data_line(delta_bytes=100_000)
    frame = b"event: response.output_text.delta" + newline + line + newline * 2
    # One coalesced transport read exceeds the aggregate budget in total,
    # while each of its 85 independently delimited frames remains bounded.
    wire = _CountingWire(frame * 85, fragment_bytes=None)
    assert len(wire.body) > 8 * 1024 * 1024
    events = [event async for event in _client(wire).stream_events("valid-frames")]
    assert events == [event] * 85
    assert wire.bytes_delivered == len(wire.body)


@pytest.mark.asyncio
async def test_exact_bound_frame_accepts_split_crlf_delimiter() -> None:
    wire_budget = 6_358_192
    _, empty_line = _data_line(delta_bytes=0)
    event, line = _data_line(delta_bytes=wire_budget - len(empty_line) - 2)
    # Align the CR of the final blank CRLF line to a bounded read boundary.
    # It is a delimiter, so its temporary pending byte must not overflow an
    # otherwise exactly bounded frame before its LF arrives in the next read.
    prefix_size = 64 * 1024 - (wire_budget + 1) % (64 * 1024)
    prefix_event, prefix_line = _data_line(
        delta_bytes=prefix_size - len(empty_line) - 4
    )
    body = prefix_line + b"\r\n\r\n" + line + b"\r\n\r\n"
    wire = _CountingWire(body, fragment_bytes=64 * 1024)
    events = [event async for event in _client(wire).stream_events("exact-frame")]
    assert events == [prefix_event, event]
