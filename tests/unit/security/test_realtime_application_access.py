"""Real protocol cases share application policy, durable progress and evidence."""

import pytest
from pydantic import ValidationError

from moonmind.security.application_access import (
    ApplicationAccessSpec,
    JsonAssertion,
    project_response,
)
from tests.unit.security.test_application_access import spec


def realtime_spec(protocol="websocket", *, mutate=False):
    data = spec(mutating=mutate).model_dump(mode="json")
    case = data["cases"][0]
    case["request"].update(
        protocol=protocol,
        method="GET",
        path="/ws",
        realtime_case_ref="read",
        message_type="update" if mutate else "read",
        effect="mutation" if mutate else "read_only",
        payload={"type": "update" if mutate else "read", "channel": "record"},
    )
    if mutate:
        case["request"]["payload"]["mutation"] = "mutation-1"
    if protocol == "signalr-json":
        case["request"]["payload"] = {"arguments": [case["request"]["payload"]]}
        if mutate:
            case["mutation_ref_path"] = "arguments.0.mutation"
    return ApplicationAccessSpec.model_validate(data)


@pytest.mark.parametrize("protocol", ["websocket", "signalr-json"])
@pytest.mark.parametrize("mutate", [False, True])
def test_realtime_case_requires_explicit_effect_and_mutation_correlation(
    protocol, mutate
):
    selected = realtime_spec(protocol, mutate=mutate)
    assert selected.cases[0].request.effect == ("mutation" if mutate else "read_only")
    data = selected.model_dump(mode="json")
    data["cases"][0]["request"]["effect"] = None
    with pytest.raises(ValidationError):
        ApplicationAccessSpec.model_validate(data)
    data = selected.model_dump(mode="json")
    data["cases"][0]["identity_check"]["protocol"] = protocol
    with pytest.raises(ValidationError):
        ApplicationAccessSpec.model_validate(data)


def test_json_assertions_use_bounded_canonical_array_indices():
    checks = [JsonAssertion(id="value", path="arguments.0.value", value="expected")]
    assert project_response({"arguments": [{"value": "expected"}]}, checks)[0].matched
    for path in ["arguments.00.value", "arguments.-1.value", "arguments.2.value"]:
        assert not project_response(
            {"arguments": [{"value": "expected"}]},
            [JsonAssertion(id="value", path=path, value="expected")],
        )[0].matched


import asyncio
import hashlib
import json
from contextlib import asynccontextmanager

from aiohttp import web

from moonmind.security.application_access import (
    assess_application_access,
    bind_job_result,
)
from moonmind.security.application_access_target import (
    ApplicationAccessCapture,
    observe_application_access,
)
from moonmind.security.target_assessments import ScopedTargetClient, TargetScope
from moonmind.workflows.skills.security_assessment import (
    _verify_application_access_report,
)
from tests.unit.security.test_application_access_target import selected


def selected_realtime(protocol, identity="other", *, mutate=False):
    data = selected(identity, mutate=mutate).model_dump(mode="json")
    case = data["cases"][0]
    case["request"].update(
        protocol=protocol,
        method="GET",
        path="/ws",
        realtime_case_ref="read",
        message_type="update" if mutate else "read",
        effect="mutation" if mutate else "read_only",
        payload={"type": "update" if mutate else "read", "channel": "record"},
    )
    if mutate:
        case["request"]["payload"]["mutation"] = "mutation-1"
    if protocol == "signalr-json":
        case["request"]["payload"] = {"arguments": [case["request"]["payload"]]}
        if mutate:
            case["mutation_ref_path"] = "arguments.0.mutation"
    return ApplicationAccessSpec.model_validate(data)


def admitted_scope(s, **limits):
    request = s.cases[0].request
    case = {
        name: getattr(request, name)
        for name in (
            "protocol",
            "path",
            "credential_ref",
            "message_type",
            "payload",
            "effect",
        )
    }
    case.update(
        case_ref=request.realtime_case_ref, anonymous=request.credential_ref is None
    )
    if request.protocol == "signalr-json":
        case["negotiation_path"] = "/negotiate"
    return TargetScope.model_validate(
        {
            "web": {
                "origin": "http://fixture.test",
                "routes": [{"path": "/", "methods": ["GET", "POST"]}],
                "mode": "active",
                "activeStrength": "low",
                "egressPolicyRef": "fixture",
                "maxRequestsPerSecond": 100,
                "timeoutSeconds": 300,
            },
            "maxRequests": 40,
            "credentialSlots": {"slot://" + i: i for i in ["owner", "other", "admin"]},
            "realtime": {"cases": [case], "maxMessageBytes": 2048, **limits},
        }
    )


