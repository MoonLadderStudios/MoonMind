"""Qualify targeted registration and pooled transport under concurrent load.

Source issue: MoonLadderStudios/MoonMind#3884.

These hermetic unit tests exercise the production wiring (the real
``OmnigentHostRegistrationService`` and the real ``OmnigentHttpClient``
stream admission against fake servers), not mocked helpers:

* N-way targeted registration at 2, 8, and 16 concurrent launches: no
  normal-path inventory listing, targeted request count scales with launches
  (not inventory size), and no cross-workflow host binding.
* New launches never fall back to scanning the inventory after timeout,
  authorization failure, or identity mismatch.
* Saturated streams cannot starve control operations: streams are admitted
  through a bound while control requests never wait on it, exhaustion is a
  normalized capacity failure within a deadline, and cancellation releases
  admission without leaking the response.
* Production activity compositions resolve the lifecycle-managed transport
  (worker-owned shared pool, else an owned bounded client).
* Registration/pool/stream metrics are bounded and identity-free.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest

from moonmind.omnigent import transport as omnigent_transport_module
from moonmind.omnigent.control_plane import metrics as control_plane_metrics
from moonmind.omnigent.host_services.registration import (
    COMPAT_REGISTRATION_RETIREMENT_TRACKER,
    OmnigentHostRegistrationService,
)
from moonmind.workflows.adapters import omnigent_client as omnigent_client_module
from moonmind.workflows.adapters.omnigent_client import (
    STREAM_ADMISSION_ENV,
    STREAM_ADMISSION_TIMEOUT_ENV,
    OmnigentClientError,
    OmnigentHttpClient,
    aclose_shared_pool_client,
    default_omnigent_pool_limits,
    init_shared_pool_client,
    pooled_http_client,
    reset_stream_admission_for_tests,
    shared_pool_client,
)


def _ready_host(*, host_id: str, name: str, owner: str = "owner-1") -> dict:
    return {
        "host_id": host_id,
        "name": name,
        "owner": owner,
        "status": "online",
        "configured_harnesses": {"opencode-native": "ready"},
    }


class _TargetedFakeClient:
    """Fake Omnigent server fronting exact-ID lookups over a big inventory."""

    def __init__(self, hosts: dict[str, dict]) -> None:
        self._hosts = hosts
        self.get_host_calls: list[str] = []
        self.list_hosts_calls = 0

    async def get_host(self, host_id: str) -> dict:
        self.get_host_calls.append(host_id)
        try:
            resolved = self._hosts[UUID(host_id).hex]
        except (ValueError, KeyError):
            raise OmnigentClientError(
                "Omnigent HTTP 404", status_code=404, failure_class="not_found"
            ) from None
        return dict(resolved)

    async def list_hosts(self) -> list[dict]:
        self.list_hosts_calls += 1
        return [dict(host) for host in self._hosts.values()]


def _registration(
    client: object, *, attempts: int = 3
) -> OmnigentHostRegistrationService:
    registration = OmnigentHostRegistrationService(
        client=client, expected_owner="owner-1", attempts=attempts
    )
    registration._registration_delay = lambda _attempt: 0  # type: ignore[method-assign]
    return registration


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [2, 8, 16])
async def test_n_way_targeted_registration_has_no_cross_binding(
    concurrency: int,
) -> None:
    """N concurrent launches bind exactly their own host with no listing."""

    control_plane_metrics.reset()
    # Canonical dashed expected IDs against bare-hex server IDs, over an
    # inventory large enough that any scan would be visible and expensive.
    expected_ids = [str(uuid4()) for _ in range(concurrency)]
    hosts = {
        UUID(expected).hex: _ready_host(
            host_id=UUID(expected).hex, name=f"launch-{index}"
        )
        for index, expected in enumerate(expected_ids)
    }
    for filler in range(500):
        filler_id = uuid4().hex
        hosts[filler_id] = _ready_host(host_id=filler_id, name=f"filler-{filler}")
    fake = _TargetedFakeClient(hosts)
    registration = _registration(fake)

    results = await asyncio.gather(
        *(
            registration.wait_for_registration(
                correlation_name=f"launch-{index}",
                harness_id="opencode-native",
                expected_host_id=expected,
            )
            for index, expected in enumerate(expected_ids)
        )
    )

    # No normal-path inventory listing at any concurrency.
    assert fake.list_hosts_calls == 0
    # Targeted request count scales with launches and bounded attempts, not
    # inventory size: one exact lookup per launch when hosts are ready.
    assert len(fake.get_host_calls) == concurrency
    # No cross-workflow host binding, and the server's exact returned ID is
    # retained for downstream calls (hex, not the dashed expected form).
    for index, (expected, result) in enumerate(zip(expected_ids, results)):
        assert result["lookupMode"] == "targeted"
        assert result["omnigentHostId"] == UUID(expected).hex
        assert result["host"]["name"] == f"launch-{index}"
    assert control_plane_metrics.counter_series() and any(
        name == control_plane_metrics.CONCURRENCY_REGISTRATION_ATTEMPTS
        and labels.get("lookup_mode") == "targeted"
        and count == concurrency
        for name, labels, count in control_plane_metrics.counter_series()
    )


@pytest.mark.asyncio
async def test_new_launch_failures_never_scan_the_inventory() -> None:
    """Timeout, authorization failure, and mismatch stay targeted-only."""

    class _FailingClient:
        def __init__(self, mode: str, host: dict) -> None:
            self.mode = mode
            self.host = host
            self.list_hosts_calls = 0

        async def get_host(self, host_id: str) -> dict:
            if self.mode == "not_found":
                raise OmnigentClientError(
                    "Omnigent HTTP 404", status_code=404, failure_class="not_found"
                )
            if self.mode == "forbidden":
                raise OmnigentClientError(
                    "Omnigent HTTP 403", status_code=403, failure_class="auth_error"
                )
            return dict(self.host)

        async def list_hosts(self) -> list[dict]:
            self.list_hosts_calls += 1
            return [dict(self.host)]

    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    expected = str(uuid4())
    good_host = _ready_host(host_id=UUID(expected).hex, name="launch-0")

    # Not-found exhausts the bounded budget, then fails closed.
    missing = _FailingClient("not_found", good_host)
    with pytest.raises(HarnessPlatformError, match="did not register ready"):
        await _registration(missing, attempts=3).wait_for_registration(
            correlation_name="launch-0",
            harness_id="opencode-native",
            expected_host_id=expected,
        )
    assert missing.list_hosts_calls == 0

    # Authorization failure is not a compatibility case either.
    forbidden = _FailingClient("forbidden", good_host)
    with pytest.raises(HarnessPlatformError, match="targeted host lookup failed"):
        await _registration(forbidden, attempts=3).wait_for_registration(
            correlation_name="launch-0",
            harness_id="opencode-native",
            expected_host_id=expected,
        )
    assert forbidden.list_hosts_calls == 0

    # Identity mismatch never retries against another host by name.
    foreign = _FailingClient(
        "ok", _ready_host(host_id=uuid4().hex, name="launch-0")
    )
    with pytest.raises(HarnessPlatformError, match="identity mismatch"):
        await _registration(foreign, attempts=3).wait_for_registration(
            correlation_name="launch-0",
            harness_id="opencode-native",
            expected_host_id=expected,
        )
    assert foreign.list_hosts_calls == 0


@pytest.mark.asyncio
async def test_compat_path_stays_observable_with_retirement_condition() -> None:
    """The retained no-ID path reports compat and names its removal owner."""

    assert "#3835" in COMPAT_REGISTRATION_RETIREMENT_TRACKER
    control_plane_metrics.reset()

    host = _ready_host(host_id=uuid4().hex, name="legacy-launch")
    fake = _TargetedFakeClient({host["host_id"]: host})
    result = await _registration(fake).wait_for_registration(
        correlation_name="legacy-launch",
        harness_id="opencode-native",
        expected_host_id=None,
    )
    assert result["lookupMode"] == "compat"
    assert result["omnigentHostId"] == host["host_id"]
    assert any(
        name == control_plane_metrics.CONCURRENCY_REGISTRATION_ATTEMPTS
        and labels.get("lookup_mode") == "compat"
        for name, labels, _count in control_plane_metrics.counter_series()
    )


def test_concurrency_metrics_are_bounded_and_identity_free() -> None:
    """New metric families carry no workflow/host/session/credential labels."""

    inventory = control_plane_metrics.label_inventory()
    for name in (
        control_plane_metrics.CONCURRENCY_REGISTRATION_ATTEMPTS,
        control_plane_metrics.CONCURRENCY_REGISTRATION_LATENCY,
        control_plane_metrics.CONCURRENCY_STREAM_ADMISSION_WAIT,
        control_plane_metrics.CONCURRENCY_OPERATION_ERRORS,
    ):
        assert name in inventory, name
        labels = inventory[name]
        assert not (set(labels) & control_plane_metrics.FORBIDDEN_LABEL_KEYS), name
        for label in labels:
            allowed = control_plane_metrics.BOUNDED_LABEL_VALUES[label]
            assert 0 < len(allowed) <= 20, (name, label)


def _admission_env(
    monkeypatch: pytest.MonkeyPatch, *, limit: int, timeout: float
) -> None:
    monkeypatch.setenv(STREAM_ADMISSION_ENV, str(limit))
    monkeypatch.setenv(STREAM_ADMISSION_TIMEOUT_ENV, str(timeout))
    reset_stream_admission_for_tests()


def test_stream_admission_limit_stays_below_pool_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stream admission always reserves at least one pool connection."""

    monkeypatch.setenv("MOONMIND_OMNIGENT_HTTP_MAX_CONNECTIONS", "10")
    monkeypatch.setenv(STREAM_ADMISSION_ENV, "1024")
    assert (
        omnigent_client_module.stream_admission_limit()
        == int(
            omnigent_client_module.default_omnigent_pool_limits().max_connections
        )
        - 1
    )
    monkeypatch.setenv(STREAM_ADMISSION_ENV, "2")
    assert omnigent_client_module.stream_admission_limit() == 2
    reset_stream_admission_for_tests()


