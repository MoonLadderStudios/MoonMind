"""Actual loopback sockets prove transport budgets, not Docker isolation."""

import asyncio
import json
import socket
import time
from contextlib import asynccontextmanager

import pytest

from moonmind.security.native_tcp_gateway import NativeTCPConfig, NativeTCPGateway


def config(port, **changes):
    value = {
        "leaseId": "test-lease",
        "deadlineUnix": time.time() + 30,
        "listenHost": "127.0.0.1",
        "listenPort": 0,
        "clientIp": "127.0.0.1",
        "endpoints": [
            {"authority": f"approved.test:{port}", "address": "127.0.0.1", "port": port}
        ],
        "maxConcurrentConnections": 2,
        "maxConnections": 10,
        "maxConnectionsPerSecond": 10,
        "maxBytes": 100,
        "handshakeSeconds": 1,
        "connectSeconds": 1,
        "idleSeconds": 1,
    }
    value.update(changes)
    return NativeTCPConfig.from_mapping(value)


@asynccontextmanager
async def fixture(tmp_path, **changes):
    async def echo(reader, writer):
        try:
            while data := await reader.read(1024):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    gateway = NativeTCPGateway(config(port, **changes), state_directory=tmp_path)
    await gateway.start()
    try:
        yield gateway, port
    finally:
        await gateway.close()
        server.close()
        await server.wait_closed()


async def connect(gateway, port, request=None):
    reader, writer = await asyncio.open_connection("127.0.0.1", gateway.bound_port)
    writer.write(
        request
        or f"CONNECT approved.test:{port} HTTP/1.1\r\nHost: approved.test:{port}\r\n\r\n".encode()
    )
    await writer.drain()
    return reader, writer


@pytest.mark.asyncio
async def test_connect_echo_and_persistent_restart_refusal(tmp_path):
    async with fixture(tmp_path) as (gateway, port):
        reader, writer = await connect(gateway, port)
        assert (
            await reader.readuntil(b"\r\n\r\n")
            == b"HTTP/1.1 200 Connection Established\r\n\r\n"
        )
        writer.write(b"hello")
        await writer.drain()
        assert await reader.readexactly(5) == b"hello"
        assert gateway.snapshot()["bytesReserved"] == 10
        writer.close()
        await writer.wait_closed()
    with pytest.raises(FileExistsError):
        await NativeTCPGateway(gateway.config, state_directory=tmp_path).start()
    assert json.loads((tmp_path / "snapshot.json").read_text())["status"] == "closed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wire_request",
    [
        b"GET / HTTP/1.1\r\n\r\n",
        b"CONNECT alternate:80 HTTP/1.1\r\n\r\n",
        b"CONNECT 127.0.0.1:80 HTTP/1.1\r\n\r\n",
        b"CONNECT approved.test:80 HTTP/1.1\r\nX: secret\r\n\r\n",
    ],
)
async def test_reject_without_dial(tmp_path, wire_request):
    async with fixture(tmp_path) as (gateway, port):
        reader, writer = await connect(gateway, port, wire_request)
        assert await reader.read() == b""
        assert gateway.snapshot()["attempts"] == 0
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_failed_attempt_counts(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    gateway = NativeTCPGateway(config(port, maxConnections=1), state_directory=tmp_path)
    await gateway.start()
    try:
        for _ in range(2):
            reader, writer = await connect(gateway, port)
            assert await reader.read() == b""
            writer.close()
            await writer.wait_closed()
        assert gateway.snapshot()["attempts"] == 1
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_byte_budget_both_directions_and_deadline(tmp_path):
    async with fixture(tmp_path, maxBytes=5, deadlineUnix=time.time() + 0.3) as (
        gateway,
        port,
    ):
        reader, writer = await connect(gateway, port)
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"hello")
        await writer.drain()
        assert await reader.read() == b""
        assert gateway.snapshot()["bytesReserved"] == 5
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.35)
        assert gateway.snapshot()["status"] == "closed"