@asynccontextmanager
async def application_fixture(protocol, *, vulnerable=False, mode="normal"):
    state = {
        "effects": 0,
        "messages": [],
        "version": 1,
        "mutation": None,
        "identity_calls": 0,
        "closed": asyncio.Event(),
        "connected": asyncio.Event(),
    }

    async def handler(request):
        identity = (
            request.headers.get("Authorization", "").removeprefix("Bearer token-")
            or "anon"
        )
        if request.path == "/identity":
            state["identity_calls"] += 1
            return web.json_response(
                {"identity": identity},
                status=401 if mode == "expired" and state["messages"] else 200,
            )
        if request.path == "/record/state":
            return web.json_response(
                {"version": state["version"], "mutation": state["mutation"]}
            )
        if request.path == "/negotiate":
            return web.json_response(
                {
                    "negotiateVersion": 1,
                    "connectionToken": "NEGOTIATION_SECRET",
                    "availableTransports": [
                        {"transport": "WebSockets", "transferFormats": ["Text"]}
                    ],
                }
            )
        socket = web.WebSocketResponse(autoping=False)
        await socket.prepare(request)
        state["connected"].set()
        try:
            async for message in socket:
                value = json.loads(message.data.removesuffix("\x1e"))
                if value.get("protocol") == "json":
                    await socket.send_str("{}\x1e")
                    continue
                payload = value["arguments"][0] if protocol == "signalr-json" else value
                state["messages"].append(payload)
                allowed = identity in {"owner", "admin"} or vulnerable
                if payload["type"] == "update" and allowed:
                    state["effects"] += 1
                    state["version"] += 1
                    state["mutation"] = payload["mutation"]
                if mode == "drop":
                    request.transport.close()
                    break
                if mode == "blocked":
                    continue
                if mode == "binary":
                    await socket.send_bytes(b"unsupported")
                    continue
                response = (
                    {"record": {"id": "record", "secret": "PRIVATE_MARKER"}}
                    if allowed
                    else {"error": "forbidden"}
                )
                if protocol == "signalr-json":
                    response = {
                        "type": 3,
                        "invocationId": value["invocationId"],
                        **({"result": response} if allowed else {"error": "forbidden"}),
                    }
                    await socket.send_str(json.dumps(response) + "\x1e")
                else:
                    await socket.send_json(response)
        finally:
            state["closed"].set()
        return socket

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    state["proxy"] = "http://127.0.0.1:" + str(site._server.sockets[0].getsockname()[1])
    try:
        yield state
    finally:
        await runner.cleanup()


def target_client(s, state, tmp_path, **options):
    for name in ["owner", "other", "admin"]:
        (tmp_path / name).write_text("token-" + name)
    return ScopedTargetClient(
        admitted_scope(s),
        proxy=state["proxy"],
        credential_root=tmp_path,
        max_seconds=options.pop("max_seconds", 10),
        **options
    )


@pytest.mark.parametrize("protocol", ["websocket", "signalr-json"])
@pytest.mark.parametrize("identity", ["owner", "other", "anon"])
@pytest.mark.parametrize("vulnerable", [False, True])
def test_real_repaired_and_vulnerable_channels_and_hub_operations(
    tmp_path, protocol, identity, vulnerable
):
    async def run():
        s = selected_realtime(protocol, identity)
        async with application_fixture(protocol, vulnerable=vulnerable) as state:
            capture = await observe_application_access(
                s,
                target_client(s, state, tmp_path),
                checkpoint_path=tmp_path / "capture",
            )
            assert len(state["messages"]) == 1
            await asyncio.wait_for(state["closed"].wait(), 1)
        report = assess_application_access(s, capture.bundle)
        assert report.cases[0].outcome == (
            "finding" if vulnerable and identity != "owner" else "pass"
        )
        assert capture.transport_budget.connections == 1
        transcript = capture.bundle.observations[0].transcript
        assert transcript and all(e.case_ref == "read" for e in transcript)
        # Retained evidence remains attributable after the target has shut down.
        raw_spec = s.model_dump_json(by_alias=True).encode()
        raw_capture = capture.model_dump_json(by_alias=True).encode()
        report.input_digests = {
            "spec": hashlib.sha256(raw_spec).hexdigest(),
            "capture": hashlib.sha256(raw_capture).hexdigest(),
        }
        report = type(report).model_validate_json(
            bind_job_result(
                report.model_dump_json(by_alias=True).encode(),
                "succeeded",
                0,
                {"spec": "art_spec", "capture": "art_capture"},
            )
        )
        verified, stored = _verify_application_access_report(
            report, raw_spec, raw_capture
        )
        assert verified == s and stored == capture
        for secret in [
            "PRIVATE_MARKER",
            "NEGOTIATION_SECRET",
            "token-owner",
            "token-other",
        ]:
            assert secret not in raw_capture.decode() + report.model_dump_json()

    asyncio.run(run())


