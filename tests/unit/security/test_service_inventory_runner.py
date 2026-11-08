"""Actual loopback fixtures; not a claim of Docker isolation or real routing."""

import asyncio
import json
import socket
import time
from contextlib import asynccontextmanager

import pytest

from moonmind.security.native_network import NativeNetworkSelection
from moonmind.security.native_tcp_gateway import (
    CONNECT_FAILURES,
    NativeTCPConfig,
    NativeTCPGateway,
)
from moonmind.security.service_inventory import (
    ServiceInventoryOptions,
    build_service_inventory_plan,
    validate_service_inventory_execution,
)
from moonmind.security.service_inventory_runner import inspect_service_inventory


def plan_for(
    ports,
    *,
    banners=(),
    heads=(),
    max_bytes=65536,
    connections=10,
    rate=100,
    concurrency=2,
    capture=1024,
):
    native = NativeNetworkSelection.model_validate(
        {
            "targets": [
                {"hosts": ["approved.test"], "ports": ports, "purpose": "live-target"}
            ],
            "limits": {
                "maxConnections": connections,
                "maxBytes": max_bytes,
                "maxConnectionsPerSecond": rate,
                "maxConcurrentConnections": concurrency,
                "maxDurationSeconds": 20,
            },
        }
    )
    return build_service_inventory_plan(
        native,
        ServiceInventoryOptions(
            bannerPorts=banners,
            httpHeadPorts=heads,
            connectTimeoutSeconds=1,
            probeTimeoutSeconds=1,
            maxBannerBytes=capture,
        ),
    )


