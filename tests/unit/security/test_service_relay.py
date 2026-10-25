from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock

import pytest

from moonmind.security.service_relay import (
    IMPLEMENTATION_DIGEST,
    FixedTargetRelay,
    RelayConfig,
    check_health,
)


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def config(**updates):
    value = {
        "targetHost": "127.0.0.1",
        "targetPort": port(),
        "listenHost": "127.0.0.1",
        "listenPort": port(),
        "protocols": ["tcp", "udp"],
        "maxConnections": 2,
        "idleSeconds": 1,
        "maxDatagramBytes": 1024,
    }
    value.update(updates)
    return RelayConfig.from_mapping(value)


@pytest.mark.parametrize(
    "updates",
    [
        {"targetHost": "example.com"},
        {"targetHost": "::1"},
        {"targetHost": "0.0.0.0"},
        {"listenHost": "0.0.0.0"},
        {"targetPort": True},
        {"targetPort": 0},
        {"listenPort": 65536},
        {"maxConnections": 0},
        {"maxConnections": 1025},
        {"idleSeconds": 0},
        {"maxDatagramBytes": 65508},
        {"protocols": []},
        {"protocols": ["http"]},
        {"protocols": ["tcp", "tcp"]},
        {"extra": "ignored"},
    ],
)
def test_config_rejects_unbounded_or_ambiguous_policy(updates):
    with pytest.raises(ValueError):
        config(**updates)


def test_config_digest_normalizes_protocol_order():
    first = config()
    other = RelayConfig.from_mapping(dict(first.to_mapping(), protocols=["udp", "tcp"]))
    assert first.digest == other.digest
    assert first.digest.startswith("sha256:")


@pytest.mark.asyncio
async def test_tcp_fixed_target_cap_idle_and_cleanup(tmp_path, monkeypatch):
    opened = 0
    writers = []

    async def echo(reader, writer):
        nonlocal opened
        opened += 1
        writers.append(writer)
        try:
            while chunk := await reader.read(4096):
                writer.write(chunk)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    cfg = config(
        targetPort=server.sockets[0].getsockname()[1],
        protocols=["tcp"],
        maxConnections=1,
    )
    relay = FixedTargetRelay(cfg, health_socket=tmp_path / "health.sock")
    monkeypatch.setattr(relay, "_start_health", AsyncMock())
    await relay.start()
    try:
        reader, writer = await asyncio.open_connection(cfg.listen_host, cfg.listen_port)
        writer.write(b"only fixed destination")
        await writer.drain()
        assert (
            await asyncio.wait_for(reader.readexactly(22), 1)
            == b"only fixed destination"
        )
        refused_reader, refused_writer = await asyncio.open_connection(
            cfg.listen_host, cfg.listen_port
        )
        assert await asyncio.wait_for(refused_reader.read(1), 1) == b""
        refused_writer.close()
        await refused_writer.wait_closed()
        assert opened == 1
        assert await asyncio.wait_for(reader.read(1), 3) == b""
        writer.close()
        await writer.wait_closed()
    finally:
        await relay.close()
        server.close()
        await server.wait_closed()
        for writer in writers:
            writer.close()
    assert relay.active_connections == 0
    assert not (tmp_path / "health.sock").exists()


class DatagramQueue(asyncio.DatagramProtocol):
    def __init__(self):
        self.queue = asyncio.Queue()
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.queue.put_nowait((data, addr))


@pytest.mark.asyncio
async def test_udp_fixed_target_ignores_foreign_response_and_bounds_sessions(
    tmp_path, monkeypatch
):
    loop = asyncio.get_running_loop()
    upstream, observed = await loop.create_datagram_endpoint(
        DatagramQueue, local_addr=("127.0.0.1", 0)
    )
    cfg = config(
        targetPort=upstream.get_extra_info("sockname")[1],
        protocols=["udp"],
        maxConnections=1,
        maxDatagramBytes=8,
    )
    relay = FixedTargetRelay(cfg, health_socket=tmp_path / "health.sock")
    monkeypatch.setattr(relay, "_start_health", AsyncMock())
    await relay.start()
    client, received = await loop.create_datagram_endpoint(
        DatagramQueue, local_addr=("127.0.0.1", 0)
    )
    other, _rejected = await loop.create_datagram_endpoint(
        DatagramQueue, local_addr=("127.0.0.1", 0)
    )
    attacker, _ = await loop.create_datagram_endpoint(
        DatagramQueue, local_addr=("127.0.0.1", 0)
    )
    try:
        destination = (cfg.listen_host, cfg.listen_port)
        client.sendto(b"first", destination)
        data, relay_addr = await asyncio.wait_for(observed.queue.get(), 1)
        assert data == b"first"
        attacker.sendto(b"evil", relay_addr)
        upstream.sendto(b"good", relay_addr)
        assert (await asyncio.wait_for(received.queue.get(), 1))[0] == b"good"
        client.sendto(b"oversized", destination)
        other.sendto(b"capped", destination)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(observed.queue.get(), 0.1)
        upstream.sendto(b"oversized", relay_addr)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(received.queue.get(), 0.1)
        assert relay.active_connections == 1
        await asyncio.sleep(1.1)
        assert relay.active_connections == 0
        other.sendto(b"new", destination)
        assert (await asyncio.wait_for(observed.queue.get(), 1))[0] == b"new"
    finally:
        await relay.close()
        for transport in (upstream, client, other, attacker):
            transport.close()
    assert relay.active_connections == 0


