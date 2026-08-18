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
from moonmind.omnigent.control_plane.records import SessionRecord

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


@dataclass
class _FakeUser:
    id: str = "op-1"
    is_superuser: bool = True
    settings_permissions: set = field(default_factory=set)


class _FakeRepo:
    def __init__(self, session):
        self._session = session

    async def get(self, session_id):
        if session_id != self._session.session_id:
            return None
        return self._session

    async def list_for_session(self, session_id, **_kwargs):
        return []


class _FakeRepos:
    def __init__(self, session_record):
        self.sessions = _FakeRepo(session_record)
        self.turn_attempts = _FakeRepo(session_record)
        self.observations = _FakeRepo(session_record)
        self.commands = _FakeRepo(session_record)
        self.decisions = _FakeRepo(session_record)

        class _Cleanup:
            async def get(self, _sid):
                return None

        self.cleanup = _Cleanup()


def _build_app(session_record, user):
    app = FastAPI()
    app.include_router(timeline_api.router)
    app.dependency_overrides[get_current_user] = lambda: user
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
        resp = await client.get("/api/omnigent/sessions/sess-1/timeline")
    assert resp.status_code == 403


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
    monkeypatch.setattr(
        timeline_api.ControlPlaneRepositories, "bind", classmethod(lambda cls, db: _FakeRepos(active))
    )
    app = _build_app(active, _FakeUser())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/omnigent/sessions/sess-1/stuck-state")
    assert resp.status_code == 200
    body = resp.json()
    # No observations at all -> active-with-no-recent-evidence -> fenced reconcile.
    assert any(f["reason"] == "moonmind_active_no_recent_evidence" for f in body["findings"])
    assert body["response"]["reconcile"] is True
    assert body["response"]["expectedRevision"] == 7
    assert body["response"]["expectedFencingGeneration"] == 3
