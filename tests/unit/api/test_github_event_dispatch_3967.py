"""Real-service dispatch binding for the opt-in GitHub event path (#3967).

The router ingress suite proves ingress -> receipt -> admission against a
controllable fake dispatcher. These tests close the remaining hop: the
production ``TemporalEventExecutionDispatcher`` binding into the real
``TemporalExecutionService.create_execution`` admission boundary (pause
guard, worker freshness, Skill validation, provider-profile runtime, plan
source) with the stable delivery identity key as the Temporal idempotency
key. Only the Temporal server ``start_workflow`` call is stubbed, following
the established ``mock_client_adapter`` pattern in the Temporal service
suite; every admission gate, the canonical DB insert, and the
idempotency-key reconcile run for real against a real database.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api_service.api.routers import github_event_webhook as webhook_module
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    GitHubEventDeliveryReceipt,
    TemporalExecutionCanonicalRecord,
)
from api_service.services import github_event_dispatch as dispatch_module
from api_service.services.github_event_dispatch import (
    TemporalEventExecutionDispatcher,
    apply_event_execution_limits,
    build_dispatch_parameters,
    enforce_event_publication_intent,
)

_SECRET = b"dispatch-binding-test-secret-3967"
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
        "preset_slug": "github-issue-search-and-implement",
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


@pytest.fixture
def mock_client_adapter():
    adapter = MagicMock()
    adapter.start_workflow = AsyncMock()
    adapter.describe_workflow = AsyncMock(return_value=None)
    adapter.update_workflow = AsyncMock()
    adapter.signal_workflow = AsyncMock()
    adapter.cancel_workflow = AsyncMock()
    adapter.terminate_workflow = AsyncMock()
    return adapter


@pytest.fixture
def service_session_factory(tmp_path, monkeypatch, mock_client_adapter):
    """Real DB + real TemporalExecutionService, stubbed Temporal server hop."""
    import asyncio

    db_path = tmp_path / "dispatch-binding.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    real_service_cls = dispatch_module.TemporalExecutionService

    def _service_factory(session: AsyncSession):
        return real_service_cls(session, client_adapter=mock_client_adapter)

    monkeypatch.setattr(
        dispatch_module, "TemporalExecutionService", _service_factory
    )
    try:
        yield maker
    finally:
        asyncio.run(engine.dispose())


async def _canonical_by_idempotency(maker, identity_key: str):
    async with maker() as session:
        result = await session.execute(
            select(TemporalExecutionCanonicalRecord).where(
                TemporalExecutionCanonicalRecord.create_idempotency_key
                == identity_key
            )
        )
        return list(result.scalars().all())


def test_dispatcher_parameters_pass_real_admission_gates(
    service_session_factory, mock_client_adapter
):
    """The production dispatcher admits through the real execution service."""
    import asyncio

    async def _run() -> tuple[str, str]:
        maker = service_session_factory
        identity_key = (
            f"github-event:v1:{_INSTALLATION}:{_REPO}:del-1:{uuid4().hex[:16]}"
        )
        parameters = build_dispatch_parameters(
            preset_slug="github-issue-search-and-implement",
            repository=_REPO,
            issue_number=7,
            delivery_key=f"github-delivery:v1:{_INSTALLATION}:{_REPO}:del-1",
            identity_key=identity_key,
            execution_limits={},
        )
        async with maker() as session:
            dispatcher = TemporalEventExecutionDispatcher(session)
            ref = await dispatcher.dispatch(
                preset_slug="github-issue-search-and-implement",
                identity_key=identity_key,
                repository=_REPO,
                issue_number=7,
                title=f"GitHub event issues.labeled {_REPO}#7 [github-issue-search-and-implement]",
                parameters=parameters,
            )
            return ref, identity_key

    ref, identity_key = asyncio.run(_run())
    # A real canonical workflow ref, not a fake "temporal:exec-*" token.
    assert ref.startswith("mm:"), ref
    assert mock_client_adapter.start_workflow.await_count == 1
    rows = asyncio.run(_canonical_by_idempotency(service_session_factory, identity_key))
    assert len(rows) == 1
    assert rows[0].workflow_id == ref
    assert rows[0].create_idempotency_key == identity_key


def test_dispatcher_expands_preset_steps_not_prose(
    service_session_factory, mock_client_adapter
):
    """The launch carries the preset's expanded steps, not generic prose."""
    import asyncio

    async def _run():
        maker = service_session_factory
        identity_key = (
            f"github-event:v1:{_INSTALLATION}:{_REPO}:del-expand:{uuid4().hex[:16]}"
        )
        parameters = build_dispatch_parameters(
            preset_slug="github-issue-search-and-implement",
            repository=_REPO,
            issue_number=7,
            delivery_key=f"github-delivery:v1:{_INSTALLATION}:{_REPO}:del-expand",
            identity_key=identity_key,
            execution_limits={},
        )
        async with maker() as session:
            dispatcher = TemporalEventExecutionDispatcher(session)
            ref = await dispatcher.dispatch(
                preset_slug="github-issue-search-and-implement",
                identity_key=identity_key,
                repository=_REPO,
                issue_number=7,
                title="GitHub event issues.labeled",
                parameters=parameters,
            )
            return ref, identity_key

    ref, identity_key = asyncio.run(_run())
    assert ref.startswith("mm:"), ref
    rows = asyncio.run(_canonical_by_idempotency(service_session_factory, identity_key))
    assert len(rows) == 1
    params = rows[0].parameters
    # The service normalizes the expanded task payload under the canonical
    # "workflow" key; the preset's authored steps must survive admission.
    workflow = params.get("workflow") or {}
    assert isinstance(workflow.get("steps"), list) and workflow["steps"]
    assert (workflow.get("taskTemplate") or {}).get("slug") == (
        "github-issue-search-and-implement"
    )
    # The preset slug provenance survives expansion for diagnostics.
    assert params.get("presetSlug") == "github-issue-search-and-implement"
    assert params["githubEventTrigger"]["repository"] == _REPO
    # Search attributes carry the repository for operator filtering.
    assert rows[0].search_attributes.get("mm_repo") == _REPO
    assert rows[0].search_attributes.get("mm_integration") == "github"