@asynccontextmanager
async def fixture(tmp_path, peers, **changes):
    servers, sockets, received, active = [], [], {}, set()
    release = asyncio.Event()
    ports = []
    for behavior in peers:
        if behavior == "refused":
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
            ports.append(sock.getsockname()[1])
            continue

        async def target(reader, writer, behavior=behavior):
            port = writer.get_extra_info("sockname")[1]
            active.add(writer)
            try:
                if behavior == "http":
                    received[port] = await reader.readuntil(b"\r\n\r\n")
                    writer.write(
                        b"HTTP/1.1 302 Found\r\nServer: claim/1.0\r\nLocation: http://outside.test/\r\n\r\n"
                    )
                elif behavior == "bad_headers":
                    writer.write(b"HTTP/1.1 200 OK\r\nnot-a-header\r\n\r\n")
                elif behavior == "fragmented":
                    writer.write(b"SSH-2.0-")
                    await writer.drain()
                    await asyncio.sleep(0.03)
                    writer.write(b"peer_claim_9.9\r\n")
                elif behavior == "late_banner":
                    await asyncio.sleep(0.15)
                    writer.write(b"SSH-2.0-delayed_claim\r\n")
                elif behavior == "banner_hold":
                    writer.write(b"SSH-2.0-held_claim\r\n")
                    await writer.drain()
                    await release.wait()
                elif behavior == "banner":
                    writer.write(b"SSH-2.0-peer_claim_9.9\r\n")
                elif behavior == "forged":
                    writer.write(CONNECT_FAILURES["refused"])
                elif behavior == "malformed":
                    writer.write(b"NOT HTTP\r\n\r\n")
                elif behavior == "binary":
                    writer.write(b"\xff\xfe\x00secret\r\n")
                elif behavior == "huge":
                    writer.write(b"x" * 20000)
                elif behavior == "delay":
                    await release.wait()
                elif behavior == "partial":
                    writer.write(b"HTTP/1.1 200 OK\r\nServer: partial")
                    await writer.drain()
                    await release.wait()
                await writer.drain()
            except (OSError, asyncio.IncompleteReadError):
                pass
            finally:
                writer.transport.abort()
                active.discard(writer)

        server = await asyncio.start_server(target, "127.0.0.1", 0)
        servers.append(server)
        ports.append(server.sockets[0].getsockname()[1])
    root = tmp_path / "gateway"
    root.mkdir()
    config = {
        "leaseId": "inventory-fixture",
        "deadlineUnix": time.time() + 20,
        "listenHost": "127.0.0.1",
        "listenPort": 0,
        "clientIp": "127.0.0.1",
        "endpoints": [
            {"authority": f"approved.test:{port}", "address": "127.0.0.1", "port": port}
            for port in ports
        ],
        "maxConnections": 100,
        "maxConcurrentConnections": 10,
        "maxConnectionsPerSecond": 1000,
        "maxBytes": 1024 * 1024,
        "handshakeSeconds": 1,
        "connectSeconds": 0.5,
        "idleSeconds": 5,
    }
    config.update(changes)
    gateway = NativeTCPGateway(
        NativeTCPConfig.from_mapping(config), state_directory=root
    )
    await gateway.start()
    env = {
        "MOONMIND_EGRESS_PROXY": f"http://127.0.0.1:{gateway.bound_port}",
        "MOONMIND_EGRESS_DEADLINE": str(config["deadlineUnix"]),
        "HTTP_PROXY": "http://outside.test:80",
        "NO_PROXY": "*",
    }
    try:
        yield gateway, ports, env, received
    finally:
        release.set()
        for writer in tuple(active):
            writer.transport.abort()
        await gateway.close()
        for server in servers:
            server.close()
            await server.wait_closed()
        for sock in sockets:
            sock.close()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_open_refused_fixed_head_and_unverified_banner(tmp_path, monkeypatch):
    async with fixture(tmp_path, ["banner", "http", "refused"]) as (
        gateway,
        ports,
        env,
        received,
    ):
        plan = plan_for(ports, banners=[ports[0]], heads=[ports[1]])
        original = asyncio.open_connection
        dialed = []

        async def capture(host, port, **kwargs):
            dialed.append((host, port))
            return await original(host, port, **kwargs)

        monkeypatch.setattr(asyncio, "open_connection", capture)
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        validate_service_inventory_execution(plan, result)
        records = {record["authority"]: record for record in result["records"]}
        assert result["stopReason"] == "complete"
        assert (
            records[f"approved.test:{ports[0]}"]["observation"]["confidence"]
            == "unverified"
        )
        assert records[f"approved.test:{ports[1]}"]["httpStatus"] == 302
        assert records[f"approved.test:{ports[2]}"]["reachability"] == "refused"
        assert received[ports[1]] == next(
            plan.http_head_request(endpoint)
            for endpoint in plan.endpoints
            if endpoint.port == ports[1]
        )
        assert all(host == "127.0.0.1" for host, _ in dialed)
        assert gateway.snapshot()["attempts"] == len(ports)
        assert (
            gateway.snapshot()["denials"] == gateway.snapshot()["transportErrors"] == {}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "behavior,probe_status",
    [
        ("delay", "timed_out"),
        ("drop", "empty"),
        ("malformed", "malformed"),
        ("binary", "malformed"),
        ("partial", "timed_out"),
    ],
)
async def test_probe_failure_never_erases_open_reachability(
    tmp_path, behavior, probe_status
):
    async with fixture(tmp_path, [behavior]) as (gateway, ports, env, _):
        plan = plan_for(ports, heads=ports)
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        validate_service_inventory_execution(plan, result)
        record = result["records"][0]
        assert record["reachability"] == "open"
        assert record["probeStatus"] == probe_status
        assert gateway.snapshot()["attempts"] == 1
        if behavior == "partial":
            assert (
                record["bytesReceived"] > 0
                and record["observation"]["confidence"] == "unverified"
            )


