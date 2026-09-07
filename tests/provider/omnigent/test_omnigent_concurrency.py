"""Bounded protected-live concurrency on the credentialless OpenCode route.

Source issue: MoonLadderStudios/MoonMind#3885 (protected-live layer).

This is the only layer that puts real load on a shared provider route, so every
control here is about keeping that load bounded and the evidence honest:

* the level comes from ``MOONMIND_OMNIGENT_CONCURRENCY_LEVEL`` and is capped by
  :data:`MAX_PROTECTED_LIVE_LEVEL`, so a misconfigured run cannot open an
  unbounded number of provider sessions;
* the run is opt-in — without ``MOONMIND_OMNIGENT_PROTECTED_LIVE_CONCURRENCY``
  it fails immediately rather than quietly exercising the provider;
* missing credentials fail the test instead of skipping it, because a skipped
  protected-live row is not a passing row. The runner checks the same
  :data:`~moonmind.omnigent.concurrency_qualification.PROTECTED_LIVE_REQUIRED_ENV`
  *before* it enters the layer, so an unconfigured route is recorded as
  ``unavailable`` naming the variable rather than reaching this failure;
* the observed overlap is published as
  :class:`~moonmind.omnigent.concurrency_qualification.ObservedOverlapEvidence`,
  computed from the per-session start/end windows this run actually observed.

Every session is created, driven, harvested, and closed by its own binding, so
a shared identity between two concurrent runs shows up here as a duplicate
session id rather than in a deployment. Closing is a ``finally``: a wave that
failed part-way through still reclaims every session it opened, because sessions
left on a shared free route consume the next run's capacity and contaminate the
level it measures.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import OmnigentBridgeSession
from moonmind.omnigent.bridge_proxy import (
    BridgePrincipalBinding,
    BridgeSessionCreateRequest,
    BridgeSessionEventRequest,
    OmnigentBridgeError,
    OmnigentBridgeSessionProxy,
)
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.concurrency_qualification import (
    CONCURRENCY_LEVEL_ENV,
    PROTECTED_LIVE_ADMISSION_ENV,
    PROTECTED_LIVE_MAX_LEVEL,
    PROTECTED_LIVE_MINIMUM_LEVEL,
    PROTECTED_LIVE_REQUIRED_ENV,
    ConcurrencyQualificationLayer,
    ExecutionOverlapSample,
    ObservedOverlapEvidence,
    publish_observed_overlap,
    unsatisfied_protected_live_environment,
)
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.provider_verification,
    pytest.mark.requires_credentials,
]

#: The provider-safe ceiling for this route. Declared once, beside the layer's
#: allowed levels, so the qualification runner refuses a level this test would
#: also refuse instead of admitting the layer and failing inside it.
MAX_PROTECTED_LIVE_LEVEL = PROTECTED_LIVE_MAX_LEVEL
DEFAULT_PROTECTED_LIVE_LEVEL = PROTECTED_LIVE_MINIMUM_LEVEL

#: How long one reclaim call may take. Reclaim runs after the measured window
#: has closed, so it is bounded rather than unbounded: a provider that stops
#: answering must not hold the wave open.
_RECLAIM_TIMEOUT_SECONDS = 60.0

_SUCCESS_STATUSES = {"completed", "succeeded"}
_TERMINAL_STATUSES = _SUCCESS_STATUSES | {"failed", "canceled", "timed_out"}
_PROMPT = "Reply with: MM-3885 protected-live concurrency row complete"


def _requested_level() -> int:
    raw = os.environ.get(CONCURRENCY_LEVEL_ENV, "").strip()
    level = int(raw) if raw.isdigit() else DEFAULT_PROTECTED_LIVE_LEVEL
    if level < PROTECTED_LIVE_MINIMUM_LEVEL:
        pytest.fail("a protected-live concurrency row needs at least two executions")
    if level > MAX_PROTECTED_LIVE_LEVEL:
        pytest.fail(
            f"requested level {level} exceeds the provider-safe ceiling "
            f"{MAX_PROTECTED_LIVE_LEVEL}"
        )
    return level


def _live_env() -> dict[str, str]:
    """Return the live environment, failing (not skipping) when it is absent.

    The qualification runner already refuses to enter this layer without
    admission, so reaching this function without credentials is a
    misconfiguration. Skipping here would turn that into a silent pass.
    """

    if os.environ.get(PROTECTED_LIVE_ADMISSION_ENV) != "1":
        pytest.fail(
            "protected-live concurrency is opt-in; it was not admitted for this run"
        )
    # One answer, shared with the runner's precondition, so the layer is never
    # admitted on an environment this test then rejects.
    unsatisfied = unsatisfied_protected_live_environment()
    if unsatisfied:
        pytest.fail(
            "protected-live concurrency requires provider credentials: "
            + ", ".join(unsatisfied)
        )
    return {name: os.environ[name] for name in PROTECTED_LIVE_REQUIRED_ENV}


def _message_event(text: str) -> BridgeSessionEventRequest:
    return BridgeSessionEventRequest(
        type="message",
        data={"role": "user", "content": [{"type": "input_text", "text": text}]},
    )


async def _reclaim_session(
    proxy: OmnigentBridgeSessionProxy, session_id: str
) -> str:
    """Reclaim one provider session, returning why it could not be, or ``""``.

    Every session this layer opens is reclaimed, including the sessions of a
    wave that failed part-way through: repeated qualification runs otherwise
    accumulate provider-side sessions that consume the shared route's capacity
    and contaminate the next run's measurement. Stopping is the reclaim that
    always exists; deleting the record is a deployment policy, so a refusal to
    delete an already-stopped session is reported rather than treated as a
    leak.
    """

    try:
        await asyncio.wait_for(
            proxy.stop_session(session_id), timeout=_RECLAIM_TIMEOUT_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 - every session is offered
        return f"{session_id}: stop failed ({type(exc).__name__}: {exc})"
    try:
        await asyncio.wait_for(
            proxy.delete_session(session_id), timeout=_RECLAIM_TIMEOUT_SECONDS
        )
    except OmnigentBridgeError:
        # Deletion is gated by bridge policy and by harvest completion. The
        # session is already stopped, so it holds no provider capacity.
        return ""
    except Exception as exc:  # noqa: BLE001 - every session is offered
        return f"{session_id}: delete failed ({type(exc).__name__}: {exc})"
    return ""


def _event_status(event: dict[str, object]) -> str:
    session = event.get("session")
    if isinstance(session, dict):
        return str(session.get("status") or "").strip().lower()
    return ""


@pytest_asyncio.fixture
async def bridge_store(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/bridge-concurrency.db"
    )
    async with engine.begin() as conn:
        await conn.run_sync(OmnigentBridgeSession.__table__.create)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield OmnigentBridgeSessionStore(session_maker)
    finally:
        await engine.dispose()


async def test_live_protected_concurrency_row(bridge_store) -> None:
    """Run ``N`` bounded concurrent live sessions and publish observed overlap."""

    env = _live_env()
    level = _requested_level()
    origin = time.monotonic()
    windows: dict[str, list[float]] = {}
    unreclaimed: list[str] = []
    barrier = asyncio.Barrier(level)

    async def one_execution(index: int) -> dict[str, object]:
        client = OmnigentHttpClient(
            base_url=env["OMNIGENT_SERVER_URL"],
            api_token=env["OMNIGENT_API_TOKEN"],
        )
        proxy = OmnigentBridgeSessionProxy(
            run_store=bridge_store,
            client=client,
            default_agent_name=env["OMNIGENT_DEFAULT_AGENT_NAME"],
        )
        run_ref = f"mm-3885-live-concurrency-{index}"
        binding = BridgePrincipalBinding(
            workflow_id=run_ref,
            correlation_id=run_ref,
            idempotency_key=run_ref,
            agent_run_id=f"ar-{run_ref}",
        )
        try:
            created = await proxy.create_session(
                request=BridgeSessionCreateRequest(
                    title=f"MM-3885 protected-live concurrency {index}",
                    host_type="managed",
                ),
                binding=binding,
            )
        except BaseException:
            # An execution that never got a session still has peers parked on
            # the barrier, and a parked peer is a session not yet reclaimed.
            await barrier.abort()
            raise
        session_id = str(created["id"])
        windows[run_ref] = [time.monotonic() - origin, time.monotonic() - origin]
        # Every path out of here — success, provider failure, a peer that never
        # reached the barrier — reclaims this session before it returns.
        try:
            # Hold until every admitted execution has a live session, so the
            # published peak is observed overlap and not staggered execution.
            await asyncio.wait_for(barrier.wait(), timeout=180)
            await proxy.post_event(
                session_id=session_id, event=_message_event(_PROMPT)
            )
            async for event in client.stream_events(session_id):
                if event.get("type") == "response.completed":
                    break
                if _event_status(event) in _TERMINAL_STATUSES:
                    break
            snapshot = await proxy.get_session(session_id)
            harvested = await proxy.harvest_session(session_id)
            return {
                "runRef": run_ref,
                "sessionId": session_id,
                "status": str(snapshot.get("status") or "").lower(),
                "harvested": "resources" in harvested,
            }
        except BaseException:
            await barrier.abort()
            raise
        finally:
            windows[run_ref][1] = time.monotonic() - origin
            failure = await _reclaim_session(proxy, session_id)
            if failure:
                unreclaimed.append(failure)

    results = await asyncio.gather(*(one_execution(i) for i in range(level)))

    # A wave that leaves provider sessions behind is not a repeatable
    # qualification wave, so an unreclaimed session fails the row.
    assert not unreclaimed, unreclaimed

    # Every execution reached a terminal success through its own session.
    assert len({item["sessionId"] for item in results}) == level
    for item in results:
        assert item["status"] in _SUCCESS_STATUSES, item
        assert item["harvested"] is True, item

    overlap = ObservedOverlapEvidence(
        requested_level=level,
        effective_limit=level,
        barrier_synchronized=True,
        samples=tuple(
            ExecutionOverlapSample(
                execution_ref=run_ref,
                started_at=window[0],
                ended_at=max(window[0], window[1]),
            )
            for run_ref, window in sorted(windows.items())
        ),
    )
    assert overlap.observed_peak == level

    publish_observed_overlap(ConcurrencyQualificationLayer.protected_live, overlap)
