"""Ingress tests for the opt-in GitHub event webhook (#3967).

A signed opted-in fixture traverses the real ingress, durable receipt,
preset/admission, and (fake) Temporal dispatch path. Unauthorized actors,
wrong repositories, invalid signatures, disabled triggers, and untrusted
fork content cause no launch or paid effect. Duplicate redelivery reuses
the same logical request; changed-body duplicates conflict.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api_service.api.routers import github_event_webhook as webhook_module
from api_service.db.base import get_async_session
from api_service.db.models import GitHubEventDeliveryReceipt

_SECRET = b"router-test-webhook-secret-8357"
_INSTALLATION = "12345"
_REPO = "acme/repo"


def _trigger_config(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "label-triage",
        "repository": _REPO,
        "installation_id": _INSTALLATION,
        "event_name": "issues",
        "action": "labeled",
        "permitted_actors": ["alice"],
        "label": "mm-ready",
        "preset_slug": "triage-preset",
        "enabled": True,
    }
    base.update(overrides)
    return base


def _payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "action": "labeled",
        "installation": {"id": int(_INSTALLATION)},
        "repository": {"full_name": _REPO},
        "sender": {"login": "alice", "type": "User"},
        "label": {"name": "mm-ready"},
        "issue": {"number": 7},
    }
    base.update(overrides)
    return base


def _sign(raw: bytes) -> str:
    return "sha256=" + hmac.new(_SECRET, raw, hashlib.sha256).hexdigest()


class _Dispatcher:
    """Fake Temporal dispatch: records admitted launches, never pays."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def dispatch(self, **kwargs: Any) -> str:
        self.calls.append(dict(kwargs))
        return f"temporal:exec-{len(self.calls)}"