@pytest.mark.asyncio
async def test_fragmented_header_and_cancel(tmp_path):
    async with fixture(tmp_path) as (gateway, port):
        reader, writer = await asyncio.open_connection("127.0.0.1", gateway.bound_port)
        for part in [
            b"CON",
            f"NECT approved.test:{port} HTTP/1.1\r\n".encode(),
            b"\r\n",
        ]:
            writer.write(part)
            await writer.drain()
            await asyncio.sleep(0)
        await reader.readuntil(b"\r\n\r\n")
        await gateway.close()
        assert await reader.read() == b""
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_evidence_failure_prevents_bind(tmp_path, monkeypatch):
    gateway = NativeTCPGateway(config(1234), state_directory=tmp_path)
    monkeypatch.setattr(
        gateway, "_persist", lambda: (_ for _ in ()).throw(OSError("unavailable"))
    )
    with pytest.raises(OSError):
        await gateway.start()
    assert gateway.server is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit,category",
    [
        ({"maxConcurrentConnections": 1}, "concurrency"),
        ({"maxConnectionsPerSecond": 1}, "rate"),
    ],
)
async def test_concurrency_and_sliding_start_rate(tmp_path, limit, category):
    async with fixture(tmp_path, **limit) as (gateway, port):
        first, w1 = await connect(gateway, port)
        await first.readuntil(b"\r\n\r\n")
        second, w2 = await connect(gateway, port)
        try:
            assert await second.read() == b""
        except ConnectionResetError:
            pass
        assert gateway.snapshot()["attempts"] == 1
        assert gateway.snapshot()["denials"][category] == 1
        w1.close()
        w2.close()


@pytest.mark.asyncio
async def test_pinned_client_and_oversized_header(tmp_path):
    async with fixture(tmp_path, clientIp="127.0.0.2") as (gateway, port):
        reader, writer = await connect(gateway, port)
        try:
            assert await reader.read() == b""
        except ConnectionResetError:
            pass
        assert gateway.snapshot()["attempts"] == 0
        assert gateway.snapshot()["denials"]["client"] == 1
        writer.close()


@pytest.mark.asyncio
async def test_oversized_and_slow_headers(tmp_path):
    async with fixture(tmp_path, handshakeSeconds=0.05) as (gateway, port):
        for wire_request in [b"CONNECT " + b"a" * 5000 + b"\r\n\r\n", b"CON"]:
            reader, writer = await connect(gateway, port, wire_request)
            assert await asyncio.wait_for(reader.read(), 1) == b""
            writer.close()
        assert gateway.snapshot()["attempts"] == 0
        assert gateway.snapshot()["denials"]["header"] == 1
        assert gateway.snapshot()["denials"]["timeout"] == 1


@pytest.mark.asyncio
async def test_runtime_evidence_failure_closes_all_streams(tmp_path, monkeypatch):
    async with fixture(tmp_path) as (gateway, port):
        reader, writer = await connect(gateway, port)
        await reader.readuntil(b"\r\n\r\n")
        original = gateway._persist
        monkeypatch.setattr(
            gateway, "_persist", lambda: (_ for _ in ()).throw(OSError("unavailable"))
        )
        writer.write(b"not-forwarded")
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 1) == b""
        assert gateway.snapshot()["status"] == "closed"
        assert not gateway.server.is_serving()
        monkeypatch.setattr(gateway, "_persist", original)
        writer.close()


@pytest.mark.asyncio
async def test_deadline_closes_active_stream_and_listener(tmp_path):
    async with fixture(tmp_path, deadlineUnix=time.time() + 0.2) as (gateway, port):
        reader, writer = await connect(gateway, port)
        await reader.readuntil(b"\r\n\r\n")
        assert await asyncio.wait_for(reader.read(), 1) == b""
        assert not gateway.server.is_serving()
        writer.close()


