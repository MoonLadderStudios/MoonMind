import pytest

from moonmind.security.target_assessments import (
    ScopedTargetClient,
    TargetRequest,
    TargetScope,
)


def test_exact_named_realtime_case():
    scope = TargetScope.model_validate(
        {
            "web": {
                "origin": "http://target.test:8099",
                "routes": [{"path": "/", "methods": ["GET", "POST"]}],
                "mode": "active",
                "activeStrength": "low",
                "egressPolicyRef": "local",
            },
            "realtime": {
                "cases": [
                    {
                        "caseRef": "read",
                        "protocol": "websocket",
                        "path": "/ws",
                        "anonymous": True,
                        "messageType": "read",
                        "payload": {"type": "read", "channel": "one"},
                        "effect": "read_only",
                    }
                ]
            },
        }
    )
    client = ScopedTargetClient(scope, proxy="http://localhost:8888")
    request = TargetRequest(
        id="one",
        protocol="websocket",
        path="/ws",
        method="GET",
        message_type="read",
        realtime_case_ref="read",
        effect="read_only",
        payload={"type": "read", "channel": "one"},
    )
    client.admit(request)
    request.payload["channel"] = "two"
    with pytest.raises(PermissionError):
        client.admit(request)


import asyncio
import json
from contextlib import asynccontextmanager

from aiohttp import web

from moonmind.security.target_assessments import TargetStopped


def realtime_scope(protocol="websocket", **limits):
    case = {
        "caseRef": "read",
        "protocol": protocol,
        "path": "/ws",
        "anonymous": True,
        "messageType": "read",
        "payload": {"type": "read", "channel": "one"},
        "effect": "read_only",
    }
    if protocol == "signalr-json":
        case.update(
            negotiationPath="/negotiate", payload={"arguments": [{"channel": "one"}]}
        )
    return TargetScope.model_validate(
        {
            "web": {
                "origin": "http://fixture.test",
                "routes": [{"path": "/", "methods": ["GET", "POST"]}],
                "mode": "active",
                "activeStrength": "low",
                "egressPolicyRef": "local",
                "maxRequestsPerSecond": 100,
            },
            "realtime": {"cases": [case], **limits},
        }
    )


def request_for(scope):
    case = scope.realtime.cases[0]
    return TargetRequest(
        id="operation",
        protocol=case.protocol,
        path=case.path,
        method="GET",
        realtime_case_ref=case.case_ref,
        effect=case.effect,
        message_type=case.message_type,
        payload=json.loads(json.dumps(case.payload)),
    )


