"""Integration-style contract coverage for MM-681 runtime inheritance surfaces."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers.executions import (
    _build_original_task_input_snapshot_payload,
    _serialize_execution,
)
from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalWorkflowType,
)
from moonmind.agents.codex_worker.worker import (
    ClaimedJob,
    CodexWorker,
    CodexWorkerConfig,
)
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.unit.agents.codex_worker.test_worker import (
    FakeHandler,
    FakeQueueClient,
    WorkerExecutionResult,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest.fixture(autouse=True)
def _stub_skill_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StubSelection:
        def __init__(self, skills):
            self.skills = skills

    class _StubWorkspace:
        def to_payload(self):
            return {}

    def _fake_resolve(*, run_id, context):
        return _StubSelection(context.get("skill_selection", []))

    def _fake_materialize(*, selection, run_root, cache_root, verify_signatures=False):
        return _StubWorkspace()

    monkeypatch.setattr(
        "moonmind.agents.codex_worker.worker.resolve_run_skill_selection",
        _fake_resolve,
    )
    monkeypatch.setattr(
        "moonmind.agents.codex_worker.worker.materialize_run_skill_workspace",
        _fake_materialize,
    )


def _inherited_runtime_params() -> dict[str, object]:
    return {
        "repository": "MoonLadderStudios/MoonMind",
        "targetRuntime": "codex_cli",
        "model": "gpt-5.4",
        "effort": "high",
        "task": {
            "instructions": "Resolve child work.",
            "runtime": {
                "mode": "codex_cli",
                "model": "gpt-5.4",
                "effort": "high",
                "executionProfileRef": "codex_default",
            },
            "publish": {"mode": "none"},
        },
    }


def test_child_execution_detail_exposes_inherited_runtime_metadata() -> None:
    record = SimpleNamespace(
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={"title": "Child", "summary": "Created"},
        owner_id="user-1",
        entry="run",
        workflow_type=TemporalWorkflowType.RUN,
        state=MoonMindWorkflowState.EXECUTING,
        workflow_id="mm:child-runtime",
        namespace="moonmind",
        run_id="run-child",
        artifact_refs=[],
        created_at="2026-05-16T00:00:00Z",
        started_at="2026-05-16T00:00:00Z",
        updated_at="2026-05-16T00:00:00Z",
        closed_at=None,
        integration_state=None,
        parameters=_inherited_runtime_params(),
        paused=False,
        waiting_reason=None,
        attention_required=False,
    )

    detail = _serialize_execution(record).model_dump(by_alias=True)

    assert detail["targetRuntime"] == "codex_cli"
    assert detail["model"] == "gpt-5.4"
    assert detail["effort"] == "high"
    assert detail["profileId"] == "codex_default"
    assert detail["inputParameters"]["task"]["runtime"] == {
        "mode": "codex_cli",
        "model": "gpt-5.4",
        "effort": "high",
        "executionProfileRef": "codex_default",
    }


@pytest.mark.asyncio
async def test_task_context_contains_inherited_execution_profile_ref(
    tmp_path: Path,
) -> None:
    job = ClaimedJob(
        id=uuid4(),
        type="task",
        payload=_inherited_runtime_params(),
    )
    queue = FakeQueueClient(jobs=[job])
    worker = CodexWorker(
        config=CodexWorkerConfig(
            moonmind_url="http://localhost:8000",
            worker_id="worker-1",
            worker_token=None,
            poll_interval_ms=1500,
            lease_seconds=120,
            workdir=tmp_path,
        ),
        queue_client=queue,
        codex_exec_handler=FakeHandler(
            WorkerExecutionResult(succeeded=True, summary="done", error_message=None)
        ),
    )  # type: ignore[arg-type]

    assert await worker.run_once() is True

    task_context_path = tmp_path / str(job.id) / "artifacts" / "task_context.json"
    task_context = json.loads(task_context_path.read_text(encoding="utf-8"))
    runtime_config = task_context["runtimeConfig"]
    assert runtime_config == {
        "mode": "codex_cli",
        "model": "gpt-5.4",
        "effort": "high",
        "providerProfile": "codex_default",
        "profileId": "codex_default",
        "executionProfileRef": "codex_default",
    }


@pytest.mark.asyncio
async def test_idempotent_resubmit_preserves_original_inherited_runtime(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/runtime.db",
        future=True,
    )
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with maker() as session:
            service = TemporalExecutionService(session, client_adapter=AsyncMock())
            owner_id = str(uuid4())
            first = await service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=owner_id,
                title="Child runtime",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters=_inherited_runtime_params(),
                idempotency_key="mm-681-child",
                repository="MoonLadderStudios/MoonMind",
                summary="Created once",
            )
            second = await service.create_execution(
                workflow_type="MoonMind.Run",
                owner_id=owner_id,
                title="Child runtime changed",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    **_inherited_runtime_params(),
                    "targetRuntime": "gemini_cli",
                    "model": "gemini-2.0",
                },
                idempotency_key="mm-681-child",
                repository="MoonLadderStudios/MoonMind",
                summary="Duplicate",
            )

        assert second.workflow_id == first.workflow_id
        assert second.parameters["targetRuntime"] == "codex_cli"
        assert second.parameters["model"] == "gpt-5.4"
        assert second.parameters["task"]["runtime"]["executionProfileRef"] == (
            "codex_default"
        )
    finally:
        await engine.dispose()


def test_original_task_input_snapshot_contains_inherited_effective_runtime() -> None:
    parameters = _inherited_runtime_params()

    snapshot = _build_original_task_input_snapshot_payload(
        source_kind="create",
        payload={
            "repository": parameters["repository"],
            "targetRuntime": parameters["targetRuntime"],
            "requiredCapabilities": ["gh"],
        },
        task_payload=parameters["task"],
    )

    draft = snapshot["draft"]
    assert draft["targetRuntime"] == "codex_cli"
    assert draft["task"]["runtime"] == {
        "mode": "codex_cli",
        "model": "gpt-5.4",
        "effort": "high",
        "executionProfileRef": "codex_default",
    }
    assert draft["authoredTaskInput"]["runtime"] == draft["task"]["runtime"]
