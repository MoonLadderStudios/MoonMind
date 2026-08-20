"""API boundary tests for the operator session-timeline endpoints.

Source: MoonLadderStudios/MoonMind#3708 ([Omnigent control plane 7/11]).

Exercises the authorized machine-readable timeline endpoint and the stuck-state
endpoint through the real FastAPI route + auth boundary, with the control-plane
repositories faked so the test stays hermetic (no database).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import api_service.api.routers.omnigent_session_timeline as timeline_api
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from moonmind.omnigent.control_plane.records import (
    DecisionRecord,
    ObservationRecord,
    SessionRecord,
    TurnAttemptRecord,
)

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


@dataclass
class _FakeUser:
    id: str = "op-1"
    is_superuser: bool = True
    settings_permissions: set = field(default_factory=set)


class _FakeSessions:
    def __init__(self, session):
        self._session = session

    async def get(self, session_id):
        if session_id != self._session.session_id:
            return None
        return self._session


class _FakeTurnAttempts:
    def __init__(self, active_turn=None, count=0):
        self._active = active_turn
        self._count = count

    async def get(self, turn_attempt_id):
        if self._active is not None and self._active.turn_attempt_id == turn_attempt_id:
            return self._active
        return None

    async def count_for_session(self, _session_id):
        return self._count


class _FakeObservations:
    def __init__(self, latest_event=None, latest_snapshot=None):
        self._event = latest_event
        self._snapshot = latest_snapshot

    async def latest_for_session(self, _session_id, *, observation_types=None):
        types = set(observation_types or ())
        if types & {"snapshot", "provider_snapshot"}:
            return self._snapshot
        return self._event


class _FakeCommands:
    def __init__(self, active=None):
        self._active = active

    async def active_for_session(self, _session_id):
        return self._active


class _FakeDecisions:
    def __init__(self, latest=None, reason_counts=None):
        self._latest = latest
        self._reason_counts = reason_counts or {}

    async def latest_for_session(self, _session_id):
        return self._latest

    async def get(self, _decision_id):
        return self._latest

    async def recent_for_session(self, _session_id, *, limit=10):
        return [self._latest] if self._latest is not None else []

    async def count_for_session_reason(self, _session_id, reason_code):
        return self._reason_counts.get(reason_code, 0)


class _FakeCleanup:
    def __init__(self, cleanup=None):
        self._cleanup = cleanup

    async def get(self, _session_id):
        return self._cleanup


class _FakeRepos:
    def __init__(
        self,
        session_record,
        *,
        active_turn=None,
        turn_count=0,
        latest_event=None,
        latest_snapshot=None,
        active_command=None,
        latest_decision=None,
        reason_counts=None,
        cleanup=None,
    ):
        self.sessions = _FakeSessions(session_record)
        self.turn_attempts = _FakeTurnAttempts(active_turn, turn_count)
        self.observations = _FakeObservations(latest_event, latest_snapshot)
        self.commands = _FakeCommands(active_command)
        self.decisions = _FakeDecisions(latest_decision, reason_counts)
        self.cleanup = _FakeCleanup(cleanup)


def _build_app(session_record, user):
    app = FastAPI()
    app.include_router(timeline_api.router)
    # Support both Depends(get_current_user) and Depends(get_current_user())
    # by overriding the factory and the cached inner dependency.
    app.dependency_overrides[get_current_user] = lambda: user

    try:
        inner = get_current_user()
        app.dependency_overrides[inner] = lambda: user
    except Exception:
        pass
    app.dependency_overrides[get_async_session] = lambda: iter([object()])
    return app


@pytest.fixture()
def session_record():
    return SessionRecord(
        session_id="sess-1",
        moonmind_workflow_id="wf-1",
        provider="codex",
        terminal_state="success",
        cleanup_state="complete",
        revision=4,
        fencing_generation=1,
    )


@pytest.mark.asyncio
async def test_timeline_endpoint_returns_projection(monkeypatch, session_record):
    monkeypatch.setattr(
        timeline_api.ControlPlaneRepositories, "bind", classmethod(lambda cls, db: _FakeRepos(session_record))
    )
    app = _build_app(session_record, _FakeUser())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/omnigent/sessions/sess-1/timeline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sessionId"] == "sess-1"
    assert body["state"]["revision"] == 4
    assert body["explanation"]["status"] == "closed"


@pytest.mark.asyncio
async def test_timeline_endpoint_404_for_unknown_session(monkeypatch, session_record):
    monkeypatch.setattr(
        timeline_api.ControlPlaneRepositories, "bind", classmethod(lambda cls, db: _FakeRepos(session_record))
    )
    app = _build_app(session_record, _FakeUser())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/omnigent/sessions/does-not-exist/timeline")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_timeline_endpoint_requires_operator_permission(monkeypatch, session_record):
    monkeypatch.setattr(
        timeline_api.ControlPlaneRepositories, "bind", classmethod(lambda cls, db: _FakeRepos(session_record))
    )
    unauthorized = _FakeUser(is_superuser=False, settings_permissions=set())
    app = _build_app(session_record, unauthorized)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [
            await client.get(f"/api/omnigent/sessions/sess-1/{suffix}")
            for suffix in ("timeline", "trace", "logs")
        ]
    assert [response.status_code for response in responses] == [403, 403, 403]


@pytest.mark.asyncio
async def test_timeline_links_redirect_through_authorized_server_routes(
    monkeypatch, session_record
):
    decision = DecisionRecord(
        decision_id="dec-1",
        session_id="sess-1",
        decision_code="await_observation",
        trace_ref="trace/one",
        created_at=NOW,
    )
    session_record = SessionRecord(
        **{
            **session_record.__dict__,
            "moonmind_run_id": "run/one",
            "last_decision_ref": "dec-1",
        }
    )
    repos = _FakeRepos(session_record, latest_decision=decision)
    monkeypatch.setattr(
        timeline_api.ControlPlaneRepositories,
        "bind",
        classmethod(lambda cls, db: repos),
    )
    monkeypatch.setenv(
        "MOONMIND_TRACE_URL_TEMPLATE", "https://telemetry.example/traces/{trace_id}"
    )
    monkeypatch.setenv(
        "MOONMIND_LOGS_URL_TEMPLATE",
        "https://telemetry.example/logs/{workflow_id}/{run_id}",
    )
    app = _build_app(session_record, _FakeUser())
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        timeline = (
            await client.get("/api/omnigent/sessions/sess-1/timeline")
        ).json()
        trace = await client.get("/api/omnigent/sessions/sess-1/trace")
        logs = await client.get("/api/omnigent/sessions/sess-1/logs")

    assert timeline["links"] == {
        "trace": "/api/omnigent/sessions/sess-1/trace",
        "logs": "/api/omnigent/sessions/sess-1/logs",
    }
    assert trace.status_code == 307
    assert trace.headers["location"] == "https://telemetry.example/traces/trace%2Fone"
    assert logs.status_code == 307
    assert logs.headers["location"] == (
        "https://telemetry.example/logs/wf-1/run%2Fone"
    )


#: A durably-old turn start so the absence of observations is aged past the
#: staleness deadline regardless of the wall-clock ``now`` the endpoint reads.
_LONG_AGO = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _active_turn(turn_attempt_id="turn-1", *, created_at=_LONG_AGO):
    return TurnAttemptRecord(
        turn_attempt_id=turn_attempt_id,
        session_id="sess-1",
        idempotency_key="k1",
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_stuck_state_endpoint_returns_findings_and_response(monkeypatch):
    active = SessionRecord(
        session_id="sess-1",
        moonmind_workflow_id="wf-1",
        provider="codex",
        active_turn_attempt_id="turn-1",
        revision=7,
        fencing_generation=3,
    )
    repos = _FakeRepos(active, active_turn=_active_turn(), turn_count=1)
    monkeypatch.setattr(
        timeline_api.ControlPlaneRepositories, "bind", classmethod(lambda cls, db: repos)
    )
    app = _build_app(active, _FakeUser())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/omnigent/sessions/sess-1/stuck-state")
    assert resp.status_code == 200
    body = resp.json()
    # No observations, but the turn started long ago -> absence is aged out ->
    # active-with-no-recent-evidence -> fenced reconcile.
    assert any(f["reason"] == "moonmind_active_no_recent_evidence" for f in body["findings"])
    assert body["response"]["reconcile"] is True
    assert body["response"]["expectedRevision"] == 7
    assert body["response"]["expectedFencingGeneration"] == 3


@pytest.mark.asyncio
async def test_stuck_state_endpoint_escalates_with_durable_detection_count(monkeypatch):
    # When the durable decision journal already records the dominant reason at or
    # beyond the persistent-ambiguity threshold, the endpoint escalates to
    # quarantine instead of recommending reconcile forever.
    active = SessionRecord(
        session_id="sess-1",
        moonmind_workflow_id="wf-1",
        provider="codex",
        active_turn_attempt_id="turn-1",
        revision=7,
        fencing_generation=3,
    )
    repos = _FakeRepos(
        active,
        active_turn=_active_turn(),
        turn_count=1,
        reason_counts={"moonmind_active_no_recent_evidence": 3},
    )
    monkeypatch.setattr(
        timeline_api.ControlPlaneRepositories, "bind", classmethod(lambda cls, db: repos)
    )
    app = _build_app(active, _FakeUser())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/omnigent/sessions/sess-1/stuck-state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["response"]["quarantine"] is True
    assert body["response"]["reconcile"] is False


@pytest.mark.asyncio
async def test_stuck_state_endpoint_projects_provider_and_lease_divergence(monkeypatch):
    active = SessionRecord(
        session_id="sess-1",
        moonmind_workflow_id="wf-1",
        provider="codex",
        provider_session_ref="opaque-provider-session",
        observed_state="running",
        compatibility_ref="compat-v1",
        revision=7,
        fencing_generation=3,
    )
    snapshot = ObservationRecord(
        observation_id="snapshot-1",
        session_id="sess-1",
        observation_type="provider_snapshot",
        source="provider_authoritative_snapshot",
        observed_at=_LONG_AGO,
        deduplication_key="snapshot-1",
        bounded_index={
            "providerSession": {"rawStatus": "completed"},
            "hostLease": {"held": True, "consumerActive": False},
            "profileLease": {"held": True, "consumerActive": False},
        },
    )
    repos = _FakeRepos(active, latest_snapshot=snapshot)
    monkeypatch.setattr(
        timeline_api.ControlPlaneRepositories, "bind", classmethod(lambda cls, db: repos)
    )
    app = _build_app(active, _FakeUser())
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (
            await client.get("/api/omnigent/sessions/sess-1/stuck-state")
        ).json()

    reasons = {finding["reason"] for finding in body["findings"]}
    assert "provider_terminal_moonmind_nonterminal" in reasons
    assert "host_lease_without_session_authority" in reasons
    assert "profile_lease_without_consumer" in reasons
