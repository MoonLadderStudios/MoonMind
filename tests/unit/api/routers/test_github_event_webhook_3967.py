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
from api_service.db.models import (
    GitHubEventDeliveryReceipt,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalWorkflowType,
)
from moonmind.statuses.workflow import MoonMindWorkflowState
from moonmind.workflows.adapters.github_event_delivery import _identity_key

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
            await conn.run_sync(
                TemporalExecutionCanonicalRecord.__table__.create,
                checkfirst=True,
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
    app.dependency_overrides[webhook_module.resolve_webhook_secrets] = (
        lambda: {"github-webhook-secret": _SECRET}
    )
    app.dependency_overrides[webhook_module.get_event_dispatcher] = (
        lambda: dispatcher
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, dispatcher, maker, settings
    finally:
        asyncio.run(engine.dispose())


async def _seed_canonical_execution(maker, workflow_id: str) -> None:
    """Mirror production: every dispatched ref has a canonical execution row."""
    import asyncio

    async with maker() as session:
        session.add(
            TemporalExecutionCanonicalRecord(
                workflow_id=workflow_id,
                run_id="run-seed",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                owner_type=TemporalExecutionOwnerType.USER,
                state=MoonMindWorkflowState.INITIALIZING,
                entry="api",
                create_idempotency_key=f"seed:{workflow_id}",
            )
        )
        await session.commit()


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
    client, dispatcher, maker, _ = harness
    first = _post(client, _payload())
    assert first.status_code == 202, first.text
    ref = first.json()["executionRef"]

    import asyncio

    asyncio.run(_seed_canonical_execution(maker, ref))
    second = _post(client, _payload())
    assert second.status_code == 202, second.text
    assert second.json()["decision"] == "redelivery_reuse"
    assert second.json()["executionRef"] == ref
    assert len(dispatcher.calls) == 1


def test_redelivery_without_execution_record_reattempts_same_identity(harness):
    """A receipt referencing a missing execution is a lost start: the
    redelivery re-attempts under the same stable identity instead of
    reusing a dead reference forever."""
    client, dispatcher, maker, _ = harness
    first = _post(client, _payload())
    assert first.status_code == 202, first.text
    ref = first.json()["executionRef"]
    identity = first.json()["identityKey"]

    import asyncio

    # No canonical row seeded: the referenced execution does not exist.
    second = _post(client, _payload())
    assert second.status_code == 202, second.text
    assert second.json()["decision"] == "admitted_dispatched"
    assert second.json()["identityKey"] == identity
    assert len(dispatcher.calls) == 2
    assert dispatcher.calls[1]["identity_key"] == identity

    async def _row():
        async with maker() as session:
            key = f"github-delivery:v1:{_INSTALLATION}:{_REPO}:del-1"
            return await session.get(GitHubEventDeliveryReceipt, key)

    row = asyncio.run(_row())
    assert row.decision == "admitted_dispatched"
    assert row.execution_ref == second.json()["executionRef"]
    assert ref != ""


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


def _post_with_delivery_id(
    client: TestClient,
    payload: dict[str, Any],
    delivery_id: str,
    secret: bytes = _SECRET,
):
    raw = json.dumps(payload).encode("utf-8")
    return client.post(
        "/api/v1/github/events",
        content=raw,
        headers={
            "X-Hub-Signature-256": "sha256="
            + hmac.new(secret, raw, hashlib.sha256).hexdigest(),
            "X-GitHub-Delivery": delivery_id,
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
        },
    )


def _client_with_loader(harness_maker, request_settings, loader_settings, dispatcher):
    """Build an ingress client whose pre-dispatch recheck reads fresh wiring."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    maker = harness_maker
    app = FastAPI()
    app.include_router(webhook_module.router)

    async def _session_override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session_override
    app.dependency_overrides[webhook_module.get_webhook_settings] = (
        lambda: request_settings
    )
    app.dependency_overrides[webhook_module.get_settings_loader] = (
        lambda: (lambda: loader_settings)
    )
    app.dependency_overrides[webhook_module.resolve_webhook_secrets] = (
        lambda: {"github-webhook-secret": _SECRET}
    )
    app.dependency_overrides[webhook_module.get_event_dispatcher] = (
        lambda: dispatcher
    )
    return TestClient(app, raise_server_exceptions=False)


def test_revoked_trigger_between_receipt_and_dispatch_yields_no_launch(harness):
    """A trigger revoked after receipt refuses dispatch (403, no launch)."""
    _, _, maker, _ = harness
    request_settings = webhook_module.WebhookSettings(
        secret_slug="github-webhook-secret",
        trigger_configs=(_trigger_config(),),
    )
    revoked_settings = webhook_module.WebhookSettings(
        secret_slug="github-webhook-secret",
        trigger_configs=(_trigger_config(enabled=False),),
    )
    dispatcher = _Dispatcher()
    client = _client_with_loader(maker, request_settings, revoked_settings, dispatcher)

    import asyncio

    response = _post_with_delivery_id(client, _payload(), "del-revoked")
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["decision"] == "rejected"
    assert body["reasonCode"] == "revoked_before_dispatch"
    assert body["executionRef"] in {"", None}
    assert dispatcher.calls == []

    async def _row():
        async with maker() as session:
            key = f"github-delivery:v1:{_INSTALLATION}:{_REPO}:del-revoked"
            return await session.get(GitHubEventDeliveryReceipt, key)

    row = asyncio.run(_row())
    assert row is not None
    assert row.decision == "rejected"
    assert row.reason_code == "revoked_before_dispatch"
    assert (row.execution_ref or "") == ""


class _FlakyDispatcher:
    """Controllable dispatch: fail/empty once, then succeed under same identity."""

    def __init__(self, mode: str = "fail") -> None:
        self.mode = mode
        self.calls: list[dict[str, Any]] = []

    async def dispatch(self, **kwargs: Any) -> str:
        self.calls.append(dict(kwargs))
        if self.mode == "fail":
            raise RuntimeError("temporal-unavailable")
        if self.mode == "empty":
            return ""
        return f"temporal:exec-{len(self.calls)}"


def test_dispatch_failure_stays_pending_and_redelivery_reattempts_same_identity(
    harness,
):
    """A dispatcher exception stays admitted_pending; redelivery re-attempts."""
    _, _, maker, _ = harness
    settings = webhook_module.WebhookSettings(
        secret_slug="github-webhook-secret",
        trigger_configs=(_trigger_config(),),
    )
    dispatcher = _FlakyDispatcher(mode="fail")
    client = _client_with_loader(maker, settings, settings, dispatcher)

    import asyncio

    first = _post_with_delivery_id(client, _payload(), "del-flaky")
    assert first.status_code == 202, first.text
    first_body = first.json()
    assert first_body["decision"] == "admitted_pending"
    assert first_body["reasonCode"] == "dispatch_failed"
    assert first_body["executionRef"] in {"", None}
    identity = first_body["identityKey"]
    assert identity.startswith("github-event:v1:")

    async def _row():
        async with maker() as session:
            key = f"github-delivery:v1:{_INSTALLATION}:{_REPO}:del-flaky"
            return await session.get(GitHubEventDeliveryReceipt, key)

    row = asyncio.run(_row())
    assert row is not None
    assert row.decision == "admitted_pending"
    assert (row.execution_ref or "") == ""
    # The receipt carries an actionable diagnostic, not just the class name.
    assert row.reason_code.startswith("dispatch_failed_RuntimeError")
    assert "temporal-unavailable" in row.reason_code

    dispatcher.mode = "succeed"
    second = _post_with_delivery_id(client, _payload(), "del-flaky")
    assert second.status_code == 202, second.text
    second_body = second.json()
    assert second_body["decision"] == "admitted_dispatched"
    assert second_body["executionRef"].startswith("temporal:exec-")
    assert second_body["identityKey"] == identity
    assert len(dispatcher.calls) == 2
    assert dispatcher.calls[0]["identity_key"] == identity
    assert dispatcher.calls[1]["identity_key"] == identity

    row = asyncio.run(_row())
    assert row.decision == "admitted_dispatched"
    assert row.execution_ref == second_body["executionRef"]


def test_empty_dispatch_reference_stays_pending_and_redelivery_dispatches(harness):
    """An empty dispatch reference stays pending; redelivery dispatches once."""
    _, _, maker, _ = harness
    settings = webhook_module.WebhookSettings(
        secret_slug="github-webhook-secret",
        trigger_configs=(_trigger_config(),),
    )
    dispatcher = _FlakyDispatcher(mode="empty")
    client = _client_with_loader(maker, settings, settings, dispatcher)

    import asyncio

    first = _post_with_delivery_id(client, _payload(), "del-empty")
    assert first.status_code == 202, first.text
    assert first.json()["decision"] == "admitted_pending"
    assert first.json()["reasonCode"] == "dispatch_empty_reference"

    dispatcher.mode = "succeed"
    second = _post_with_delivery_id(client, _payload(), "del-empty")
    assert second.status_code == 202, second.text
    assert second.json()["decision"] == "admitted_dispatched"
    assert second.json()["executionRef"].startswith("temporal:exec-")
    assert len(dispatcher.calls) == 2


def test_crash_before_start_redelivery_via_http_reuses_logical_request(harness):
    """A receipt with no execution ref redelivered via HTTP dispatches once."""
    client, dispatcher, maker, _ = harness

    import asyncio

    payload = _payload()
    raw = json.dumps(payload).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    key = f"github-delivery:v1:{_INSTALLATION}:{_REPO}:del-crash"

    async def _seed():
        async with maker() as session:
            session.add(
                GitHubEventDeliveryReceipt(
                    delivery_key=key,
                    repository=_REPO,
                    event_name="issues",
                    action="labeled",
                    payload_digest=digest,
                    decision="admitted_pending",
                    reason_code="admitted",
                    preset_slug="triage-preset",
                )
            )
            await session.commit()

    asyncio.run(_seed())
    response = _post_with_delivery_id(client, payload, "del-crash")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["decision"] == "admitted_dispatched"
    assert body["executionRef"].startswith("temporal:exec-")
    assert len(dispatcher.calls) == 1
    assert dispatcher.calls[0]["identity_key"] == _identity_key(
        installation_id=_INSTALLATION,
        repository=_REPO,
        delivery_id="del-crash",
        digest_hex=digest,
    )

    async def _row():
        async with maker() as session:
            return await session.get(GitHubEventDeliveryReceipt, key)

    row = asyncio.run(_row())
    assert row.decision == "admitted_dispatched"
    assert row.execution_ref == body["executionRef"]


def test_oversized_streamed_body_rejected_before_buffering(harness):
    """A body past the limit is cut off mid-stream (413, no launch)."""
    from moonmind.workflows.adapters.github_event_delivery import (
        MAX_WEBHOOK_BODY_BYTES,
    )

    client, dispatcher, _, _ = harness
    raw = b'{"pad": "' + b"x" * (MAX_WEBHOOK_BODY_BYTES + 1) + b'"}'
    response = client.post(
        "/api/v1/github/events",
        content=raw,
        headers={
            "X-Hub-Signature-256": _sign(raw),
            "X-GitHub-Delivery": "del-huge",
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 413, response.text
    assert response.json()["reasonCode"] == "body_too_large"
    assert dispatcher.calls == []


def test_per_trigger_webhook_secret_slug_is_honored(harness):
    """A delivery signed with the trigger's own secret slug verifies."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _, _, maker, _ = harness
    trigger_secret = b"trigger-scoped-secret-3967"
    request_settings = webhook_module.WebhookSettings(
        secret_slug="github-webhook-secret",
        trigger_configs=(
            _trigger_config(webhook_secret_slug="trigger-secret"),
        ),
    )
    dispatcher = _Dispatcher()
    app = FastAPI()
    app.include_router(webhook_module.router)

    async def _session_override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session_override
    app.dependency_overrides[webhook_module.get_webhook_settings] = (
        lambda: request_settings
    )
    app.dependency_overrides[webhook_module.get_settings_loader] = (
        lambda: (lambda: request_settings)
    )
    # Global slug resolves nothing; only the trigger slug verifies.
    app.dependency_overrides[webhook_module.resolve_webhook_secrets] = (
        lambda: {"trigger-secret": trigger_secret}
    )
    app.dependency_overrides[webhook_module.get_event_dispatcher] = (
        lambda: dispatcher
    )
    client = TestClient(app, raise_server_exceptions=False)

    raw = json.dumps(_payload()).encode("utf-8")
    response = client.post(
        "/api/v1/github/events",
        content=raw,
        headers={
            "X-Hub-Signature-256": "sha256="
            + hmac.new(trigger_secret, raw, hashlib.sha256).hexdigest(),
            "X-GitHub-Delivery": "del-trigger-secret",
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["decision"] == "admitted_dispatched"
    assert len(dispatcher.calls) == 1


def test_concurrent_changed_body_loser_conflicts_without_new_work(harness):
    """An insert-race loser with a divergent body gets 409, never dispatch."""
    import asyncio
    import hashlib as _hashlib
    import json as _json

    client, dispatcher, maker, _ = harness
    payload = _payload()
    raw = _json.dumps(payload).encode("utf-8")
    winner_digest = _hashlib.sha256(b"winner-body").hexdigest()
    key = f"github-delivery:v1:{_INSTALLATION}:{_REPO}:del-race"

    async def _seed_winner():
        async with maker() as session:
            session.add(
                GitHubEventDeliveryReceipt(
                    delivery_key=key,
                    repository=_REPO,
                    event_name="issues",
                    action="labeled",
                    payload_digest=winner_digest,
                    decision="admitted_pending",
                    reason_code="admitted",
                    preset_slug="triage-preset",
                )
            )
            await session.commit()

    asyncio.run(_seed_winner())
    response = _post_with_delivery_id(client, payload, "del-race")
    assert response.status_code == 409, response.text
    assert response.json()["decision"] == "conflict"
    assert response.json()["reasonCode"] == "changed_body_conflict"
    assert dispatcher.calls == []


def test_stale_stored_receipt_redelivery_rejected(harness):
    """A weeks-old pending receipt redelivered by hand never launches."""
    import asyncio
    import datetime
    import hashlib as _hashlib
    import json as _json

    client, dispatcher, maker, _ = harness
    payload = _payload()
    raw = _json.dumps(payload).encode("utf-8")
    digest = _hashlib.sha256(raw).hexdigest()
    key = f"github-delivery:v1:{_INSTALLATION}:{_REPO}:del-stale"
    ancient = datetime.datetime.now(
        datetime.timezone.utc
    ) - datetime.timedelta(days=8)

    async def _seed_stale():
        async with maker() as session:
            session.add(
                GitHubEventDeliveryReceipt(
                    delivery_key=key,
                    repository=_REPO,
                    event_name="issues",
                    action="labeled",
                    payload_digest=digest,
                    decision="admitted_pending",
                    reason_code="admitted",
                    preset_slug="triage-preset",
                    created_at=ancient,
                )
            )
            await session.commit()

    asyncio.run(_seed_stale())
    response = _post_with_delivery_id(client, payload, "del-stale")
    assert response.status_code == 202, response.text
    assert response.json()["decision"] == "rejected"
    assert response.json()["reasonCode"] == "stale_event"
    assert dispatcher.calls == []


def test_pr_backed_issue_rejected_by_default(harness):
    """issues/labeled on a PR cannot prove non-fork: rejected by default."""
    client, dispatcher, _, _ = harness
    payload = _payload(
        issue={"number": 7, "pull_request": {"url": "https://api.github.com/x"}},
    )
    raw = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/api/v1/github/events",
        content=raw,
        headers={
            "X-Hub-Signature-256": _sign(raw),
            "X-GitHub-Delivery": "del-pr-issue",
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code in {202, 403}, response.text
    assert response.json()["reasonCode"] == "fork_content_untrusted"
    assert dispatcher.calls == []
