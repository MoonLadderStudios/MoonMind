"""Replay the stock host UUID wire contract without provider calls or credentials."""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_ports import expected_omnigent_host_id
from moonmind.omnigent.host_services.registration import OmnigentHostRegistrationService
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient
from tests.integration.reliability.helpers import load_replay

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize(
    "persisted", [False, True], ids=["new-launch", "in-flight-launch"]
)
@pytest.mark.parametrize("harness", ["opencode-native", "codex-native", "claude-native"])
async def test_targeted_registration_stock_uuid_handoff(
    monkeypatch: pytest.MonkeyPatch, persisted: bool, harness: str
) -> None:
    manifest = load_replay("omnigent-targeted-host-uuid", "manifest.json")
    expected_id = (
        manifest["persistedExpectedHostId"]
        if persisted
        else expected_omnigent_host_id("registration-replay-lease", 1)
    )
    host = dict(manifest["host"], host_id=UUID(expected_id).hex)
    host["configured_harnesses"] = {harness: "ready"}
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.path == f"/v1/hosts/{expected_id}"
        if len(calls) == 1:
            return httpx.Response(404, json={"detail": "host not found"})
        if len(calls) == 2:
            return httpx.Response(200, json=dict(host, status="offline"))
        return httpx.Response(200, json=host)

    client = OmnigentHttpClient(
        base_url="https://omnigent.test", transport=httpx.MockTransport(handler)
    )
    registration = OmnigentHostRegistrationService(
        client=client, expected_owner=host["owner"], attempts=3
    )
    monkeypatch.setattr(registration, "_registration_delay", lambda _attempt: 0)
    result = await registration.wait_for_registration(
        correlation_name=host["name"], harness_id=harness, expected_host_id=expected_id
    )
    assert len(calls) == 3
    assert result["lookupMode"] == "targeted"
    assert result["harnessReady"] is True
    # Attestation and subsequent session creation consume the server's exact ID.
    assert result["omnigentHostId"] == host["host_id"]
    assert result["host"] == host
    if not persisted:
        assert expected_id == host["host_id"]


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("host_id", "00000000000000000000000000000000", "identity mismatch"),
        ("host_id", "invalid-host-id", "identity mismatch"),
        ("host_id", "", "identity mismatch"),
        ("name", "another-host", "name mismatch"),
        ("owner", "another-owner", "owner mismatch"),
    ],
)
async def test_targeted_registration_rejects_foreign_or_malformed_identity(
    field: str, value: str, error: str
) -> None:
    manifest = load_replay("omnigent-targeted-host-uuid", "manifest.json")
    host = dict(manifest["host"], **{field: value})
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=host)

    registration = OmnigentHostRegistrationService(
        client=OmnigentHttpClient(
            base_url="https://omnigent.test", transport=httpx.MockTransport(handler)
        ),
        expected_owner="local",
        attempts=3,
    )
    with pytest.raises(HarnessPlatformError, match=error):
        await registration.wait_for_registration(
            correlation_name=manifest["host"]["name"],
            harness_id="opencode-native",
            expected_host_id=manifest["persistedExpectedHostId"],
        )
    assert len(calls) == 1  # Identity failures never retry against another host.