@pytest.fixture
def harness(monkeypatch, tmp_path):
    db_path = tmp_path / "receipts.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(
                GitHubEventDeliveryReceipt.__table__.create, checkfirst=True
            )

    import asyncio

    asyncio.run(_init())
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _session_override():
        async with maker() as session:
            yield session

    dispatcher = _Dispatcher()
    settings = webhook_module.WebhookSettings(
        secret_slug="github-webhook-secret",
        trigger_configs=(_trigger_config(),),
    )

    app = FastAPI()
    app.include_router(webhook_module.router)
    app.dependency_overrides[get_async_session] = _session_override
    app.dependency_overrides[webhook_module.get_webhook_settings] = lambda: settings
    app.dependency_overrides[webhook_module.get_settings_loader] = (
        lambda: (lambda: settings)
    )
    app.dependency_overrides[webhook_module.resolve_webhook_secret] = (
        lambda: _SECRET
    )
    app.dependency_overrides[webhook_module.get_event_dispatcher] = (
        lambda: dispatcher
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, dispatcher, maker, settings
    finally:
        asyncio.run(engine.dispose())


def _post(client: TestClient, payload: dict[str, Any], secret: bytes = _SECRET):
    raw = json.dumps(payload).encode("utf-8")
    return client.post(
        "/api/v1/github/events",
        content=raw,
        headers={
            "X-Hub-Signature-256": "sha256="
            + hmac.new(secret, raw, hashlib.sha256).hexdigest(),
            "X-GitHub-Delivery": "del-1",
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
        },
    )


async def _receipts(maker) -> list[GitHubEventDeliveryReceipt]:
    async with maker() as session:
        result = await session.execute(select(GitHubEventDeliveryReceipt))
        return list(result.scalars().all())


def test_signed_opted_in_fixture_dispatches_once(harness):
    client, dispatcher, maker, _ = harness
    response = _post(client, _payload())
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["decision"] in {"admitted_dispatched", "admitted"}
    assert body["executionRef"].startswith("temporal:exec-")
    assert body["presetSlug"] == "triage-preset"
    assert len(dispatcher.calls) == 1
    call = dispatcher.calls[0]
    assert call["preset_slug"] == "triage-preset"
    assert call["repository"] == _REPO
    assert call["issue_number"] == 7

    import asyncio

    rows = asyncio.run(_receipts(maker))
    assert len(rows) == 1
    row = rows[0]
    assert row.delivery_key == f"github-delivery:v1:{_INSTALLATION}:{_REPO}:del-1"
    assert row.decision in {"admitted_dispatched", "admitted"}
    assert row.execution_ref.startswith("temporal:exec-")
    assert row.payload_digest == hashlib.sha256(
        json.dumps(_payload()).encode("utf-8")
    ).hexdigest()
    # No secret-bearing payload in ordinary history.
    assert "mm-ready" not in (row.execution_ref or "")
    assert len(row.payload_digest) == 64


def test_invalid_signature_causes_no_launch_or_receipt(harness):
    client, dispatcher, maker, _ = harness
    raw = json.dumps(_payload()).encode("utf-8")
    response = client.post(
        "/api/v1/github/events",
        content=raw,
        headers={
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
            "X-GitHub-Delivery": "del-bad",
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401, response.text
    assert dispatcher.calls == []

    import asyncio

    assert asyncio.run(_receipts(maker)) == []


def test_missing_signature_causes_no_launch(harness):
    client, dispatcher, _, _ = harness
    raw = json.dumps(_payload()).encode("utf-8")
    response = client.post(
        "/api/v1/github/events",
        content=raw,
        headers={
            "X-GitHub-Delivery": "del-nosig",
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401, response.text
    assert dispatcher.calls == []


def test_unauthorized_actor_wrong_repo_disabled_fork_cause_no_launch(harness):
    client, dispatcher, maker, settings = harness
    cases = [
        ("bad-actor", _payload(sender={"login": "mallory", "type": "User"})),
        (
            "wrong-repo",
            _payload(repository={"full_name": "acme/other"}),
        ),
        (
            "fork-content",
            _payload(
                installation={"id": int(_INSTALLATION)},
                pull_request={
                    "head": {"repo": {"fork": True, "full_name": "mallory/repo"}}
                },
            ),
        ),
    ]
    for idx, (label, payload) in enumerate(cases):
        raw = json.dumps(payload).encode("utf-8")
        response = client.post(
            "/api/v1/github/events",
            content=raw,
            headers={
                "X-Hub-Signature-256": _sign(raw),
                "X-GitHub-Delivery": f"del-neg-{idx}",
                "X-GitHub-Event": "issues",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code in {202, 403}, (label, response.text)
        assert response.json()["executionRef"] in {"", None}, label
    assert dispatcher.calls == []

    # Disabled trigger: flip the only config off and reread.
    from moonmind.workflows.adapters.github_event_delivery import load_trigger_configs

    settings.trigger_configs = load_trigger_configs(
        [_trigger_config(enabled=False)]
    )
    response = _post(client, _payload())
    assert response.status_code in {202, 403}, response.text
    assert response.json()["executionRef"] in {"", None}
    assert dispatcher.calls == []


def test_unsupported_events_are_ignored_safely(harness):
    client, dispatcher, _, _ = harness
    raw = json.dumps(
        {
            "zen": "hello",
            "installation": {"id": int(_INSTALLATION)},
            "repository": {"full_name": _REPO},
            "sender": {"login": "alice", "type": "User"},
        }
    ).encode("utf-8")
    response = client.post(
        "/api/v1/github/events",
        content=raw,
        headers={
            "X-Hub-Signature-256": _sign(raw),
            "X-GitHub-Delivery": "del-ping",
            "X-GitHub-Event": "ping",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["decision"] == "ignored"
    assert dispatcher.calls == []


def test_duplicate_redelivery_reuses_same_execution(harness):
    client, dispatcher, _, _ = harness
    first = _post(client, _payload())
    assert first.status_code == 202, first.text
    ref = first.json()["executionRef"]
    second = _post(client, _payload())
    assert second.status_code == 202, second.text
    assert second.json()["decision"] == "redelivery_reuse"
    assert second.json()["executionRef"] == ref
    assert len(dispatcher.calls) == 1


def test_changed_body_duplicate_conflicts_without_new_work(harness):
    client, dispatcher, _, _ = harness
    first = _post(client, _payload())
    assert first.status_code == 202, first.text
    mutated = _payload(issue={"number": 8})
    raw = json.dumps(mutated).encode("utf-8")
    response = client.post(
        "/api/v1/github/events",
        content=raw,
        headers={
            "X-Hub-Signature-256": _sign(raw),
            "X-GitHub-Delivery": "del-1",
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["decision"] == "conflict"
    assert len(dispatcher.calls) == 1


def test_self_generated_app_bot_event_is_ignored(harness):
    client, dispatcher, _, _ = harness
    payload = _payload(sender={"login": "moonmind-bot[bot]", "type": "Bot"})
    raw = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/api/v1/github/events",
        content=raw,
        headers={
            "X-Hub-Signature-256": _sign(raw),
            "X-GitHub-Delivery": "del-self",
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
            "X-GitHub-Hook-Installation-Target-Type": "integration",
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["decision"] == "ignored"
    assert dispatcher.calls == []


def test_receipt_survives_engine_restart(harness, tmp_path):
    client, dispatcher, maker, _ = harness
    first = _post(client, _payload())
    assert first.status_code == 202, first.text
    ref = first.json()["executionRef"]

    import asyncio

    async def _reopen() -> list[GitHubEventDeliveryReceipt]:
        engine2 = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/receipts.db")
        maker2 = async_sessionmaker(engine2, class_=AsyncSession)
        try:
            async with maker2() as session:
                result = await session.execute(select(GitHubEventDeliveryReceipt))
                return list(result.scalars().all())
        finally:
            await engine2.dispose()

    rows = asyncio.run(_reopen())
    assert len(rows) == 1
    assert rows[0].execution_ref == ref