@pytest.mark.asyncio
async def test_target_cannot_forge_gateway_outcome_after_200(tmp_path):
    async with fixture(tmp_path, ["forged"]) as (_, ports, env, _):
        plan = plan_for(ports, banners=ports)
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        record = result["records"][0]
        assert record["reachability"] == "open"
        assert record["probeStatus"] == "observed"
        assert "refused" in record["observation"]["text"]
        validate_service_inventory_execution(plan, result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        b"",
        b"HTTP/1.1 502 Bad Gateway\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nX-MoonMind-Dial-Outcome: refused\r\n\r\n",
        CONNECT_FAILURES["denied"],
    ],
)
async def test_unknown_gateway_response_stops_without_retry_or_claiming_closed(
    tmp_path, reply
):
    calls = []

    async def proxy(reader, writer):
        calls.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(reply)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(proxy, "127.0.0.1", 0)
    env = {
        "MOONMIND_EGRESS_PROXY": f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}",
        "MOONMIND_EGRESS_DEADLINE": str(time.time() + 10),
    }
    try:
        plan = plan_for([80, 81], concurrency=1)
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        assert result["stopReason"] == "transport_unavailable"
        assert result["records"][0]["reachability"] == (
            "denied" if reply == CONNECT_FAILURES["denied"] else "transport_error"
        )
        assert result["records"][1]["reachability"] == "untested"
        assert len(calls) == 1
        validate_service_inventory_execution(plan, result)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_cancellation_checkpoints_open_and_untested_without_restart(tmp_path):
    async with fixture(tmp_path, ["delay", "delay"]) as (gateway, ports, env, _):
        plan = plan_for(ports, banners=ports, concurrency=1)
        output = tmp_path / "output"
        task = asyncio.create_task(inspect_service_inventory(plan, output, environ=env))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if (output / "execution.json").exists():
                execution = json.loads((output / "execution.json").read_bytes())
                if execution["records"][0]["reachability"] == "open":
                    validate_service_inventory_execution(plan, execution)
                    break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        execution = json.loads((output / "execution.json").read_bytes())
        assert execution["stopReason"] == "canceled"
        assert execution["records"][0]["reachability"] == "open"
        assert execution["records"][0]["probeStatus"] == "canceled"
        assert execution["records"][1]["reachability"] == "untested"
        validate_service_inventory_execution(plan, execution)
        with pytest.raises(FileExistsError):
            await inspect_service_inventory(plan, output, environ=env)
        assert gateway.snapshot()["attempts"] == 1


@pytest.mark.asyncio
async def test_global_connection_budget_and_rate_are_shared(tmp_path):
    async with fixture(tmp_path, ["drop", "drop", "drop"]) as (gateway, ports, env, _):
        plan = plan_for(ports, connections=2, rate=1)
        start = time.monotonic()
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        assert time.monotonic() - start >= 1
        assert result["stopReason"] == "connection_budget_exhausted"
        assert (
            sum(record["reachability"] == "open" for record in result["records"]) == 2
        )
        assert gateway.snapshot()["attempts"] == 2
        validate_service_inventory_execution(plan, result)


@pytest.mark.asyncio
async def test_byte_budget_prevents_partial_head_or_payload(tmp_path):
    async with fixture(tmp_path, ["http"]) as (gateway, ports, env, received):
        plan = plan_for(ports, heads=ports, max_bytes=1)
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        assert result["records"][0]["reachability"] == "open"
        assert result["records"][0]["probeStatus"] == "budget_exhausted"
        assert result["stopReason"] == "byte_budget_exhausted"
        assert result["records"][0]["bytesSent"] == 0
        assert gateway.snapshot()["bytesReserved"] == 0
        assert not received
        validate_service_inventory_execution(plan, result)


@pytest.mark.asyncio
async def test_capture_limit_is_applied_before_decode(tmp_path):
    async with fixture(tmp_path, ["huge"]) as (_, ports, env, _):
        plan = plan_for(ports, banners=ports, capture=32)
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        record = result["records"][0]
        assert record["bytesReceived"] == 32
        assert record["truncated"] is True
        assert len(record["observation"]["text"].encode()) == 32
        validate_service_inventory_execution(plan, result)


@pytest.mark.asyncio
async def test_original_deadline_preserves_partial_progress(tmp_path):
    async with fixture(tmp_path, ["delay", "delay"]) as (gateway, ports, env, _):
        env["MOONMIND_EGRESS_DEADLINE"] = str(time.time() + 0.15)
        plan = plan_for(ports, banners=ports, concurrency=1)
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        assert result["stopReason"] == "duration_exhausted"
        assert result["records"][0]["reachability"] == "open"
        assert result["records"][1]["reachability"] == "untested"
        assert gateway.snapshot()["attempts"] == 1
        validate_service_inventory_execution(plan, result)


@pytest.mark.asyncio
async def test_shared_rate_and_concurrency_do_not_exceed_gateway(tmp_path):
    async with fixture(
        tmp_path,
        ["drop", "drop", "drop"],
        maxConcurrentConnections=2,
        maxConnectionsPerSecond=2,
    ) as (gateway, ports, env, _):
        plan = plan_for(ports, rate=2, concurrency=2)
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        assert result["stopReason"] == "complete"
        assert all(record["reachability"] == "open" for record in result["records"])
        assert gateway.snapshot()["denials"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [-3600, 3600])
