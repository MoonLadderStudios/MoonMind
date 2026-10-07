"""An online host whose harness stays unready fails with the host's own verdict.

Stock Omnigent publishes per-harness readiness on the live host row. When the
launched host is online but reports the selected harness as ``needs-auth``
(for example an OAuth home whose tokens were cleared), waiting out the whole
registration budget and reporting a registration timeout hides the actionable
cause. Once the host has had a full readiness refresh to settle, the wait must
stop with a typed failure naming the observed readiness.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
    remediation_for,
)
from moonmind.omnigent.host_services.registration import (
    HOST_HARNESS_READINESS_SETTLE_SECONDS,
    OmnigentHostRegistrationService,
)

pytestmark = pytest.mark.asyncio


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _SequenceClient:
    """Serve one host row per poll; the last row repeats."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls = 0

    async def get_host(self, host_id: str) -> dict:
        row = self.rows[min(self.calls, len(self.rows) - 1)]
        self.calls += 1
        return dict(row)

    async def list_hosts(self) -> list[dict]:  # pragma: no cover - never used
        raise AssertionError("targeted registration must not scan the inventory")


def _host(host_id: str, *, status: str, readiness: object) -> dict:
    return {
        "host_id": host_id,
        "name": "launch-0",
        "owner": "local",
        "status": status,
        "configured_harnesses": {"claude-native": readiness},
    }


def _service(
    client: _SequenceClient, clock: _Clock, monkeypatch: pytest.MonkeyPatch
) -> OmnigentHostRegistrationService:
    service = OmnigentHostRegistrationService(
        client=client, expected_owner="local", attempts=91, clock=clock
    )

    def _delay(_attempt: int) -> float:
        clock.now += 2.0
        return 0

    monkeypatch.setattr(service, "_registration_delay", _delay)
    return service


async def _wait(service: OmnigentHostRegistrationService, expected: str) -> dict:
    return await service.wait_for_registration(
        correlation_name="launch-0",
        harness_id="claude-native",
        expected_host_id=expected,
    )


async def test_online_host_needing_auth_fails_with_reauthentication_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = str(uuid4())
    host_id = UUID(expected).hex
    client = _SequenceClient(
        [
            _host(host_id, status="offline", readiness="needs-auth"),
            _host(host_id, status="online", readiness="needs-auth"),
        ]
    )
    clock = _Clock()

    with pytest.raises(HarnessPlatformError) as raised:
        await _wait(_service(client, clock, monkeypatch), expected)

    error = raised.value
    assert error.code == HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NEEDS_AUTH
    assert "needs-auth" in str(error)
    assert "claude-native" in str(error)
    assert remediation_for(error.code) == "reauthenticate_provider_profile"
    # The verdict arrives after one readiness settle window, not after the
    # whole registration budget.
    assert client.calls < 91
    assert clock.now - 1000.0 >= HOST_HARNESS_READINESS_SETTLE_SECONDS


async def test_online_host_missing_the_harness_fails_as_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = str(uuid4())
    client = _SequenceClient(
        [_host(UUID(expected).hex, status="online", readiness="binary-missing")]
    )

    with pytest.raises(HarnessPlatformError) as raised:
        await _wait(_service(client, _Clock(), monkeypatch), expected)

    assert raised.value.code == HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY
    assert "binary-missing" in str(raised.value)
    assert client.calls < 91


async def test_readiness_that_settles_inside_the_window_registers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = str(uuid4())
    host_id = UUID(expected).hex
    unready = _host(host_id, status="online", readiness="needs-auth")
    client = _SequenceClient(
        [unready] * 10 + [dict(unready, configured_harnesses={"claude-native": True})]
    )

    result = await _wait(_service(client, _Clock(), monkeypatch), expected)

    assert result["harnessReady"] is True
    assert result["omnigentHostId"] == host_id


async def test_unobservable_readiness_value_is_not_echoed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = str(uuid4())
    client = _SequenceClient(
        [
            _host(
                UUID(expected).hex,
                status="online",
                readiness={"detail": "token sk-secret-value"},
            )
        ]
    )

    with pytest.raises(HarnessPlatformError) as raised:
        await _wait(_service(client, _Clock(), monkeypatch), expected)

    assert raised.value.code == HarnessPlatformFailure.OMNIGENT_HOST_HARNESS_NOT_READY
    assert "sk-secret-value" not in str(raised.value)