@asynccontextmanager
async def fixture_server(mode="normal", signalr=False):
    state = {
        "sends": [],
        "closed": asyncio.Event(),
        "connected": asyncio.Event(),
        "negotiations": 0,
        "paths": [],
        "request_targets": [],
    }

    async def handler(request):
        state["paths"].append(request.path)
        # Observe the actual proxy request target, omitting query tokens.
        state["request_targets"].append(request.raw_path.split("?", 1)[0])
        if mode == "http-proxy" and not request.raw_path.startswith("http://"):
            return web.Response(status=501)
        if request.path == "/identity":
            identity = (
                request.headers.get("Authorization", "").removeprefix("Bearer token-")
                or "anon"
            )
            return web.json_response({"identity": identity})
        if mode.startswith("refuse-"):
            _, stage, status = mode.split("-")
            if request.path == ("/negotiate" if stage == "negotiation" else "/ws"):
                return web.Response(status=int(status), text="Private gateway refusal")
        if mode == "redirect-upgrade" and request.path == "/ws":
            raise web.HTTPFound("http://redirected.test/elsewhere")
        if (
            mode == "blocked-negotiation"
            and request.path == "/negotiate"
            or mode == "blocked-upgrade"
            and request.path == "/ws"
        ):
            state["connected"].set()
            while request.transport is not None and not request.transport.is_closing():
                await asyncio.sleep(0.01)
            state["closed"].set()
            return web.Response(status=503)
        if request.path == "/negotiate":
            state["negotiations"] += 1
            if mode == "redirect":
                raise web.HTTPFound("/elsewhere")
            transports = [
                {
                    "transport": "LongPolling" if mode == "fallback" else "WebSockets",
                    "transferFormats": ["Text"],
                }
            ]
            return web.json_response(
                {
                    "negotiateVersion": 1,
                    "connectionToken": "private-token",
                    "availableTransports": transports,
                }
            )
        socket = web.WebSocketResponse(autoping=False)
        await socket.prepare(request)
        state["connected"].set()
        try:
            async for message in socket:
                state["sends"].append(message.data)
                if signalr and len(state["sends"]) == 1:
                    await socket.send_str("{")
                    await socket.send_str("}\x1e")
                    continue
                if mode == "blocked":
                    continue
                if mode == "binary":
                    await socket.send_bytes(b"unsupported")
                elif mode == "ping":
                    await socket.ping()
                elif mode == "oversize":
                    await socket.send_str("x" * 2048)
                elif signalr:
                    completion = {
                        "type": 3,
                        "invocationId": "wrong" if mode == "unmatched" else "operation",
                        "result": {"access": "allow"},
                    }
                    if mode in {"forbidden", "internal failure"}:
                        completion.pop("result")
                        completion["error"] = mode
                    suffix = "\x1e"
                    if mode == "coalesced":
                        suffix += (
                            json.dumps(
                                {
                                    "type": 1,
                                    "target": "serverInstruction",
                                    "arguments": [],
                                }
                            )
                            + "\x1e"
                        )
                    await socket.send_str(json.dumps(completion) + suffix)
                else:
                    await socket.send_json({"access": "allow"})
        finally:
            state["closed"].set()
        return socket

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", state
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("protocol", ["websocket", "signalr-json"])
def test_real_protocol_exchange_and_reservation(protocol):
    async def run():
        async with fixture_server(signalr=protocol == "signalr-json") as (proxy, state):
            scope = realtime_scope(protocol)
            client = ScopedTargetClient(scope, proxy=proxy)
            request = request_for(scope)
            reservation = client.reserve_transport(request)
            assert client.transport_budget.connections == 0
            result = await client.exchange(request)
            assert result.payload == {"access": "allow"}
            assert client.transport_budget.connections == 1
            assert client.transport_budget.bytes <= reservation.bytes
            assert client.transport_budget.messages <= reservation.messages
            assert "private-token" not in json.dumps(result.model_dump())
            assert all(
                set(entry.model_dump(by_alias=True))
                == {
                    "requestId",
                    "caseRef",
                    "correlationId",
                    "stage",
                    "direction",
                    "bytes",
                }
                for entry in result.transcript
            )
            if protocol == "signalr-json":
                invocation = json.loads(state["sends"][1].rstrip("\x1e"))
                assert invocation == {
                    "type": 1,
                    "invocationId": "operation",
                    "target": "read",
                    "arguments": [{"channel": "one"}],
                }

    asyncio.run(run())


@pytest.mark.parametrize(
    "mode,protocol",
    [
        ("binary", "websocket"),
        ("ping", "websocket"),
        ("oversize", "websocket"),
        ("redirect", "signalr-json"),
        ("fallback", "signalr-json"),
        ("unmatched", "signalr-json"),
        ("coalesced", "signalr-json"),
    ],
)
def test_unsupported_protocols_are_bounded(mode, protocol):
    async def run():
        async with fixture_server(mode, signalr=protocol == "signalr-json") as (
            proxy,
            state,
        ):
            scope = realtime_scope(protocol, max_message_bytes=1024)
            client = ScopedTargetClient(scope, proxy=proxy)
            with pytest.raises((TargetStopped, PermissionError)):
                await client.exchange(request_for(scope))
            if mode in {"redirect", "fallback"}:
                assert not state["sends"]

    asyncio.run(run())


def test_cancellation_closes_blocked_receive():
    async def run():
        async with fixture_server("blocked") as (proxy, state):
            stop = False
            scope = realtime_scope()
            client = ScopedTargetClient(scope, proxy=proxy, should_stop=lambda: stop)
            task = asyncio.create_task(client.exchange(request_for(scope)))
            while not state["sends"]:
                await asyncio.sleep(0.01)
            stop = True
            with pytest.raises(TargetStopped, match="cancelled"):
                await asyncio.wait_for(task, 1)
            await asyncio.wait_for(state["closed"].wait(), 1)
            assert [
                (event.stage, event.direction) for event in client.transport_transcript
            ] == [("application", "send")]

    asyncio.run(run())