@pytest.mark.asyncio
async def test_downstream_byte_budget_without_client_data(tmp_path):
    async def banner(reader, writer):
        writer.write(b"123456")
        await writer.drain()
        await reader.read()
        writer.close()

    server = await asyncio.start_server(banner, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    gateway = NativeTCPGateway(config(port, maxBytes=5), state_directory=tmp_path)
    await gateway.start()
    try:
        reader, writer = await connect(gateway, port)
        await reader.readuntil(b"\r\n\r\n")
        assert await reader.read() == b""
        assert gateway.snapshot()["bytesReserved"] == 0
        assert gateway.snapshot()["denials"]["bytes"] == 1
        writer.close()
    finally:
        await gateway.close()
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize(
    "changes",
    [
        {"endpoints": [{"authority": "host:80", "address": "localhost", "port": 80}]},
        {"clientIp": "localhost"},
        {"maxBytes": True},
        {"deadlineUnix": float("nan")},
    ],
)
def test_invalid_config(changes):
    with pytest.raises((ValueError, TypeError)):
        config(1234, **changes)


@pytest.mark.asyncio
async def test_readonly_readiness_identity_and_closed_state(tmp_path):
    from moonmind.security.native_tcp_gateway import check

    async with fixture(tmp_path) as (gateway, port):
        assert check(gateway.config, tmp_path)["status"] == "running"
        other = config(port)
        with pytest.raises(ValueError):
            check(other, tmp_path)
    with pytest.raises(ValueError):
        check(gateway.config, tmp_path)


@pytest.mark.asyncio
async def test_no_dns_fallback_and_half_close_response(tmp_path, monkeypatch):
    async def answer_after_eof(reader, writer):
        data = await reader.read()
        writer.write(data.upper())
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(answer_after_eof, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    gateway = NativeTCPGateway(config(port), state_directory=tmp_path)
    await gateway.start()

    async def forbidden(*args, **kwargs):
        raise AssertionError("runtime DNS forbidden")

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", forbidden)
    try:
        reader, writer = await connect(gateway, port)
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"hello")
        await writer.drain()
        writer.write_eof()
        assert await reader.read() == b"HELLO"
        writer.close()
    finally:
        await gateway.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_process_crash_cannot_reconstruct_budget(tmp_path):
    import sys
    from pathlib import Path

    import moonmind.security.native_tcp_gateway as module

    state = tmp_path / "state"
    state.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config(1234).to_mapping()))
    command = [
        sys.executable,
        str(Path(module.__file__)),
        "--config",
        str(config_path),
        "--state-directory",
        str(state),
    ]
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        for _ in range(100):
            try:
                if (
                    json.loads((state / "snapshot.json").read_text())["status"]
                    == "running"
                ):
                    break
            except FileNotFoundError:
                pass
            await asyncio.sleep(0.01)
        else:
            pytest.fail("gateway did not initialize")
        process.kill()
        await process.wait()
        replacement = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, error = await replacement.communicate()
        assert replacement.returncode != 0
        assert b"FileExistsError" in error
        assert json.loads((state / "snapshot.json").read_text())["pid"] == process.pid
        with pytest.raises((ValueError, OSError)):
            module.check(
                NativeTCPConfig.from_mapping(json.loads(config_path.read_text())), state
            )
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def test_endpoint_product_bound_matches_authority():
    endpoints = [
        {"authority": f"approved.test:{port}", "address": "127.0.0.1", "port": port}
        for port in range(1, 1025)
    ]
    assert len(config(1, endpoints=endpoints).endpoints) == 1024
    endpoints.append(
        {"authority": "approved.test:1025", "address": "127.0.0.1", "port": 1025}
    )
    with pytest.raises(ValueError):
        config(1, endpoints=endpoints)


