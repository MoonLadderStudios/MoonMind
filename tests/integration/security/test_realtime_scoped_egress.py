"""Real bounded transports traverse the compiled, deny-by-default Squid proxy."""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import pytest
from aiohttp import web
from pydantic import ValidationError

from moonmind.security.egress_policy import ScopedEgressPolicy
from moonmind.security.scoped_egress import render_scoped_proxy
from moonmind.security.target_assessments import ScopedTargetClient, TargetStopped
from tests.unit.security.test_realtime_scope_transport import (
    realtime_scope,
    request_for,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@contextmanager
def compiled_proxy(port):
    squid = shutil.which("squid")
    assert squid, "test-runtime must supply the proxy fixture"
    with tempfile.TemporaryDirectory(prefix="moonmind-test-realtime-proxy-") as root:
        directory = Path(root)
        identity = {}
        if os.geteuid() == 0:
            account = pwd.getpwnam("proxy")
            os.chown(directory, account.pw_uid, account.pw_gid)
            identity = {
                "user": account.pw_uid,
                "group": account.pw_gid,
                "extra_groups": [],
            }
        policy = ScopedEgressPolicy.model_validate(
            {
                "policyRef": "realtime-fixture",
                "allowedWorkflowIds": ["fixture"],
                "allowedImageSourceRefs": ["fixture"],
                "destinations": [
                    {
                        "host": "realtime.example.test",
                        "purpose": "live-target",
                        "protocol": "http",
                        "port": port,
                    }
                ],
            }
        )
        # Only DNS is substituted for loopback. Do not add upgrade permissions
        # or change ACLs here: exercise the exact production compiler defaults.
        config, hosts = render_scoped_proxy(
            policy,
            {"realtime.example.test": ("127.0.0.1",)},
            listen_address="127.0.0.5",
            client_address="127.0.0.1",
        )
        for name in ("hosts", "access.log", "squid.pid", "cache.log"):
            config = config.replace(
                f"/tmp/moonmind-{name}".encode(), str(directory / name).encode()
            )
        for name, content in (("hosts", hosts), ("squid.conf", config)):
            (directory / name).write_bytes(content)
            (directory / name).chmod(0o444)
        with (directory / "stderr").open("wb") as stderr:
            process = subprocess.Popen(
                [squid, "-NYC", "-d", "1", "-f", str(directory / "squid.conf")],
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                **identity,
            )
            failure = None
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(
                            f"fixture Squid exited {process.returncode} before listening"
                        )
                    try:
                        with socket.create_connection(("127.0.0.5", 3128), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.05)
                else:
                    pytest.fail("compiled realtime proxy did not listen")
                yield "http://127.0.0.5:3128"
            except BaseException as exc:
                failure = exc
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if failure is not None:
                    # Report fixed fields only, never query tokens, headers,
                    # response bodies, or arbitrary proxy exception strings.
                    failure.add_note(f"Fixture Squid exit status: {process.returncode}")
                    for name in ("stderr", "access.log"):
                        try:
                            with (directory / name).open("rb") as source:
                                source.seek(0, os.SEEK_END)
                                source.seek(max(0, source.tell() - 4000))
                                tail = source.read(4000).decode(errors="replace")
                        except OSError:
                            continue
                        if name == "stderr":
                            categories = [
                                label
                                for label in ("FATAL", "ERROR", "WARNING")
                                if label + ":" in tail
                            ]
                            failure.add_note(
                                f"Fixture Squid stderr categories: {categories}"
                            )
                            continue
                        for line in tail.splitlines()[-12:]:
                            fields = line.split()
                            if len(fields) < 7 or fields[5] not in {
                                "GET",
                                "POST",
                                "CONNECT",
                            }:
                                continue
                            code = fields[3].rsplit("/", 1)[-1]
                            path = urlsplit(fields[6]).path
                            if code.isdecimal() and path in {
                                "/ws",
                                "/negotiate",
                                "/elsewhere",
                            }:
                                failure.add_note(
                                    f"Fixture Squid HTTP {code} {fields[5]} {path}"
                                )
                    status = getattr(failure.__context__, "status", None)
                    if isinstance(status, int):
                        failure.add_note(f"Fixture handshake HTTP status: {status}")


@asynccontextmanager
async def realtime_target(protocol):
    state = {
        "paths": [],
        "messages": [],
        "redirect_port": None,
        "closed": asyncio.Event(),
    }

    async def handler(request):
        state["paths"].append(request.path)
        if state["redirect_port"] is not None:
            port = int(state["redirect_port"])
            assert 1 <= port <= 65535
            # The hostile redirect is confined to the fixture's other listener;
            # no request value or arbitrary URL can select its host or path.
            raise web.HTTPFound(f"http://127.0.0.1:{port}/elsewhere")
        if request.path == "/negotiate":
            return web.json_response(
                {
                    "negotiateVersion": 1,
                    "connectionToken": "fixture-token",
                    "availableTransports": [
                        {"transport": "WebSockets", "transferFormats": ["Text"]}
                    ],
                }
            )
        channel = web.WebSocketResponse(autoping=False)
        await channel.prepare(request)
        try:
            async for message in channel:
                value = json.loads(message.data.removesuffix("\x1e"))
                state["messages"].append(value)
                if value.get("protocol") == "json":
                    await channel.send_str("{}\x1e")
                elif protocol == "signalr-json":
                    await channel.send_str(
                        json.dumps(
                            {
                                "type": 3,
                                "invocationId": value["invocationId"],
                                "result": {"access": "allow"},
                            }
                        )
                        + "\x1e"
                    )
                else:
                    await channel.send_json({"access": "allow"})
        finally:
            state["closed"].set()
        return channel

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield site._server.sockets[0].getsockname()[1], state
    finally:
        await runner.cleanup()


def bounded_client(protocol, origin, proxy):
    # Include bounded Squid denial HTML as well as protocol fixture messages.
    scope = realtime_scope(protocol, maxConnections=1, maxMessageBytes=16384)
    scope.web.origin = origin
    return ScopedTargetClient(scope, proxy=proxy, max_seconds=5), request_for(scope)


@pytest.mark.parametrize("protocol", ["websocket", "signalr-json"])
def test_compiled_proxy_allows_bounded_realtime_exchange(protocol):
    async def run():
        async with realtime_target(protocol) as (port, state):
            with compiled_proxy(port) as proxy:
                client, request = bounded_client(
                    protocol, f"http://realtime.example.test:{port}", proxy
                )
                result = await client.exchange(request)
                assert result.status == 101
                assert result.payload == {"access": "allow"}
                await asyncio.wait_for(state["closed"].wait(), 1)
                assert state["paths"] == (
                    ["/negotiate", "/ws"] if protocol == "signalr-json" else ["/ws"]
                )
                assert len(state["messages"]) == (
                    2 if protocol == "signalr-json" else 1
                )
                assert client.transport_budget.connections == 1
                assert client.transport_budget.messages == (
                    6 if protocol == "signalr-json" else 2
                )
                before = list(state["paths"])
                with pytest.raises(TargetStopped, match="transport_connections_budget"):
                    await client.exchange(request)
                assert state["paths"] == before

    asyncio.run(run())


@pytest.mark.parametrize("protocol", ["websocket", "signalr-json"])
def test_compiled_proxy_denies_realtime_host_port_and_redirect(protocol):
    async def run():
        async with (
            realtime_target(protocol) as (port, target),
            realtime_target(protocol) as (forbidden_port, forbidden),
        ):
            with compiled_proxy(port) as proxy:
                # An admitted transport case cannot enlarge the separately
                # compiled network policy. Both listeners remain untouched.
                for origin in (
                    f"http://unrelated.example.test:{port}",
                    f"http://realtime.example.test:{forbidden_port}",
                ):
                    client, request = bounded_client(protocol, origin, proxy)
                    result = await client.exchange(request)
                    assert result.status == 403
                    assert not target["paths"] and not forbidden["paths"]
                # Application scope rejects literal IPs before transport.
                with pytest.raises(ValidationError, match="with a DNS name"):
                    bounded_client(protocol, f"http://127.0.0.1:{port}", proxy)
                assert not target["paths"] and not forbidden["paths"]
                # Independently prove that bypassing the application client
                # cannot bypass the proxy's literal-IP restriction either.
                async with aiohttp.ClientSession(
                    trust_env=False, timeout=aiohttp.ClientTimeout(total=5)
                ) as session:
                    with pytest.raises(aiohttp.WSServerHandshakeError) as denied:
                        await session.ws_connect(
                            f"http://127.0.0.1:{port}/ws", proxy=proxy
                        )
                    assert denied.value.status == 403
                assert not target["paths"] and not forbidden["paths"]
                target["redirect_port"] = forbidden_port
                client, request = bounded_client(
                    protocol, f"http://realtime.example.test:{port}", proxy
                )
                with pytest.raises(PermissionError, match="redirects are not admitted"):
                    await client.exchange(request)
                assert target["paths"] == (
                    ["/negotiate"] if protocol == "signalr-json" else ["/ws"]
                )
                assert not target["messages"]
                assert not forbidden["paths"] and not forbidden["messages"]

    asyncio.run(run())