def test_budget_and_mutated_payload_never_send():
    async def run():
        async with fixture_server() as (proxy, state):
            scope = realtime_scope(max_connections=1)
            client = ScopedTargetClient(scope, proxy=proxy)
            request = request_for(scope)
            await client.exchange(request)
            with pytest.raises(TargetStopped, match="connections_budget"):
                await client.exchange(request)
            assert len(state["sends"]) == 1
            client.restore_transport({"connections": 0, "messages": 0, "bytes": 0})
            request.payload["channel"] = "other"
            with pytest.raises(PermissionError):
                await client.exchange(request)
            assert len(state["sends"]) == 1

    asyncio.run(run())


@pytest.mark.parametrize("limit", [{"max_messages": 1}, {"max_bytes": 1}])
def test_complete_reservation_refuses_before_connect(limit):
    async def run():
        async with fixture_server() as (proxy, state):
            scope = realtime_scope(**limit)
            client = ScopedTargetClient(scope, proxy=proxy)
            with pytest.raises(TargetStopped, match="budget"):
                await client.exchange(request_for(scope))
            assert not state["connected"].is_set()

    asyncio.run(run())


def test_signalr_receive_frame_budget():
    async def run():
        async with fixture_server(signalr=True) as (proxy, state):
            scope = realtime_scope("signalr-json", max_receive_messages=2)
            client = ScopedTargetClient(scope, proxy=proxy)
            with pytest.raises(TargetStopped, match="receive_budget"):
                await client.exchange(request_for(scope))
            assert (
                len(state["sends"]) == 1
            )  # No application invocation after incomplete handshake.

    asyncio.run(run())


def test_json_scalar_type_substitution_is_not_authorized():
    scope = realtime_scope()
    scope.realtime.cases[0].payload["flag"] = True
    request = request_for(scope)
    request.payload["flag"] = 1
    with pytest.raises(PermissionError):
        ScopedTargetClient(scope, proxy="http://localhost:8888").admit(request)


def test_mutation_during_rate_wait_is_refused_at_send():
    async def run():
        async with fixture_server() as (proxy, state):
            scope = realtime_scope()
            scope.web.max_requests_per_second = 1
            client = ScopedTargetClient(scope, proxy=proxy)
            request = request_for(scope)
            task = asyncio.create_task(client.exchange(request))
            await asyncio.wait_for(state["connected"].wait(), 1)
            request.payload["channel"] = "forbidden"
            with pytest.raises(PermissionError):
                await task
            assert not state["sends"]

    asyncio.run(run())


@pytest.mark.parametrize("error", ["forbidden", "internal failure"])
def test_signalr_completion_error_is_ephemeral_policy_input(error):
    async def run():
        async with fixture_server(error, signalr=True) as (proxy, _state):
            scope = realtime_scope("signalr-json")
            client = ScopedTargetClient(scope, proxy=proxy)
            observation = await client.exchange(request_for(scope))
            assert observation.payload == {"error": error}
            assert error not in json.dumps(
                observation.model_dump(by_alias=True)["transcript"]
            )

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["blocked-negotiation", "blocked-upgrade"])
def test_cancellation_closes_blocked_transport_setup(mode):
    async def run():
        async with fixture_server(mode, signalr=True) as (proxy, state):
            stop = False
            scope = realtime_scope("signalr-json")
            client = ScopedTargetClient(scope, proxy=proxy, should_stop=lambda: stop)
            task = asyncio.create_task(client.exchange(request_for(scope)))
            await asyncio.wait_for(state["connected"].wait(), 1)
            stop = True
            with pytest.raises(TargetStopped, match="cancelled"):
                await asyncio.wait_for(task, 1)
            await asyncio.wait_for(state["closed"].wait(), 1)
            assert not state["sends"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        {"payload": {"secret": "token"}},
        {"token": "private-token"},
        {"stage": "arbitrary"},
        {"direction": "both"},
        {"bytes": -1},
        {"bytes": 256 * 1024 + 1},
        {"bytes": True},
        {"requestId": "x" * 65},
        {"caseRef": "invalid identifier"},
        {"correlationId": "x" * 65},
    ],
)
def test_transport_transcript_rejects_unbounded_or_arbitrary_fields(change):
    from pydantic import ValidationError

    from moonmind.security.target_assessments import TargetObservation

    event = {
        "requestId": "operation",
        "caseRef": "read",
        "correlationId": "operation",
        "stage": "application",
        "direction": "receive",
        "bytes": 10,
    }
    event.update(change)
    with pytest.raises(ValidationError):
        TargetObservation(
            request_id="operation",
            protocol="websocket",
            status=101,
            payload={},
            evidence_ref="sha256:example",
            transcript=[event],
        )