@pytest.mark.asyncio
async def test_installed_openssl_proxy_starttls_smtp(tmp_path):
    import shutil

    if not shutil.which("openssl"):
        pytest.skip("OpenSSL binary unavailable")
    received = asyncio.Future()

    async def smtp(reader, writer):
        try:
            # OpenSSL's proxy parser can discard an immediate greeting coalesced
            # with CONNECT 200. This fixture tests its wire syntax, not that
            # upstream client buffering limitation (separate qualification).
            await asyncio.sleep(0.05)
            writer.write(b"220 local.test ESMTP\r\n")
            await writer.drain()
            line = await reader.readline()
            if not received.done():
                received.set_result(line)
            writer.write(b"250 local.test\r\n")
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(smtp, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    gateway = NativeTCPGateway(config(port, maxBytes=4096), state_directory=tmp_path)
    await gateway.start()
    process = await asyncio.create_subprocess_exec(
        "openssl",
        "s_client",
        "-proxy",
        f"127.0.0.1:{gateway.bound_port}",
        "-connect",
        f"approved.test:{port}",
        "-starttls",
        "smtp",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert (await asyncio.wait_for(received, 2)).startswith(b"EHLO ")
        assert gateway.snapshot()["attempts"] == 1
    finally:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        await gateway.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_readiness_waits_for_listener_start(tmp_path, monkeypatch):
    """A persisted live PID alone must not attest an unstarted listener."""
    from moonmind.security.native_tcp_gateway import check

    entered, release = asyncio.Event(), asyncio.Event()
    original_start_server = asyncio.start_server

    async def delayed_server(*args, **kwargs):
        server = await original_start_server(*args, **kwargs)
        start_serving = server.start_serving

        async def delayed_start_serving():
            entered.set()
            await release.wait()
            await start_serving()

        server.start_serving = delayed_start_serving
        return server

    monkeypatch.setattr(asyncio, "start_server", delayed_server)
    gateway = NativeTCPGateway(config(80), state_directory=tmp_path)
    starting = asyncio.create_task(gateway.start())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(ValueError, match="not live"):
            check(gateway.config, tmp_path)
        release.set()
        await asyncio.wait_for(starting, 2)
        assert check(gateway.config, tmp_path)["status"] == "running"
    finally:
        release.set()
        await starting
        await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("shutdown", ["cancel", "deadline", "evidence-failure"])
async def test_enforcement_aborts_backpressured_transport(
    tmp_path, shutdown, monkeypatch
):
    """A peer that stops reading cannot keep queued bytes alive after revoke."""
    accepted = asyncio.Event()
    release = asyncio.Event()

    async def blocked_peer(reader, writer):
        writer.get_extra_info("socket").setsockopt(
            socket.SOL_SOCKET, socket.SO_RCVBUF, 4096
        )
        writer.transport.pause_reading()
        accepted.set()
        try:
            await release.wait()
        finally:
            writer.transport.abort()

    server = await asyncio.start_server(blocked_peer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    gateway = NativeTCPGateway(
        config(
            port,
            maxBytes=4 * 1024 * 1024,
            idleSeconds=10,
            deadlineUnix=time.time() + (1 if shutdown == "deadline" else 30),
        ),
        state_directory=tmp_path,
    )
    await gateway.start()
    reader, writer = await connect(gateway, port)
    try:
        await reader.readuntil(b"\r\n\r\n")
        await accepted.wait()
        upstream = next(
            w for w in gateway._writers if w.get_extra_info("peername")[1] == port
        )
        upstream.get_extra_info("socket").setsockopt(
            socket.SOL_SOCKET, socket.SO_SNDBUF, 4096
        )
        upstream.transport.set_write_buffer_limits(high=1024, low=0)
        writer.write(b"x" * (2 * 1024 * 1024))
        for _ in range(200):
            if upstream.transport.get_write_buffer_size() > 0:
                break
            await asyncio.sleep(0.005)
        assert upstream.transport.get_write_buffer_size() > 0, (
            "fixture must create real pending upstream bytes"
        )
        if shutdown == "deadline":
            for _ in range(300):
                if gateway._closed:
                    break
                await asyncio.sleep(0.005)
            assert gateway._closed
        elif shutdown == "evidence-failure":
            with monkeypatch.context() as patch:

                def fail_persist():
                    raise OSError("controlled evidence write failure")

                patch.setattr(gateway, "_persist", fail_persist)
                with pytest.raises(OSError, match="controlled evidence"):
                    gateway._record()
        await asyncio.wait_for(gateway.close(), 0.5)
        assert upstream.transport.get_write_buffer_size() == 0
        assert gateway.snapshot()["status"] == "closed"
        assert not gateway._tasks
    finally:
        writer.transport.abort()
        release.set()
        await gateway.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("session_limit", ["idle", "bytes"])
@pytest.mark.parametrize("shutdown", ["cancel", "deadline"])
async def test_session_limit_cannot_orphan_buffered_transport(
    tmp_path, session_limit, shutdown
):
    """Session teardown must not remove still-flushing sockets from revocation."""
    accepted, flood, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def blocked_peer(reader, writer):
        writer.get_extra_info("socket").setsockopt(
            socket.SOL_SOCKET, socket.SO_RCVBUF, 4096
        )
        writer.transport.pause_reading()
        accepted.set()
        try:
            await flood.wait()
            if session_limit == "bytes":
                # The reverse direction exhausts the shared budget while the
                # forward direction is demonstrably backpressured.
                writer.write(b"y" * (256 * 1024))
            await release.wait()
        finally:
            writer.transport.abort()

    server = await asyncio.start_server(blocked_peer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    gateway = NativeTCPGateway(
        config(
            port,
            maxBytes=256 * 1024,
            idleSeconds=0.15 if session_limit == "idle" else 5,
            deadlineUnix=time.time() + (1 if shutdown == "deadline" else 30),
        ),
        state_directory=tmp_path,
    )
    await gateway.start()
    reader, writer = await connect(gateway, port)
    try:
        await reader.readuntil(b"\r\n\r\n")
        await accepted.wait()
        upstream = next(
            w for w in gateway._writers if w.get_extra_info("peername")[1] == port
        )
        upstream.get_extra_info("socket").setsockopt(
            socket.SOL_SOCKET, socket.SO_SNDBUF, 4096
        )
        upstream.transport.set_write_buffer_limits(high=1024, low=0)
        writer.write(b"x" * (2 * 1024 * 1024))
        for _ in range(200):
            if upstream.transport.get_write_buffer_size() > 0:
                break
            await asyncio.sleep(0.005)
        assert upstream.transport.get_write_buffer_size() > 0
        flood.set()
        for _ in range(150):
            if not gateway._tasks:
                break
            await asyncio.sleep(0.005)
        assert gateway.snapshot()["denials"].get(session_limit) == 1
        assert not gateway._tasks, (
            "per-session teardown must have completed before revocation"
        )
        if shutdown == "deadline":
            for _ in range(300):
                if gateway._closed:
                    break
                await asyncio.sleep(0.005)
            assert gateway._closed
        await asyncio.wait_for(gateway.close(), 0.5)
        assert upstream.transport.get_write_buffer_size() == 0
    finally:
        writer.transport.abort()
        upstream.transport.abort()
        flood.set()
        release.set()
        await gateway.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["peer-resumes", "deadline", "cancel"])
async def test_successful_eof_flush_remains_owned_until_closed(tmp_path, ending):
    """Normal EOF preserves data, but a stalled flush never sheds its lease."""
    accepted, resume, received = asyncio.Event(), asyncio.Event(), asyncio.Event()
    contents = bytearray()

    async def half_closed_peer(reader, writer):
        writer.get_extra_info("socket").setsockopt(
            socket.SOL_SOCKET, socket.SO_RCVBUF, 4096
        )
        writer.transport.pause_reading()
        writer.write_eof()
        accepted.set()
        try:
            await resume.wait()
            writer.transport.resume_reading()
            while chunk := await reader.read(65536):
                contents.extend(chunk)
            received.set()
        finally:
            writer.transport.abort()

    server = await asyncio.start_server(half_closed_peer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    gateway = NativeTCPGateway(
        config(
            port,
            maxBytes=1024 * 1024,
            deadlineUnix=time.time() + (2 if ending == "deadline" else 30),
        ),
        state_directory=tmp_path,
    )
    await gateway.start()
    reader, writer = await connect(gateway, port)
    upstream = None
    try:
        await reader.readuntil(b"\r\n\r\n")
        await accepted.wait()
        upstream = next(
            w for w in gateway._writers if w.get_extra_info("peername")[1] == port
        )
        upstream.get_extra_info("socket").setsockopt(
            socket.SOL_SOCKET, socket.SO_SNDBUF, 4096
        )
        # drain() permits a bounded buffer below high water. Both normal EOFs
        # may therefore finish before the final data reaches the peer.
        upstream.transport.set_write_buffer_limits(high=1024 * 1024, low=512 * 1024)
        data = b"x" * (128 * 1024)
        writer.write(data)
        writer.write_eof()
        for _ in range(200):
            if upstream.transport.is_closing():
                break
            await asyncio.sleep(0.005)
        assert upstream.transport.is_closing()
        assert upstream.transport.get_write_buffer_size() > 0
        assert gateway._tasks and upstream in gateway._writers
        if ending == "peer-resumes":
            resume.set()
            await asyncio.wait_for(received.wait(), 5)
            assert contents == data
        elif ending == "deadline":
            for _ in range(500):
                if gateway._closed:
                    break
                await asyncio.sleep(0.005)
            assert gateway._closed
        await asyncio.wait_for(gateway.close(), 0.5)
        assert upstream.transport.get_write_buffer_size() == 0
        assert not gateway._tasks
    finally:
        writer.transport.abort()
        if upstream:
            upstream.transport.abort()
        resume.set()
        await gateway.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary",
    [
        "new-connection",
        "handshake",
        "dial-reservation",
        "client-forward",
        "target-forward",
        "forward-reservation",
        "idle",
        "rollback",
    ],
)
async def test_absolute_deadline_survives_clock_changes(
    tmp_path, monkeypatch, boundary
):
    from types import SimpleNamespace

    from moonmind.security import native_tcp_gateway as runtime

    wall = [time.time()]
    monkeypatch.setattr(runtime, "time", SimpleNamespace(time=lambda: wall[0]))
    target_connections = []
    target_received = bytearray()
    send_target = asyncio.Event()

    async def target(reader, writer):
        target_connections.append(writer)
        try:
            if boundary == "target-forward":
                await send_target.wait()
                writer.write(b"forbidden")
                await writer.drain()
            while data := await reader.read(4096):
                target_received.extend(data)
        except OSError:
            pass
        finally:
            writer.transport.abort()

    server = await asyncio.start_server(target, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    gateway = NativeTCPGateway(
        config(port, deadlineUnix=wall[0] + (0.25 if boundary == "rollback" else 30)),
        state_directory=tmp_path,
    )
    await gateway.start()
    proxy_port = gateway.bound_port
    writer = None
    try:
        if boundary == "new-connection":
            wall[0] += 60
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        except (ConnectionRefusedError, ConnectionResetError):
            assert boundary == "new-connection"
            assert gateway._closed
            assert not target_connections
            return
        if boundary == "handshake":
            wall[0] += 60
        if boundary in {"dial-reservation", "forward-reservation"}:
            persist = gateway._persist

            def advance_during_reservation():
                persist()
                if gateway.snapshot()[
                    "attempts" if boundary == "dial-reservation" else "bytesReserved"
                ]:
                    wall[0] += 60

            monkeypatch.setattr(gateway, "_persist", advance_during_reservation)
        writer.write(
            f"CONNECT approved.test:{port} HTTP/1.1\r\nHost: approved.test:{port}\r\n\r\n".encode()
        )
        await writer.drain()
        if boundary not in {"new-connection", "handshake", "dial-reservation"}:
            assert (
                await reader.readuntil(b"\r\n\r\n")
                == b"HTTP/1.1 200 Connection Established\r\n\r\n"
            )
            if boundary != "forward-reservation":
                wall[0] += -3600 if boundary == "rollback" else 60
            if boundary in {"client-forward", "forward-reservation"}:
                writer.write(b"forbidden")
                await writer.drain()
            elif boundary == "target-forward":
                send_target.set()
        try:
            received = await asyncio.wait_for(reader.read(), 0.7)
        except ConnectionResetError:
            received = b""
        assert received == b""
        assert target_received == b""
        if boundary in {"new-connection", "handshake", "dial-reservation"}:
            assert not target_connections
        assert gateway._closed
    finally:
        if writer:
            writer.transport.abort()
        send_target.set()
        await gateway.close()
        for target_writer in target_connections:
            target_writer.transport.abort()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_value", ["", "curl/7.88.1", "Bearer secret"])
async def test_bookworm_curl_empty_agent_is_exactly_bounded(tmp_path, agent_value):
    """Bookworm emits an empty User-Agent even with --user-agent ''."""
    async with fixture(tmp_path) as (gateway, port):
        authority = f"approved.test:{port}"
        wire = (
            f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n"
            f"User-Agent: {agent_value}\r\nProxy-Connection: Keep-Alive\r\n\r\n"
        ).encode()
        reader, writer = await connect(gateway, port, wire)
        try:
            if agent_value:
                assert await reader.read() == b""
                assert gateway.snapshot()["attempts"] == 0
            else:
                assert (
                    await reader.readuntil(b"\r\n\r\n")
                    == b"HTTP/1.1 200 Connection Established\r\n\r\n"
                )
                writer.write(b"hello")
                await writer.drain()
                assert await reader.readexactly(5) == b"hello"
                assert gateway.snapshot()["attempts"] == 1
        finally:
            writer.close()
            await writer.wait_closed()
