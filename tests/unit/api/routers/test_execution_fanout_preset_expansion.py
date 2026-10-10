"""Execution-scoped fan-out through real preset catalog persistence."""

from __future__ import annotations

import json
import runpy
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import executions as executions_module
from api_service.api.routers.executions import _get_service, router
from api_service.db.base import get_async_session
from api_service.db.models import Base, ManagedAgentProviderProfile, PresetRecent
from api_service.services.presets.catalog import PresetCatalogService
from moonmind.config.settings import settings
from moonmind.security.execution_fanout_capabilities import (
    mint_execution_fanout_capability,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.unit.api.routers.test_executions import (
    _build_execution_record,
    _override_temporal_client,
    _override_user_dependencies,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "artifact_field", [None, "planArtifactRef", "inputArtifactRef"]
)
@pytest.mark.parametrize("managed_batch", [False, True])
async def test_system_owned_fanout_expands_existing_pr_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact_field: str | None,
    managed_batch: bool,
) -> None:
    """Replay the failed batch child without treating SYSTEM as a user UUID."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/fanout.db")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    artifact_store = LocalTemporalArtifactStore(tmp_path / "artifacts")
    monkeypatch.setattr(
        executions_module,
        "get_temporal_artifact_service",
        lambda session: TemporalArtifactService(
            TemporalArtifactRepository(session), store=artifact_store
        ),
    )
    artifact_ref = "art:sha256:machine-plan"
    repository = "MoonLadderStudios/Tactics"
    parent_id = "mm:a282ca74-abd3-43aa-83a0-5d0aa386e457"
    parent = SimpleNamespace(
        workflow_id=parent_id,
        owner_id="system",
        owner_type="system",
        parameters={
            "targetRuntime": "codex_cli",
            "model": "gpt-6-astra",
            "effort": "high",
            "profileId": "codex_openai_oauth",
            "workflow": {
                "runtime": {
                    "mode": "codex_cli",
                    "model": "gpt-6-astra",
                    "effort": "high",
                    "executionProfileRef": "codex_openai_oauth",
                }
            },
        },
        memo={},
        search_attributes={},
    )
    service = AsyncMock()
    service.describe_execution.return_value = parent
    service.create_execution.return_value = _build_execution_record(owner_id="system")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_service] = lambda: service
    _override_temporal_client(app)
    _override_user_dependencies(app, is_superuser=False)
    for dependency in tuple(app.dependency_overrides):
        if getattr(dependency, "__name__", "") in {
            "_current_user_fallback",
            "_strict_current_user",
            "_optional_current_user",
        }:
            app.dependency_overrides[dependency] = lambda: None

    async def session_dependency():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_async_session] = session_dependency
    monkeypatch.setattr(settings.workflow, "default_runtime", "codex_cli")
    capability = mint_execution_fanout_capability(
        secret=str(settings.security.JWT_SECRET_KEY),
        parent_workflow_id=parent_id,
        agent_run_id="agent-run-batch",
        step_id="batch-step",
        session_id="batch-session",
        runtime_id="codex_cli",
        source_kind="omnigent",
        lifetime_seconds=300,
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        seed_dir = tmp_path / "presets"
        seed_dir.mkdir()
        source = (
            Path(__file__).resolve().parents[4]
            / "api_service/data/presets/pr-review-resolve.yaml"
        )
        shutil.copy2(source, seed_dir / source.name)
        async with sessions() as session:
            session.add(
                ManagedAgentProviderProfile(
                    profile_id="codex_openai_oauth",
                    runtime_id="codex_cli",
                    provider_id="openai",
                    enabled=True,
                    auth_state="connected",
                    default_model="gpt-5.5",
                    default_effort="medium",
                )
            )
            await PresetCatalogService(session).sync_seed_templates(seed_dir=seed_dir)
            if artifact_field == "inputArtifactRef":
                artifacts = executions_module.get_temporal_artifact_service(session)
                artifact, _ = await artifacts.create(
                    principal="system", content_type="application/json"
                )
                artifact_ref = artifact.artifact_id
                await artifacts.write_complete(
                    artifact_id=artifact_ref,
                    principal="system",
                    content_type="application/json",
                    payload=json.dumps(
                        {
                            "workflow": {
                                "steps": [
                                    {
                                        "type": "tool",
                                        "tool": {
                                            "name": "deployment.update_compose_stack"
                                        },
                                    }
                                ]
                            }
                        }
                    ).encode(),
                )
            await session.commit()

        repository_payload = repository
        if managed_batch:
            # Validate the shipped producer's target through real admission,
            # fan-out authentication and persisted preset expansion.
            helper = runpy.run_path(str(
                Path(__file__).resolve().parents[4]
                / ".agents/skills/batch-pr-resolver/bin/batch_pr_resolver.py"
            ))
            generated = helper["_build_queue_request"](
                repository, 2752, "moonmind-job-47d25995",
                runtime=helper["RuntimeSelection"](),
                merge_method="rebase", max_iterations=7, priority=0, max_attempts=3,
                repository_connection_ref="repository-connection:git-default",
            )
            repository_payload = generated["payload"]["repository"]

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/executions",
                headers={
                    "Authorization": f"Bearer {capability}",
                    "X-MoonMind-Execution-Fanout": "v1",
                },
                json={
                    "type": "task",
                    "payload": {
                        **({artifact_field: artifact_ref} if artifact_field else {}),
                        "repository": repository_payload,
                        "executionPrincipal": {
                            "kind": "operator",
                            "scopes": ["deployment_control", "docker_admin"],
                        },
                        "runtimeInheritance": "caller",
                        "idempotencyKey": "batch-pr-resolver:parent:pr:2752",
                        "task": {
                            "title": "moonmind-job-47d25995",
                            "instructions": "Resolve PR #2752.",
                            "taskTemplate": {"slug": "pr-review-resolve"},
                            "inputs": {
                                "repository": repository,
                                "pull_request": "2752",
                                "review_provider": "none",
                                "finish_with_pr_resolver": True,
                                "merge_method": "rebase",
                                "max_iterations": 7,
                            },
                        },
                    },
                },
            )

        assert response.status_code == 201, response.text
        assert response.json()["workflowId"] == "mm:wf-1"
        service.create_execution.assert_awaited_once()
        creation = service.create_execution.await_args.kwargs
        assert creation["owner_id"] == "system"
        assert creation["owner_type"] == "system"
        if artifact_field:
            argument = (
                "plan_artifact_ref"
                if artifact_field == "planArtifactRef"
                else "input_artifact_ref"
            )
            assert creation[argument] == artifact_ref
        initial = creation["initial_parameters"]
        if managed_batch:
            assert initial["repository"]["branch"] == {"name": "moonmind-job-47d25995"}
            assert initial["repository"]["connectionRef"] == "repository-connection:git-default"
        assert initial["executionPrincipal"] == {
            "kind": "workflow",
            "workflowId": parent_id,
            "scopes": ["executions:create-child", "executions:inherit-runtime"],
        }
        assert initial["parentWorkflowId"] == parent_id
        assert initial["targetRuntime"] == "codex_cli"
        assert initial["profileId"] == "codex_openai_oauth"
        assert initial["model"] == "gpt-6-astra"
        assert initial["effort"] == "high"
        workflow = initial["workflow"]
        assert workflow["runtime"]["executionProfileRef"] == "codex_openai_oauth"
        assert workflow["taskTemplate"]["slug"] == "pr-review-resolve"
        assert workflow["steps"][0]["tool"]["inputs"] == {
            "repository": repository,
            "pullRequest": "2752",
        }
        policy = workflow["publish"]["mergeAutomation"]
        assert policy["finishMode"] == "merge"
        assert policy["mergeMethod"] == "rebase"
        assert int(policy["maxIterations"]) == 7
        assert policy["automatedReview"] == "disabled"
        assert workflow["instructions"] == "Resolve PR #2752."
        async with sessions() as session:
            assert (await session.scalars(select(PresetRecent))).all() == []
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