def test_transport_transcript_bound_and_legacy_absence():
    from pydantic import ValidationError

    from moonmind.security.target_assessments import TargetObservation

    legacy = {
        "request_id": "operation",
        "protocol": "websocket",
        "status": 101,
        "payload": {},
        "evidence_ref": "sha256:example",
    }
    assert "transcript" not in TargetObservation(**legacy).model_dump(by_alias=True)
    event = {
        "requestId": "operation",
        "caseRef": "read",
        "correlationId": "operation",
        "stage": "application",
        "direction": "receive",
        "bytes": 10,
    }
    with pytest.raises(ValidationError):
        TargetObservation(**legacy, transcript=[event] * 260)
    roundtrip = TargetObservation(**legacy, transcript=[event]).model_dump(
        by_alias=True
    )
    assert roundtrip["transcript"] == [event]


def test_partial_transport_transcript_is_copied_and_resets_on_failed_admission():
    async def run():
        async with fixture_server("binary") as (proxy, _state):
            scope = realtime_scope()
            client = ScopedTargetClient(scope, proxy=proxy)
            request = request_for(scope)
            assert client.transport_transcript == []
            with pytest.raises(TargetStopped, match="unsupported_realtime_frame"):
                await client.exchange(request)
            events = client.transport_transcript
            assert len(events) == 1
            assert events[0].stage == "application"
            assert events[0].direction == "send"
            events[0].request_id = "tampered"
            events.clear()
            assert client.transport_transcript[0].request_id == request.id
            request.payload["channel"] = "forbidden"
            with pytest.raises(PermissionError):
                await client.exchange(request)
            assert client.transport_transcript == []

    asyncio.run(run())


@pytest.mark.parametrize("protocol", ["websocket", "signalr-json"])
def test_named_upgrade_redirect_never_contacts_destination(protocol):
    async def run():
        async with fixture_server(
            "redirect-upgrade", signalr=protocol == "signalr-json"
        ) as (proxy, state):
            scope = realtime_scope(protocol)
            client = ScopedTargetClient(scope, proxy=proxy)
            with pytest.raises(PermissionError, match="redirects"):
                await client.exchange(request_for(scope))
            assert "/ws" in state["paths"]
            assert "/elsewhere" not in state["paths"]
            assert not state["sends"]

    asyncio.run(run())


def test_caller_scope_mutation_during_rate_wait_cannot_widen_authority():
    async def run():
        async with fixture_server() as (proxy, state):
            scope = realtime_scope()
            scope.web.max_requests_per_second = 1
            request = request_for(scope)
            client = ScopedTargetClient(scope, proxy=proxy)
            task = asyncio.create_task(client.exchange(request))
            await asyncio.wait_for(state["connected"].wait(), 1)
            scope.realtime.cases[0].payload["channel"] = "forbidden"
            scope.realtime.max_connections = 8192
            result = await task
            assert result.payload == {"access": "allow"}
            assert json.loads(state["sends"][0])["channel"] == "one"
            assert client.scope.realtime.max_connections == 32
            with pytest.raises(PermissionError):
                await client.exchange(request_for(scope))
            assert len(state["sends"]) == 1

    asyncio.run(run())