def test_execution_limits_translate_to_canonical_budget():
    params = apply_event_execution_limits(
        {"instructions": "x"}, {"maxModelBudgetUsd": 5}
    )
    assert params["maxBudgetUsd"] == 5.0
    assert params["instructions"] == "x"


def test_execution_limits_reject_unknown_or_invalid():
    import pytest as _pytest

    with _pytest.raises(ValueError, match="Unsupported execution_limits"):
        apply_event_execution_limits({}, {"maxSpend": 5})
    with _pytest.raises(ValueError, match="positive number"):
        apply_event_execution_limits({}, {"maxModelBudgetUsd": 0})
    with _pytest.raises(ValueError, match="positive number"):
        apply_event_execution_limits({}, {"maxModelBudgetUsd": "lots"})


def test_publication_intent_none_strips_preset_publish():
    params = enforce_event_publication_intent(
        {
            "task": {"steps": [], "publish": {"mode": "auto"}},
            "publish": {"mode": "auto"},
        },
        "none",
    )
    assert "publish" not in params
    assert "publish" not in params["task"]


def test_publication_intent_other_than_none_rejected():
    import pytest as _pytest

    with _pytest.raises(ValueError, match="publication_intent"):
        enforce_event_publication_intent({}, "draft")


def test_dispatcher_reuses_execution_for_same_identity_key(
    service_session_factory, mock_client_adapter
):
    """A lost start acknowledgment reconciles to the same logical execution."""
    import asyncio

    identity_key = (
        f"github-event:v1:{_INSTALLATION}:{_REPO}:del-2:{uuid4().hex[:16]}"
    )

    async def _dispatch_once() -> str:
        maker = service_session_factory
        parameters = build_dispatch_parameters(
            preset_slug="github-issue-search-and-implement",
            repository=_REPO,
            issue_number=7,
            delivery_key=f"github-delivery:v1:{_INSTALLATION}:{_REPO}:del-2",
            identity_key=identity_key,
            execution_limits={},
        )
        async with maker() as session:
            dispatcher = TemporalEventExecutionDispatcher(session)
            return await dispatcher.dispatch(
                preset_slug="github-issue-search-and-implement",
                identity_key=identity_key,
                repository=_REPO,
                issue_number=7,
                title=f"GitHub event issues.labeled {_REPO}#7 [github-issue-search-and-implement]",
                parameters=parameters,
            )

    first = asyncio.run(_dispatch_once())
    second = asyncio.run(_dispatch_once())
    assert first.startswith("mm:")
    assert second == first
    assert mock_client_adapter.start_workflow.await_count == 1
    rows = asyncio.run(_canonical_by_idempotency(service_session_factory, identity_key))
    assert len(rows) == 1


def test_signed_fixture_traverses_ingress_to_real_service(
    service_session_factory, mock_client_adapter
):
    """Signed ingress -> receipt -> real admission -> real dispatch ref."""
    import asyncio

    maker = service_session_factory

    async def _session_override():
        async with maker() as session:
            yield session

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
    # NOTE: no dispatcher override — the production
    # TemporalEventExecutionDispatcher runs with the patched service factory.
    client = TestClient(app, raise_server_exceptions=False)

    raw = json.dumps(_payload()).encode("utf-8")
    response = client.post(
        "/api/v1/github/events",
        content=raw,
        headers={
            "X-Hub-Signature-256": "sha256="
            + hmac.new(_SECRET, raw, hashlib.sha256).hexdigest(),
            "X-GitHub-Delivery": "del-real",
            "X-GitHub-Event": "issues",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["decision"] == "admitted_dispatched"
    assert body["executionRef"].startswith("mm:"), body
    assert body["presetSlug"] == "github-issue-search-and-implement"
    assert mock_client_adapter.start_workflow.await_count == 1

    async def _receipts():
        async with maker() as session:
            result = await session.execute(select(GitHubEventDeliveryReceipt))
            return list(result.scalars().all())

    rows = asyncio.run(_receipts())
    assert len(rows) == 1
    row = rows[0]
    assert row.decision == "admitted_dispatched"
    assert row.execution_ref.startswith("mm:")
    assert row.payload_digest == hashlib.sha256(raw).hexdigest()