@pytest.mark.asyncio
async def test_failed_start_cleans_owned_listeners_and_health(tmp_path):
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    cfg = config(listenPort=server.sockets[0].getsockname()[1])
    relay = FixedTargetRelay(cfg, health_socket=tmp_path / "health.sock")
    try:
        with pytest.raises(OSError):
            await relay.start()
        assert not (tmp_path / "health.sock").exists()
        assert relay.active_connections == 0
    finally:
        await relay.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_live_health_reports_loaded_config_and_implementation(tmp_path):
    try:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except PermissionError:
        pytest.skip("execution environment denies Unix socket creation")
    probe.close()
    cfg = config(protocols=["tcp"])
    relay = FixedTargetRelay(cfg, health_socket=tmp_path / "health.sock")
    await relay.start()
    try:
        assert await check_health(cfg, tmp_path / "health.sock") == {
            "configDigest": cfg.digest,
            "implementationDigest": IMPLEMENTATION_DIGEST,
        }
        wrong = RelayConfig.from_mapping(dict(cfg.to_mapping(), maxConnections=3))
        with pytest.raises(ValueError):
            await check_health(wrong, tmp_path / "health.sock")
    finally:
        await relay.close()
    assert not (tmp_path / "health.sock").exists()


@pytest.mark.asyncio
async def test_tcp_half_close_preserves_upstream_final_response(tmp_path, monkeypatch):
    async def after_eof(reader, writer):
        body = await reader.read()
        writer.write(body.upper())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(after_eof, "127.0.0.1", 0)
    cfg = config(targetPort=server.sockets[0].getsockname()[1], protocols=["tcp"])
    relay = FixedTargetRelay(cfg, health_socket=tmp_path / "health.sock")
    monkeypatch.setattr(relay, "_start_health", AsyncMock())
    await relay.start()
    try:
        reader, writer = await asyncio.open_connection(cfg.listen_host, cfg.listen_port)
        writer.write(b"final response")
        await writer.drain()
        writer.write_eof()
        assert await asyncio.wait_for(reader.read(), 1) == b"FINAL RESPONSE"
        writer.close()
        await writer.wait_closed()
    finally:
        await relay.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_cancellation_closes_active_streams_and_reused_udp_peer_survives(
    tmp_path, monkeypatch
):
    upstream_closed = asyncio.Event()

    async def wait_for_close(reader, writer):
        try:
            await reader.read()
        finally:
            upstream_closed.set()
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(wait_for_close, "127.0.0.1", 0)
    cfg = config(targetPort=server.sockets[0].getsockname()[1], protocols=["tcp"])
    relay = FixedTargetRelay(cfg, health_socket=tmp_path / "health.sock")
    monkeypatch.setattr(relay, "_start_health", AsyncMock())
    await relay.start()
    reader, writer = await asyncio.open_connection(cfg.listen_host, cfg.listen_port)
    writer.write(b"active")
    await writer.drain()
    await asyncio.sleep(0.02)
    try:
        await relay.close()
        assert await asyncio.wait_for(reader.read(), 1) == b""
        await asyncio.wait_for(upstream_closed.wait(), 1)
        assert relay.active_connections == 0
    finally:
        writer.close()
        await writer.wait_closed()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_stale_udp_cleanup_does_not_close_replacement_session(tmp_path):
    relay = FixedTargetRelay(config(), health_socket=tmp_path / "health.sock")
    first = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    second = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    task = asyncio.create_task(asyncio.sleep(10))
    timer = asyncio.get_running_loop().call_later(10, lambda: None)
    peer = ("127.0.0.1", 12345)
    relay._udp_sessions[peer] = (second, task, timer)
    try:
        relay._drop_udp(peer, first)
        assert relay.active_connections == 1
        assert second.fileno() >= 0
    finally:
        first.close()
        await relay.close()


def test_self_forwarding_endpoint_is_refused():
    with pytest.raises(ValueError):
        config(targetPort=12345, listenPort=12345)


def test_cli_failure_never_echoes_configuration(tmp_path, capsys):
    from moonmind.security.service_relay import main

    path = tmp_path / "config.json"
    path.write_text('{"secret": "do-not-echo", "targetHost": "private.internal"}')
    assert main(["--config", str(path), "--check"]) == 1
    assert (
        capsys.readouterr().out == "service relay configuration or readiness failed\n"
    )