def test_direct_scope_mutation_during_rate_wait_is_refused():
    async def run():
        async with fixture_server() as (proxy, state):
            scope = realtime_scope()
            scope.web.max_requests_per_second = 1
            client = ScopedTargetClient(scope, proxy=proxy)
            task = asyncio.create_task(client.exchange(request_for(scope)))
            await asyncio.wait_for(state["connected"].wait(), 1)
            client.scope.realtime.max_connections = 8192
            with pytest.raises(PermissionError, match="scope changed"):
                await task
            assert not state["sends"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "protocol,stage",
    [
        ("websocket", "upgrade"),
        ("signalr-json", "upgrade"),
        ("signalr-json", "negotiation"),
    ],
)
@pytest.mark.parametrize("status", [401, 403])
def test_named_transport_refusal_never_manufactures_application_denial(
    protocol, stage, status
):
    async def run():
        async with fixture_server(
            f"refuse-{stage}-{status}", signalr=protocol == "signalr-json"
        ) as (proxy, state):
            scope = realtime_scope(protocol)
            client = ScopedTargetClient(scope, proxy=proxy)
            observation = await client.exchange(request_for(scope))
            assert observation.status == status
            assert observation.payload == {}
            assert observation.transcript == client.transport_transcript
            assert not state["sends"]
            assert "Private gateway refusal" not in observation.model_dump_json()

    asyncio.run(run())


@pytest.mark.parametrize(
    "protocol,stage",
    [
        ("websocket", "upgrade"),
        ("signalr-json", "upgrade"),
        ("signalr-json", "negotiation"),
    ],
)
@pytest.mark.parametrize("status", [401, 403])
def test_named_transport_refusal_is_inconclusive_with_verified_identity(
    tmp_path, protocol, stage, status
):
    from moonmind.security.application_access import (
        JsonAssertion,
        assess_application_access,
    )
    from moonmind.security.application_access_target import observe_application_access
    from tests.unit.security.test_realtime_application_access import (
        admitted_scope,
        selected_realtime,
    )

    async def run():
        selected = selected_realtime(protocol, "other")
        selected.cases[0].deny_assertions = [
            JsonAssertion(id="denied", path="access", value="deny")
        ]
        (tmp_path / "other").write_text("token-other")
        async with fixture_server(
            f"refuse-{stage}-{status}", signalr=protocol == "signalr-json"
        ) as (proxy, state):
            client = ScopedTargetClient(
                admitted_scope(selected), proxy=proxy, credential_root=tmp_path
            )
            capture = await observe_application_access(
                selected, client, checkpoint_path=tmp_path / "capture"
            )
            assert state["paths"].count("/identity") == 2
            assert not state["sends"]
        observation = capture.bundle.observations[0]
        assert observation.status == status
        report = assess_application_access(selected, capture.bundle)
        assert report.cases[0].outcome == "inconclusive"
        assert report.status == "partial"
        assert (
            "Private gateway refusal"
            not in capture.model_dump_json() + report.model_dump_json()
        )

    asyncio.run(run())


@pytest.mark.parametrize("protocol", ["websocket", "signalr-json", "legacy-websocket"])
def test_proxy_upgrade_uses_admitted_http_request_target(protocol):
    async def run():
        async with fixture_server("http-proxy", signalr=protocol == "signalr-json") as (
            proxy,
            state,
        ):
            scope = realtime_scope(
                "websocket" if protocol == "legacy-websocket" else protocol
            )
            request = request_for(scope)
            if protocol == "legacy-websocket":
                scope.realtime = None
                scope.messages = {"/ws": ["read"]}
                request.realtime_case_ref = None
                request.effect = None
            client = ScopedTargetClient(scope, proxy=proxy)
            observation = await client.exchange(request)
            assert observation.status == 101
            assert observation.payload == {"access": "allow"}
            assert state["request_targets"][-1] == "http://fixture.test/ws"
            assert all(
                target.startswith("http://fixture.test/")
                for target in state["request_targets"]
            )
            assert len(state["sends"]) == (2 if protocol == "signalr-json" else 1)

    asyncio.run(run())
