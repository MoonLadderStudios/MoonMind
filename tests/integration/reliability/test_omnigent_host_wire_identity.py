"""Replay the production UUID mismatch through the real HTTP adapter."""

import json
import uuid
from pathlib import Path

import httpx
import pytest

from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.host_ports import expected_omnigent_host_id
from moonmind.omnigent.host_services.registration import OmnigentHostRegistrationService
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

pytestmark = [pytest.mark.integration, pytest.mark.reliability_journey]


@pytest.mark.asyncio
@pytest.mark.parametrize("credentialless", [False, True])
@pytest.mark.parametrize("mismatch", [None, "host_id", "owner", "name"])
async def test_host_wire_identity_registration(monkeypatch, credentialless, mismatch):
    fixture = json.loads(
        (
            Path(__file__).parent
            / "replays"
            / "omnigent-host-uuid-wire-identity"
            / "manifest.json"
        ).read_text()
    )
    lease, generation = fixture["hostLeaseRef"], fixture["hostLeaseGeneration"]
    expected = expected_omnigent_host_id(lease, generation)
    # Stock host identity loading and DB UUID storage use bare hex. This also
    # models a host already launched with the previous dashed environment ID.
    previous_launch_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{lease}:{generation}"))
    registered_id = uuid.UUID(previous_launch_id).hex
    requests = []

    async def handle(request):
        assert request.url.path == f"/v1/hosts/{expected}"
        event = fixture["eventScript"][len(requests)]
        requests.append(event)
        if event == "not_found":
            return httpx.Response(404, json={"detail": "host not found"})
        host = {
            "host_id": registered_id,
            "name": "mm-host-replay",
            "owner": "local",
            "status": event,
            "configured_harnesses": {
                "opencode-native": "needs-auth" if credentialless else "ready"
            },
            "runners": [],
        }
        if mismatch:
            host[mismatch] = (
                expected_omnigent_host_id(lease, generation + 1)
                if mismatch == "host_id"
                else "another-host-owner-or-name"
            )
        return httpx.Response(200, json=host)

    async def no_sleep(_delay):
        pass

    monkeypatch.setattr(
        "moonmind.omnigent.host_services.registration.asyncio.sleep", no_sleep
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
        service = OmnigentHostRegistrationService(
            client=OmnigentHttpClient(
                base_url="http://omnigent.test", client=transport
            ),
            expected_owner="local",
            attempts=3,
        )
        call = service.wait_for_registration(
            correlation_name="mm-host-replay",
            harness_id="opencode-native",
            credentialless=credentialless,
            expected_host_id=expected,
        )
        if mismatch:
            with pytest.raises(HarnessPlatformError, match="mismatch"):
                await call
            assert requests == ["not_found", "offline"]
        else:
            result = await call
            assert result["omnigentHostId"] == registered_id
            assert result["lookupMode"] == "targeted"
            assert requests == fixture["eventScript"]