async def test_runner_absolute_deadline_survives_clock_changes(
    tmp_path, monkeypatch, delta
):
    from types import SimpleNamespace

    from moonmind.security import service_inventory_runner as runner

    async with fixture(tmp_path, ["delay", "delay"]) as (_, ports, env, _):
        wall = [time.time()]
        env["MOONMIND_EGRESS_DEADLINE"] = str(wall[0] + 0.2)
        monkeypatch.setattr(
            runner,
            "time",
            SimpleNamespace(time=lambda: wall[0], monotonic=time.monotonic),
        )
        output = tmp_path / "output"
        plan = plan_for(ports, banners=ports, concurrency=1)
        task = asyncio.create_task(inspect_service_inventory(plan, output, environ=env))
        for _ in range(100):
            await asyncio.sleep(0.005)
            if (output / "execution.json").exists() and json.loads(
                (output / "execution.json").read_bytes()
            )["records"][0]["reachability"] == "open":
                break
        wall[0] += delta
        result = await asyncio.wait_for(task, 0.5)
        assert result["stopReason"] == "duration_exhausted"
        assert result["records"][0]["reachability"] == "open"
        assert result["records"][1]["reachability"] == "untested"
        validate_service_inventory_execution(plan, result)


@pytest.mark.asyncio
async def test_late_checkpoint_failure_preserves_completed_observation(
    tmp_path, monkeypatch
):
    from moonmind.security import service_inventory_runner as runner

    async with fixture(tmp_path, ["banner"]) as (_, ports, env, _):
        plan = plan_for(ports, banners=ports)
        output = tmp_path / "output"
        checkpoint = runner._checkpoint

        def fail_completed_once(path, value):
            if value["records"][0]["probeStatus"] == "observed":
                monkeypatch.setattr(runner, "_checkpoint", checkpoint)
                raise OSError("SECRET untrusted failure")
            checkpoint(path, value)

        monkeypatch.setattr(runner, "_checkpoint", fail_completed_once)
        with pytest.raises(RuntimeError, match="checkpoint"):
            await inspect_service_inventory(plan, output, environ=env)
        saved = json.loads((output / "execution.json").read_bytes())
        assert saved["stopReason"] == "runner_error"
        assert saved["records"][0]["reachability"] == "open"
        assert saved["records"][0]["probeStatus"] == "observed"
        assert "SECRET" not in json.dumps(saved)
        validate_service_inventory_execution(plan, saved)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proxy",
    [
        "",
        "https://approved.test:3128",
        "http://user:secret@approved.test:3128",
        "http://approved.test:3128/path",
        "http://approved.test:3128?x=1",
    ],
)
async def test_no_unconfigured_proxy_or_direct_fallback(tmp_path, monkeypatch, proxy):
    called = False

    async def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("no socket may be created")

    monkeypatch.setattr(asyncio, "open_connection", forbidden)
    with pytest.raises(ValueError):
        await inspect_service_inventory(
            plan_for([80]),
            tmp_path,
            environ={
                "MOONMIND_EGRESS_PROXY": proxy,
                "MOONMIND_EGRESS_DEADLINE": str(time.time() + 10),
            },
        )
    assert not called


