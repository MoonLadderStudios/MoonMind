"""Local sockets prove dial evidence; injected errno tests are not routing proof."""

import asyncio
import errno
import json
import socket
import time

import pytest

from moonmind.security.native_tcp_gateway import NativeTCPConfig, NativeTCPGateway


def config(port, **overrides):
    value = {
        "leaseId": "dial-test",
        "deadlineUnix": time.time() + 10,
        "listenHost": "127.0.0.1",
        "listenPort": 0,
        "clientIp": "127.0.0.1",
        "endpoints": [
            {"authority": f"approved.test:{port}", "address": "127.0.0.1", "port": port}
        ],
        "maxConnections": 8,
        "maxConcurrentConnections": 2,
        "maxConnectionsPerSecond": 8,
        "maxBytes": 4096,
        "handshakeSeconds": 1,
        "connectSeconds": 0.1,
        "idleSeconds": 1,
    }
    value.update(overrides)
    return NativeTCPConfig.from_mapping(value)


async def request(gateway, port):
    reader, writer = await asyncio.open_connection("127.0.0.1", gateway.bound_port)
    writer.write(f"CONNECT approved.test:{port} HTTP/1.1\r\n\r\n".encode())
    await writer.drain()
    return reader, writer


@pytest.mark.asyncio
async def test_real_refusal_is_persisted_before_fixed_response(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    # Bound but not listening: no port reuse race with another local listener.
    gateway = NativeTCPGateway(config(port), state_directory=tmp_path)
    await gateway.start()
    try:
        reader, writer = await request(gateway, port)
        response = await reader.readuntil(b"\r\n\r\n")
        assert (
            response
            == b"HTTP/1.1 502 Bad Gateway\r\nX-MoonMind-Dial-Outcome: refused\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        )
        saved = json.loads((tmp_path / "snapshot.json").read_bytes())
        assert saved["dialOutcomes"] == {"refused": 1}
        assert saved["attempts"] == 1
        assert saved["transportErrors"] == saved["denials"] == {}
        writer.close()
        await writer.wait_closed()
    finally:
        await gateway.close()
        sock.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,outcome",
    [
        (errno.ENETUNREACH, "unreachable"),
        (errno.EHOSTUNREACH, "unreachable"),
        (errno.ETIMEDOUT, "timed_out"),
        (errno.EPERM, "transport_error"),
    ],
)
async def test_dial_errno_mapping_has_no_exception_or_target_text(
    tmp_path, monkeypatch, error, outcome
):
    gateway = NativeTCPGateway(config(12345), state_directory=tmp_path)
    await gateway.start()
    original = asyncio.open_connection

    async def fail_target(host, port, **kwargs):
        if port == 12345:
            raise OSError(error, "RAW SECRET target.example")
        return await original(host, port, **kwargs)

    monkeypatch.setattr(asyncio, "open_connection", fail_target)
    try:
        reader, writer = await request(gateway, 12345)
        response = await reader.readuntil(b"\r\n\r\n")
        assert f"X-MoonMind-Dial-Outcome: {outcome}\r\n".encode() in response
        assert b"RAW" not in response and b"target.example" not in response
        snapshot = gateway.snapshot()
        assert snapshot["dialOutcomes"] == {outcome: 1}
        assert snapshot["denials"] == {}
        assert snapshot["transportErrors"] == (
            {"io": 1} if outcome == "transport_error" else {}
        )
        writer.close()
        await writer.wait_closed()
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("absolute_expiry", [False, True])
async def test_dial_timeout_is_not_absolute_authority_expiry(
    tmp_path, monkeypatch, absolute_expiry
):
    gateway = NativeTCPGateway(
        config(
            12345,
            connectSeconds=0.02,
            deadlineUnix=time.time() + (0.01 if absolute_expiry else 10),
        ),
        state_directory=tmp_path,
    )
    original = asyncio.open_connection

    async def delay_target(host, port, **kwargs):
        if port == 12345:
            await asyncio.sleep(1)
        return await original(host, port, **kwargs)

    monkeypatch.setattr(asyncio, "open_connection", delay_target)
    await gateway.start()
    try:
        reader, writer = await request(gateway, 12345)
        response = await reader.read()
        assert (b"timed_out" in response) is not absolute_expiry
        assert gateway.snapshot()["dialOutcomes"] == (
            {} if absolute_expiry else {"timed_out": 1}
        )
        assert gateway.snapshot()["denials"] == {}
        writer.close()
        await writer.wait_closed()
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_unpersisted_dial_observation_is_never_reported(tmp_path, monkeypatch):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    gateway = NativeTCPGateway(config(port), state_directory=tmp_path)
    await gateway.start()
    persist = gateway._persist

    def fail_outcome():
        if gateway.snapshot()["dialOutcomes"]:
            raise OSError("evidence unavailable")
        persist()

    monkeypatch.setattr(gateway, "_persist", fail_outcome)
    try:
        reader, writer = await request(gateway, port)
        assert await reader.read() == b""
        assert gateway._closed
        saved = json.loads((tmp_path / "snapshot.json").read_bytes())
        assert saved["dialOutcomes"] == {}
        writer.transport.abort()
    finally:
        monkeypatch.setattr(gateway, "_persist", persist)
        await gateway.close()
        sock.close()


@pytest.mark.asyncio
async def test_denial_response_wait_keeps_writer_owned_until_original_deadline(
    tmp_path, monkeypatch
):
    gateway = NativeTCPGateway(
        config(12345, deadlineUnix=time.time() + 0.1), state_directory=tmp_path
    )
    entered = asyncio.Event()
    drain = asyncio.StreamWriter.drain

    async def block_gateway_drain(writer):
        if writer in gateway._writers:
            entered.set()
            await asyncio.sleep(10)
        else:
            await drain(writer)

    monkeypatch.setattr(asyncio.StreamWriter, "drain", block_gateway_drain)
    await gateway.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", gateway.bound_port)
        writer.write(b"CONNECT forbidden.test:80 HTTP/1.1\r\n\r\n")
        await writer.drain()
        await asyncio.wait_for(entered.wait(), 0.5)
        assert gateway._tasks and gateway._writers
        await asyncio.wait_for(reader.read(), 0.5)
        for _ in range(50):
            if not gateway._tasks:
                break
            await asyncio.sleep(0.005)
        assert gateway._closed
        assert not gateway._tasks and not gateway._writers
        assert gateway.snapshot()["attempts"] == 0
        writer.transport.abort()
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        "X-MoonMind-Close-On-EOF: 0",
        "X-MoonMind-Close-On-EOF: true",
        "X-MoonMind-Close-On-EOF: 1\r\nX-MoonMind-Close-On-EOF: 1",
        "X-MoonMind-Close-On-EOF: 1\r\nProxy-Connection: Keep-Alive",
    ],
)
async def test_close_hint_has_one_exact_header_form(tmp_path, header):
    from moonmind.security.native_tcp_gateway import CONNECT_FAILURES

    gateway = NativeTCPGateway(config(12345), state_directory=tmp_path)
    await gateway.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", gateway.bound_port)
        writer.write(
            f"CONNECT approved.test:12345 HTTP/1.1\r\nHost: approved.test:12345\r\n{header}\r\n\r\n".encode()
        )
        await writer.drain()
        assert await reader.read() == CONNECT_FAILURES["denied"]
        assert gateway.snapshot()["attempts"] == 0
        writer.transport.abort()
    finally:
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("close_hint", [False, True])
async def test_opt_in_client_eof_closes_but_generic_half_close_remains_owned(
    tmp_path, close_hint
):
    from moonmind.security.native_tcp_gateway import (
        CONNECT_CLOSE_ON_EOF,
        CONNECT_SUCCESS,
    )

    release = asyncio.Event()

    async def target(reader, writer):
        try:
            writer.write(b"hello")
            await writer.drain()
            await release.wait()
        finally:
            writer.transport.abort()

    server = await asyncio.start_server(target, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    gateway = NativeTCPGateway(config(port), state_directory=tmp_path)
    await gateway.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", gateway.bound_port)
        hint = f"{CONNECT_CLOSE_ON_EOF}\r\n" if close_hint else ""
        writer.write(
            f"CONNECT approved.test:{port} HTTP/1.1\r\nHost: approved.test:{port}\r\n{hint}\r\n".encode()
        )
        await writer.drain()
        assert await reader.readuntil(b"\r\n\r\n") == CONNECT_SUCCESS
        assert await reader.readexactly(5) == b"hello"
        writer.write_eof()
        if close_hint:
            assert await asyncio.wait_for(reader.read(), 0.5) == b""
            assert gateway.snapshot()["activeConnections"] == 0
        else:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(reader.read(), 0.1)
            assert gateway.snapshot()["activeConnections"] == 1
        assert (
            gateway.snapshot()["transportErrors"] == gateway.snapshot()["denials"] == {}
        )
        writer.transport.abort()
    finally:
        release.set()
        await gateway.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_opt_in_target_eof_preserves_payload_before_releasing_slot(tmp_path):
    from moonmind.security.native_tcp_gateway import (
        CONNECT_CLOSE_ON_EOF,
        CONNECT_SUCCESS,
    )

    payload = b"x" * 32768

    async def target(reader, writer):
        writer.write(payload)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(target, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    gateway = NativeTCPGateway(
        config(port, maxBytes=len(payload)), state_directory=tmp_path
    )
    await gateway.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", gateway.bound_port)
        writer.write(
            f"CONNECT approved.test:{port} HTTP/1.1\r\nHost: approved.test:{port}\r\n{CONNECT_CLOSE_ON_EOF}\r\n\r\n".encode()
        )
        await writer.drain()
        assert await reader.readuntil(b"\r\n\r\n") == CONNECT_SUCCESS
        assert await asyncio.wait_for(reader.read(), 1) == payload
        assert gateway.snapshot()["activeConnections"] == 0
        assert gateway.snapshot()["bytesReserved"] == len(payload)
        assert (
            gateway.snapshot()["transportErrors"] == gateway.snapshot()["denials"] == {}
        )
        writer.transport.abort()
    finally:
        await gateway.close()
        server.close()
        await server.wait_closed()