@pytest.mark.asyncio
async def test_saturated_streams_cannot_starve_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control requests complete while every stream slot is held."""

    _admission_env(monkeypatch, limit=2, timeout=5.0)
    gate = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stream"):
            await gate.wait()
            return httpx.Response(200, content=b'data: {"type": "done"}\n')
        return httpx.Response(200, json={"ok": True})

    stream_transport = httpx.MockTransport(handler)
    stream_clients = [
        OmnigentHttpClient(
            base_url="https://omnigent.test",
            client=httpx.AsyncClient(transport=stream_transport),
        )
        for _ in range(2)
    ]
    control = OmnigentHttpClient(
        base_url="https://omnigent.test",
        transport=httpx.MockTransport(handler),
    )

    async def _drain(client: OmnigentHttpClient) -> list[dict]:
        return [event async for event in client.stream_events("sess-1")]

    tasks = [asyncio.create_task(_drain(client)) for client in stream_clients]
    try:
        # Let both streams acquire their admission slots and block in the
        # provider read before asserting control still flows.
        for _ in range(100):
            await asyncio.sleep(0.01)
            waiters = omnigent_client_module._stream_semaphore()._value
            if waiters == 0:
                break
        assert omnigent_client_module._stream_semaphore()._value == 0
        # Control never waits on stream admission: it succeeds while streams
        # are saturated.
        assert await control.get_session("sess-1") == {"ok": True}
    finally:
        gate.set()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                # Expected after requesting cancellation of helper tasks.
                pass
    # Cancellation released both admission slots.
    assert omnigent_client_module._stream_semaphore()._value == 2
    reset_stream_admission_for_tests()


@pytest.mark.asyncio
async def test_stream_exhaustion_is_a_normalized_capacity_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A third stream past the bound fails fast with a capacity error."""

    _admission_env(monkeypatch, limit=1, timeout=0.2)
    gate = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, content=b'data: {"type": "done"}\n')

    held = OmnigentHttpClient(
        base_url="https://omnigent.test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    waiter = OmnigentHttpClient(
        base_url="https://omnigent.test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    async def _drain() -> list[dict]:
        return [event async for event in held.stream_events("sess-1")]

    task = asyncio.create_task(_drain())
    try:
        for _ in range(100):
            await asyncio.sleep(0.01)
            if omnigent_client_module._stream_semaphore()._value == 0:
                break
        assert omnigent_client_module._stream_semaphore()._value == 0
        with pytest.raises(OmnigentClientError, match="admission exhausted"):
            async for _event in waiter.stream_events("sess-1"):
                pass
    finally:
        gate.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Expected after requesting cancellation of the helper task.
            pass
    reset_stream_admission_for_tests()


@pytest.mark.asyncio
async def test_stream_admission_exhaustion_records_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admission exhaustion is recorded once, not doubled by the wrapper."""

    control_plane_metrics.reset()
    _admission_env(monkeypatch, limit=1, timeout=0.2)
    gate = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, content=b'data: {"type": "done"}\n')

    held = OmnigentHttpClient(
        base_url="https://omnigent.test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    waiter = OmnigentHttpClient(
        base_url="https://omnigent.test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    async def _drain() -> list[dict]:
        return [event async for event in held.stream_events("sess-1")]

    task = asyncio.create_task(_drain())
    try:
        for _ in range(100):
            await asyncio.sleep(0.01)
            if omnigent_client_module._stream_semaphore()._value == 0:
                break
        with pytest.raises(OmnigentClientError, match="admission exhausted"):
            async for _event in waiter.stream_events("sess-1"):
                pass
    finally:
        gate.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # Expected after requesting cancellation of the helper task.
            pass
    errors = [
        (labels.get("operation_outcome"), count)
        for name, labels, count in control_plane_metrics.counter_series()
        if name == control_plane_metrics.CONCURRENCY_OPERATION_ERRORS
        and labels.get("operation_class") == "streaming"
    ]
    assert ("capacity_exhausted", 1) in errors
    assert ("error", 1) not in errors
    reset_stream_admission_for_tests()


@pytest.mark.asyncio
async def test_stream_response_error_releases_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed stream does not leak its admission slot."""

    _admission_env(monkeypatch, limit=1, timeout=5.0)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    client = OmnigentHttpClient(
        base_url="https://omnigent.test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(OmnigentClientError):
        async for _event in client.stream_events("sess-1"):
            pass
    assert omnigent_client_module._stream_semaphore()._value == 1
    reset_stream_admission_for_tests()


@pytest.mark.asyncio
async def test_stream_cancellation_releases_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling a stalled stream frees its slot for the next stream."""

    _admission_env(monkeypatch, limit=1, timeout=5.0)
    gate = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, content=b'data: {"type": "done"}\n')

    client = OmnigentHttpClient(
        base_url="https://omnigent.test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    async def _drain() -> list[dict]:
        return [event async for event in client.stream_events("sess-1")]

    task = asyncio.create_task(_drain())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert omnigent_client_module._stream_semaphore()._value == 1

    gate.set()
    assert await _drain() == [{"type": "done"}]
    reset_stream_admission_for_tests()


@pytest.mark.asyncio
async def test_pooled_http_client_uses_shared_pool_without_closing_it() -> None:
    """Worker compositions share the pool; the worker owns the close."""

    await aclose_shared_pool_client()
    try:
        shared = await init_shared_pool_client()
        async with pooled_http_client() as resolved:
            assert resolved is shared
        assert shared_pool_client() is shared
        assert resolved.is_closed is False
    finally:
        await aclose_shared_pool_client()
    assert shared_pool_client() is None


@pytest.mark.asyncio
async def test_pooled_http_client_fallback_is_bounded_and_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside a worker the composition still gets a bounded owned client."""

    await aclose_shared_pool_client()
    assert shared_pool_client() is None
    captured: dict = {}
    real_client = httpx.AsyncClient

    def _recording_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        captured.update(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _recording_factory)
    async with pooled_http_client() as owned:
        assert isinstance(owned, real_client)
        assert owned.is_closed is False
    assert owned.is_closed is True
    expected = default_omnigent_pool_limits()
    assert captured["limits"].max_connections == expected.max_connections


@pytest.mark.asyncio
async def test_transport_fallback_without_pool_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owned short-lived transport honors the governed pool limits."""

    captured: dict = {}
    real_client = httpx.AsyncClient

    def _recording_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        captured.update(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _recording_factory)
    async with omnigent_transport_module.omnigent_httpx_client(None) as owned:
        assert isinstance(owned, real_client)
    assert owned.is_closed is True
    assert (
        captured["limits"].max_connections
        == omnigent_transport_module.OMNIGENT_HTTP_DEFAULT_MAX_CONNECTIONS
    )


@pytest.mark.asyncio
async def test_session_client_context_prefers_shared_pool() -> None:
    """Session compositions share the worker pool and never close it."""

    from moonmind.workflows.temporal.activities import omnigent_session_activities

    await aclose_shared_pool_client()
    try:
        shared = await init_shared_pool_client()
        closer, _client = await omnigent_session_activities._omnigent_client_context()
        await closer.aclose()
        assert shared_pool_client() is shared
        assert shared.is_closed is False
    finally:
        await aclose_shared_pool_client()


@pytest.mark.asyncio
async def test_session_client_context_fallback_is_owned_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a worker pool each composition owns one bounded client."""

    from moonmind.workflows.temporal.activities import omnigent_session_activities

    await aclose_shared_pool_client()
    assert shared_pool_client() is None
    captured: dict = {}
    real_client = httpx.AsyncClient

    def _recording_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        captured.update(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _recording_factory)
    first_closer, _first = await omnigent_session_activities._omnigent_client_context()
    second_closer, _second = (
        await omnigent_session_activities._omnigent_client_context()
    )
    expected = default_omnigent_pool_limits()
    assert captured["limits"].max_connections == expected.max_connections
    await first_closer.aclose()
    await second_closer.aclose()