@pytest.mark.parametrize("protocol", ["websocket", "signalr-json"])
@pytest.mark.parametrize(
    "mode,reason", [("expired", "identity_failed"), ("binary", "unsupported")]
)
def test_session_revocation_and_unsupported_frames_never_pass(
    tmp_path, protocol, mode, reason
):
    async def run():
        s = selected_realtime(protocol)
        async with application_fixture(protocol, mode=mode) as state:
            capture = await observe_application_access(
                s,
                target_client(s, state, tmp_path),
                checkpoint_path=tmp_path / "capture",
            )
            await asyncio.wait_for(state["closed"].wait(), 1)
        assert capture.bundle.observations[0].stop_reason == reason
        assert assess_application_access(s, capture.bundle).status == "partial"
        assert (
            assess_application_access(s, capture.bundle).cases[0].outcome
            == "inconclusive"
        )

    asyncio.run(run())


@pytest.mark.parametrize("protocol", ["websocket", "signalr-json"])
def test_restart_reconciles_uncertain_mutation_without_reopening_socket(
    tmp_path, protocol
):
    async def run():
        s = selected_realtime(protocol, mutate=True)
        path = tmp_path / "capture"
        async with application_fixture(protocol, vulnerable=True, mode="drop") as state:
            first = await observe_application_access(
                s, target_client(s, state, tmp_path), checkpoint_path=path
            )
            assert first.progress[0].phase == "attempted" and state["effects"] == 1
            reservation = first.transport_budget.model_copy(deep=True)
            second = await observe_application_access(
                s, target_client(s, state, tmp_path), checkpoint_path=path
            )
            assert state["effects"] == 1 and len(state["messages"]) == 1
        assert second.transport_budget == reservation
        assert second.started_at == first.started_at
        report = assess_application_access(s, second.bundle)
        assert report.cases[0].outcome == "finding" and report.status == "partial"
        assert second.bundle.observations[0].receipt_ref == "mutation-1"
        assert ApplicationAccessCapture.model_validate_json(path.read_bytes()) == second

    asyncio.run(run())


@pytest.mark.parametrize("protocol", ["websocket", "signalr-json"])
def test_cancelled_or_expired_original_window_does_not_reconnect(tmp_path, protocol):
    async def run():
        s = selected_realtime(protocol)
        path = tmp_path / "capture"
        async with application_fixture(protocol, mode="blocked") as state:
            task = asyncio.create_task(
                observe_application_access(
                    s, target_client(s, state, tmp_path), checkpoint_path=path
                )
            )
            await asyncio.wait_for(state["connected"].wait(), 1)
            task.cancel()
            capture = await task
            assert capture.stop_reason == "cancelled"
            await asyncio.wait_for(state["closed"].wait(), 1)
            count = len(state["messages"])
            await observe_application_access(
                s, target_client(s, state, tmp_path), checkpoint_path=path
            )
            assert len(state["messages"]) == count
            capture.stop_reason = "request_failed"
            capture.started_at -= 20
            path.write_text(capture.model_dump_json(by_alias=True))
            stopped = await observe_application_access(
                s, target_client(s, state, tmp_path), checkpoint_path=path
            )
            assert stopped.stop_reason == "timeout" and len(state["messages"]) == count

    asyncio.run(run())


@pytest.mark.parametrize("protocol", ["websocket", "signalr-json"])
def test_cumulative_connection_reservation_stops_next_case_before_upgrade(
    tmp_path, protocol
):
    async def run():
        data = selected_realtime(protocol).model_dump(mode="json")
        access = dict(
            data["authorization_matrix"]["cases"][0],
            id="second",
            action="subscribe-again",
        )
        data["authorization_matrix"]["cases"].append(access)
        case = json.loads(json.dumps(data["cases"][0]))
        case["case_ref"] = "second"
        # Same admitted operation, new assessment case, cannot renew transport caps.
        data["cases"].append(case)
        s = ApplicationAccessSpec.model_validate(data)
        async with application_fixture(protocol) as state:
            c = target_client(s, state, tmp_path)
            c.scope = admitted_scope(s, maxConnections=1)
            # Construct from the final authority so client immutability checks apply.
            c = ScopedTargetClient(
                c.scope, proxy=state["proxy"], credential_root=tmp_path, max_seconds=10
            )
            path = tmp_path / "capture"
            capture = await observe_application_access(s, c, checkpoint_path=path)
            assert capture.stop_reason == "budget_exhausted"
            assert len(state["messages"]) == 1
            assert capture.transport_budget.connections == 1
            tampered = capture.model_dump(mode="json")
            del tampered["transport_budget"]
            with pytest.raises(ValueError, match="durable transport"):
                ApplicationAccessCapture.model_validate(tampered)
            tampered = capture.model_dump(mode="json")
            tampered["transport_budget"]["connections"] = 2
            with pytest.raises(ValueError, match="transport budget"):
                ApplicationAccessCapture.model_validate(tampered)

    asyncio.run(run())
