"""Operator child admission through persisted executions and real Temporal RPCs."""

from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.testing import WorkflowEnvironment

from api_service.api.execution_fanout import resolve_execution_request_authority
from api_service.api.routers import executions
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    TemporalExecutionCanonicalRecord,
)
from api_service.services.single_user_conversion import run_guarded_upgrade
from moonmind.config.settings import settings
from moonmind.security.execution_fanout_capabilities import (
    mint_execution_fanout_capability,
    verify_execution_fanout_capability,
)
from moonmind.workflows.executions.runtime_inheritance import (
    SCOPE_CREATE_CHILD,
    SCOPE_INHERIT_RUNTIME,
    RuntimeInheritanceError,
    resolve_child_runtime_inheritance,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.helpers.temporal_visibility import register_deployment_search_attributes

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.parametrize("owner_type", ["system", "user"], ids=["new", "retained"])
async def test_operator_creates_child_from_parent_without_identity_headers(
    tmp_path, monkeypatch, owner_type
):
    for name in (
        "MOONMIND_PUBLIC_BASE_URL",
        "MOONMIND_API_PUBLISH_HOST",
        "MOONMIND_TRUSTED_INGRESS",
        "MOONMIND_TRUSTED_PROXIES",
        "MOONMIND_DEPLOYMENT_PROJECT_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/executions.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with (
            async_sessionmaker(engine, expire_on_commit=False)() as session,
            await WorkflowEnvironment.start_local() as env,
        ):
            await register_deployment_search_attributes(env)
            conversion = await run_guarded_upgrade(session, operator_authorized=True)
            assert conversion.published
            profile = ManagedAgentProviderProfile(
                profile_id=f"provider-{uuid4().hex}",
                runtime_id="codex_cli",
                provider_id="openai",
                enabled=True,
                default_model="gpt-5.4",
                default_effort="high",
            )
            session.add(profile)
            await session.commit()
            adapter = TemporalClientAdapter(env.client)
            service = TemporalExecutionService(
                session, client_adapter=adapter, namespace=env.client.namespace
            )
            retained_owner = str(uuid4()) if owner_type == "user" else None
            runtime = {
                "mode": profile.runtime_id,
                "model": profile.default_model,
                "effort": profile.default_effort,
                "executionProfileRef": profile.profile_id,
            }

            async def create_parent():
                return await service.create_execution(
                    workflow_type="MoonMind.UserWorkflow",
                    owner_id=retained_owner,
                    owner_type=owner_type,
                    title="Runtime inheritance source",
                    input_artifact_ref=None,
                    plan_artifact_ref=None,
                    manifest_artifact_ref=None,
                    failure_policy=None,
                    initial_parameters={
                        "targetRuntime": profile.runtime_id,
                        "profileId": profile.profile_id,
                        "model": profile.default_model,
                        "effort": profile.default_effort,
                        "workflow": {
                            "instructions": "Retain this admitted runtime selection.",
                            "runtime": runtime,
                            "publish": {"mode": "none"},
                        },
                    },
                    idempotency_key=uuid4().hex,
                )

            parent = await create_parent()
            other_parent = await create_parent()
            # A start RPC failure must not turn a database-only row into proof.
            parent_description = await adapter.describe_workflow(parent.workflow_id)
            assert parent_description.run_id == parent.run_id
            original_owner = (parent.owner_type, parent.owner_id)

            app = FastAPI()
            app.include_router(executions.router)

            async def session_dependency():
                yield session

            app.dependency_overrides[get_async_session] = session_dependency
            app.dependency_overrides[executions._get_service] = lambda: service
            app.dependency_overrides[executions.get_temporal_client] = (
                lambda: env.client
            )
            artifact_store = LocalTemporalArtifactStore(tmp_path / "artifacts")
            monkeypatch.setattr(
                executions,
                "get_temporal_artifact_service",
                lambda db: TemporalArtifactService(
                    TemporalArtifactRepository(db), store=artifact_store
                ),
            )
            transport = httpx.ASGITransport(app, client=("127.0.0.1", 43210))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                payload = {
                    "type": "workflow",
                    "payload": {
                        "runtimeInheritance": "parent",
                        "parentWorkflowId": parent.workflow_id,
                        "workflow": {
                            "instructions": "Use the parent's admitted selection.",
                            "publish": {"mode": "none"},
                        },
                    },
                }
                response = await client.post("/api/executions", json=payload)
                assert response.status_code == 201, response.text
                child_id = response.json()["workflowId"]
                child = await session.get(TemporalExecutionCanonicalRecord, child_id)
                assert child is not None
                assert child.owner_type.value == "system"
                assert child.parameters["parentWorkflowId"] == parent.workflow_id
                assert child.parameters["targetRuntime"] == profile.runtime_id
                assert child.parameters["profileId"] == profile.profile_id
                for key, value in runtime.items():
                    assert child.parameters["workflow"]["runtime"][key] == value
                assert "executionPrincipal" not in child.parameters
                child_description = await adapter.describe_workflow(child_id)
                assert child_description.run_id == child.run_id

                payload["payload"]["parentWorkflowId"] = f"mm:{uuid4()}"
                response = await client.post("/api/executions", json=payload)
                assert response.status_code == 422, response.text
                assert response.json()["detail"]["code"] == "parent_execution_not_found"

            # A machine with the same retained owner still cannot select a
            # different parent, or inherit with only one of the two scopes.
            token = mint_execution_fanout_capability(
                secret=str(settings.security.JWT_SECRET_KEY),
                parent_workflow_id=parent.workflow_id,
                agent_run_id=uuid4().hex,
                step_id=uuid4().hex,
                session_id=uuid4().hex,
                runtime_id=profile.runtime_id,
                source_kind="omnigent",
                lifetime_seconds=300,
            )
            authority = await resolve_execution_request_authority(
                user=None,
                service=service,
                capability=verify_execution_fanout_capability(
                    token, secret=str(settings.security.JWT_SECRET_KEY)
                ),
            )
            principal = authority.principal
            assert principal is not None
            assert principal.is_instance_operator is False
            inherited = await resolve_child_runtime_inheritance(
                request_payload={"runtimeInheritance": "caller"},
                task_payload={},
                principal=principal,
                service=service,
            )
            assert inherited is not None
            assert inherited.source_workflow_id == parent.workflow_id
            assert inherited.profile_id == profile.profile_id
            assert inherited.model == profile.default_model
            for selected_parent, scopes in (
                (other_parent.workflow_id, principal.scopes),
                (parent.workflow_id, frozenset({SCOPE_CREATE_CHILD})),
                (parent.workflow_id, frozenset({SCOPE_INHERIT_RUNTIME})),
            ):
                with pytest.raises(RuntimeInheritanceError) as caught:
                    await resolve_child_runtime_inheritance(
                        request_payload={
                            "runtimeInheritance": "parent",
                            "parentWorkflowId": selected_parent,
                        },
                        task_payload={},
                        principal=replace(principal, scopes=scopes),
                        service=service,
                    )
                assert caught.value.code == "runtime_inheritance_forbidden"
            await session.refresh(parent)
            assert (parent.owner_type, parent.owner_id) == original_owner
    finally:
        await engine.dispose()