def test_1024_endpoint_plan_crosses_real_subprocess_argv_without_app_settings():
    import subprocess
    import sys
    from pathlib import Path

    from moonmind.security.service_inventory import service_inventory_plan_arguments

    native = NativeNetworkSelection.model_validate(
        {
            "targets": [
                {
                    "hosts": [f"h{i:03}.example.test" for i in range(256)],
                    "ports": [80, 81, 82, 83],
                    "purpose": "live-target",
                }
            ],
            "limits": {
                "maxConnections": 1024,
                "maxBytes": 1024,
                "maxConnectionsPerSecond": 1000,
                "maxConcurrentConnections": 256,
                "maxDurationSeconds": 20,
            },
        }
    )
    plan = build_service_inventory_plan(native, ServiceInventoryOptions())
    args = service_inventory_plan_arguments(plan)
    assert len(args) > 2 and max(map(len, args)) <= 4096
    root = Path(__file__).resolve().parents[3]
    code = "import sys;sys.path.insert(0,sys.argv.pop(1));from moonmind.security.service_inventory import parse_service_inventory_plan_arguments;p=parse_service_inventory_plan_arguments(sys.argv[1:]);assert 'moonmind.config.settings' not in sys.modules;print(p.plan_key,len(p.endpoints))"
    process = subprocess.run(
        [sys.executable, "-I", "-c", code, str(root), *args],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == f"{plan.plan_key} 1024"


def test_oversized_checkpoint_cannot_replace_useful_evidence(tmp_path, monkeypatch):
    from moonmind.security import service_inventory_runner as runner

    path = tmp_path / "execution.json"
    runner._checkpoint(path, {"useful": "confirmed"})
    before = path.read_bytes()
    monkeypatch.setattr(runner, "MAX_SERVICE_INVENTORY_OUTPUT_BYTES", 64)
    with pytest.raises(ValueError, match="byte bound"):
        runner._checkpoint(path, {"peer": "\\" * 64})
    assert path.read_bytes() == before
    assert not path.with_suffix(".tmp").exists()


@pytest.mark.asyncio
async def test_fragmented_passive_banner_is_collected_until_first_line(tmp_path):
    async with fixture(tmp_path, ["fragmented"]) as (_, ports, env, _):
        plan = plan_for(ports, banners=ports)
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        record = result["records"][0]
        assert "SSH-2.0-peer_claim_9.9" in record["observation"]["text"]
        assert record["probeStatus"] == "observed"
        validate_service_inventory_execution(plan, result)


@pytest.mark.asyncio
async def test_http_status_alone_does_not_attest_malformed_headers(tmp_path):
    async with fixture(tmp_path, ["bad_headers"]) as (_, ports, env, _):
        plan = plan_for(ports, heads=ports)
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        record = result["records"][0]
        assert record["reachability"] == "open"
        assert record["probeStatus"] == "malformed"
        assert "httpStatus" not in record
        validate_service_inventory_execution(plan, result)


@pytest.mark.asyncio
async def test_storage_failure_after_connect_is_not_a_probe_failure(
    tmp_path, monkeypatch
):
    from moonmind.security import service_inventory_runner as runner

    async with fixture(tmp_path, ["drop"]) as (_, ports, env, _):
        plan = plan_for(ports)
        output = tmp_path / "output"
        checkpoint = runner._checkpoint

        def fail_open_once(path, value):
            if value["records"][0]["reachability"] == "open":
                monkeypatch.setattr(runner, "_checkpoint", checkpoint)
                raise OSError("storage unavailable")
            checkpoint(path, value)

        monkeypatch.setattr(runner, "_checkpoint", fail_open_once)
        with pytest.raises(RuntimeError, match="checkpoint"):
            await inspect_service_inventory(plan, output, environ=env)
        saved = json.loads((output / "execution.json").read_bytes())
        assert saved["stopReason"] == "runner_error"
        assert saved["records"][0]["reachability"] == "open"
        assert saved["records"][0]["probeStatus"] == "not_requested"
        validate_service_inventory_execution(plan, saved)


@pytest.mark.asyncio
@pytest.mark.parametrize("dial_seconds", [1, 5])
async def test_admitted_dial_timeout_arrives_before_client_envelope_expires(
    tmp_path, monkeypatch, dial_seconds
):
    async with fixture(tmp_path, ["drop", "drop"], connectSeconds=dial_seconds) as (
        gateway,
        ports,
        env,
        _,
    ):
        original = asyncio.open_connection
        delayed = min(ports)

        async def drop_selected_dial(host, port, **kwargs):
            if host == "127.0.0.1" and port == delayed:
                await asyncio.sleep(dial_seconds + 3)
            return await original(host, port, **kwargs)

        monkeypatch.setattr(asyncio, "open_connection", drop_selected_dial)
        base = plan_for(ports, concurrency=1)
        plan = build_service_inventory_plan(
            base.native_network,
            ServiceInventoryOptions(connectTimeoutSeconds=dial_seconds),
        )
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        assert result["stopReason"] == "complete"
        assert [record["reachability"] for record in result["records"]] == [
            "timed_out",
            "open",
        ]
        assert gateway.snapshot()["dialOutcomes"] == {"timed_out": 1, "open": 1}
        assert gateway.snapshot()["attempts"] == 2
        validate_service_inventory_execution(plan, result)


@pytest.mark.asyncio
async def test_pending_receive_reservation_does_not_exhaust_actual_byte_budget(
    tmp_path,
):
    async with fixture(tmp_path, ["delay", "delay"]) as (gateway, ports, env, _):
        plan = plan_for(
            ports, banners=ports, max_bytes=1024, capture=1024, concurrency=2
        )
        started = time.monotonic()
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        assert time.monotonic() - started >= 1
        assert result["stopReason"] == "complete"
        assert all(
            record["reachability"] == "open" and record["probeStatus"] == "timed_out"
            for record in result["records"]
        )
        assert (
            sum(
                record["bytesReceived"] + record["bytesSent"]
                for record in result["records"]
            )
            == 0
        )
        assert gateway.snapshot()["attempts"] == 2
        assert gateway.snapshot()["bytesReserved"] == 0
        validate_service_inventory_execution(plan, result)


@pytest.mark.asyncio
async def test_unused_receive_capacity_is_released_to_another_probe(tmp_path):
    async with fixture(tmp_path, ["late_banner", "late_banner"]) as (_, ports, env, _):
        plan = plan_for(
            ports, banners=ports, max_bytes=1024, capture=1024, concurrency=2
        )
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        assert result["stopReason"] == "complete"
        assert all(record["probeStatus"] == "observed" for record in result["records"])
        assert sum(record["bytesReceived"] for record in result["records"]) == 2 * len(
            b"SSH-2.0-delayed_claim\r\n"
        )
        validate_service_inventory_execution(plan, result)


@pytest.mark.asyncio
async def test_cancel_releases_pending_capacity_without_spending_or_successor(tmp_path):
    from moonmind.security.service_inventory_runner import _Inventory

    async with fixture(tmp_path, ["delay", "delay"]) as (gateway, ports, env, _):
        plan = plan_for(
            ports, banners=ports, max_bytes=1024, capture=1024, concurrency=2
        )
        runner = _Inventory(plan, tmp_path / "output", env)
        task = asyncio.create_task(runner.run())
        for _ in range(100):
            await asyncio.sleep(0.01)
            if all(
                record["reachability"] == "open"
                for record in runner.execution["records"]
            ):
                break
        assert runner.bytes_pending == 1024 and runner.bytes_spent == 0
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runner.bytes_pending == runner.bytes_spent == 0
        assert runner.execution["stopReason"] == "canceled"
        assert all(
            record["probeStatus"] == "canceled"
            for record in runner.execution["records"]
        )
        assert gateway.snapshot()["attempts"] == 2
        validate_service_inventory_execution(plan, runner.execution)


@pytest.mark.asyncio
async def test_response_grace_cannot_extend_original_lease(tmp_path, monkeypatch):
    async with fixture(tmp_path, ["drop", "drop"], connectSeconds=1) as (
        gateway,
        ports,
        env,
        _,
    ):
        original = asyncio.open_connection

        async def delayed_dial(host, port, **kwargs):
            if port in ports:
                await asyncio.sleep(10)
            return await original(host, port, **kwargs)

        monkeypatch.setattr(asyncio, "open_connection", delayed_dial)
        env["MOONMIND_EGRESS_DEADLINE"] = str(time.time() + 0.15)
        plan = plan_for(ports, concurrency=1)
        started = time.monotonic()
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        assert time.monotonic() - started < 0.5
        assert result["stopReason"] == "duration_exhausted"
        assert result["records"][1]["reachability"] == "untested"
        assert gateway.snapshot()["attempts"] == 1
        validate_service_inventory_execution(plan, result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "behavior,status", [("delay", "timed_out"), ("banner_hold", "observed")]
)
async def test_finished_probe_releases_single_gateway_slot_before_next_endpoint(
    tmp_path, behavior, status
):
    async with fixture(tmp_path, [behavior, behavior], maxConcurrentConnections=1) as (
        gateway,
        ports,
        env,
        _,
    ):
        plan = plan_for(ports, banners=ports, concurrency=1)
        result = await inspect_service_inventory(plan, tmp_path / "output", environ=env)
        assert result["stopReason"] == "complete"
        assert all(
            record["reachability"] == "open" and record["probeStatus"] == status
            for record in result["records"]
        )
        assert gateway.snapshot()["attempts"] == 2
        assert gateway.snapshot()["activeConnections"] == 0
        assert (
            gateway.snapshot()["denials"] == gateway.snapshot()["transportErrors"] == {}
        )
        validate_service_inventory_execution(plan, result)
