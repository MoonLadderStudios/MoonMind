"""Saved execution defaults through the real settings persistence boundary."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers.executions import _workflow_creation_defaults
from api_service.db.models import Base, ManagedAgentProviderProfile, SettingsOverride


@pytest.mark.asyncio
@pytest.mark.parametrize("principal_kind", ["system", "uuid_string", "uuid"])
async def test_saved_profile_default_normalizes_settings_subjects(
    tmp_path, principal_kind
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/settings.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    workspace_id, user_id = uuid4(), uuid4()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            for profile_id in ("workspace-default", "user-default"):
                session.add(
                    ManagedAgentProviderProfile(
                        profile_id=profile_id,
                        runtime_id="codex_cli",
                        provider_id="openai",
                        enabled=True,
                    )
                )
            session.add_all(
                [
                    SettingsOverride(
                        scope="workspace",
                        workspace_id=workspace_id,
                        key="workflow.default_runtime",
                        value_json="claude_code",
                    ),
                    SettingsOverride(
                        scope="workspace",
                        workspace_id=workspace_id,
                        key="workflow.default_provider_profile_ref",
                        value_json="workspace-default",
                    ),
                    SettingsOverride(
                        scope="user",
                        workspace_id=workspace_id,
                        user_id=user_id,
                        key="workflow.default_provider_profile_ref",
                        value_json="user-default",
                    ),
                ]
            )
            await session.commit()
            user = SimpleNamespace(
                id=(
                    "system"
                    if principal_kind == "system"
                    else str(user_id) if principal_kind == "uuid_string" else user_id
                ),
                workspace_id=(
                    str(workspace_id)
                    if principal_kind == "uuid_string"
                    else workspace_id
                ),
            )
            selected = await _workflow_creation_defaults(
                session=session,
                user=user,
                payloads=({},),
                task_payload={},
                parameter_payload={},
            )
            assert selected.provider_profile_ref == (
                "workspace-default" if principal_kind == "system" else "user-default"
            )
            assert selected.default_runtime == "claude_code"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["api", "cli", "recurring"])
@pytest.mark.parametrize("profile_state", ["deleted", "disabled", "unready"])
async def test_saved_profile_default_rejects_unavailable_account_before_effects(
    tmp_path, monkeypatch, entrypoint, profile_state
):
    """A saved account never becomes permission to launch another ready account."""
    from unittest.mock import AsyncMock

    from fastapi import FastAPI, HTTPException
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import func, select

    from api_service.api.routers.executions import _get_service, router
    from api_service.db.base import get_async_session
    from api_service.db.models import (
        OmnigentAgentProfile,
        OmnigentAgentProfileUsage,
        OmnigentAgentProfileVersion,
        ProviderCredentialSource,
        ProviderProfileAuthState,
        RecurringWorkflowDefinition,
        RuntimeMaterializationMode,
        TemporalArtifact,
        TemporalExecutionCanonicalRecord,
    )
    from api_service.services.omnigent_agent_profile_selection import (
        resolve_default_agent_profile_snapshot,
    )
    from moonmind.config.settings import settings
    from moonmind.workflow_cli import build_execution_payload
    from moonmind.workflows.temporal.service import TemporalExecutionService
    from tests.unit.api.routers.test_executions import _override_user_dependencies

    monkeypatch.setattr(settings.workflow, "default_runtime", "omnigent")
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "artifacts")
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/admission.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("api_service.db.base.async_session_maker", session_factory)
    # Stop at the first effect if admission incorrectly accepts another account.
    persist_artifact = AsyncMock(
        side_effect=HTTPException(
            418, "Unexpected artifact persistence after admission"
        )
    )
    compile_plan = AsyncMock()
    start_workflow, create_schedule = AsyncMock(), AsyncMock()
    monkeypatch.setattr(
        "api_service.services.omnigent_execution_plan_service.persist_json_artifact",
        persist_artifact,
    )
    monkeypatch.setattr(
        "api_service.services.omnigent_execution_plan_service.compile_and_persist_execution_plan",
        compile_plan,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter.start_workflow",
        start_workflow,
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.TemporalClientAdapter.create_schedule",
        create_schedule,
    )
    try:
        async with session_factory() as session:
            for profile_id in ("saved-account-a", "alternative-account-b"):
                session.add(
                    ManagedAgentProviderProfile(
                        profile_id=profile_id,
                        runtime_id="codex_cli",
                        provider_id="openai",
                        enabled=True,
                        is_default=profile_id == "alternative-account-b",
                        credential_source=ProviderCredentialSource.OAUTH_VOLUME,
                        runtime_materialization_mode=RuntimeMaterializationMode.OAUTH_HOME,
                        auth_state=ProviderProfileAuthState.CONNECTED,
                        volume_ref="test-codex-oauth",
                        volume_mount_path="/root/.codex",
                        max_parallel_runs=1,
                        cooldown_after_429_seconds=0,
                        command_behavior={"auth_readiness": {"launch_ready": True}},
                        clear_env_keys=[
                            "OPENAI_API_KEY",
                            "OPENAI_BASE_URL",
                            "OPENAI_ORG_ID",
                            "OPENAI_PROJECT",
                            "MINIMAX_API_KEY",
                        ],
                    )
                )
            session.add_all(
                [
                    SettingsOverride(
                        scope="workspace",
                        key="workflow.default_provider_profile_ref",
                        value_json="saved-account-a",
                    ),
                    OmnigentAgentProfile(
                        profile_id="codex-configuration",
                        display_name="Codex configuration",
                        state="active",
                        active_version=1,
                        default_for_runtime=True,
                    ),
                    OmnigentAgentProfileVersion(
                        profile_id="codex-configuration",
                        version=1,
                        digest="sha256:" + "a" * 64,
                        document={
                            "endpointRef": "default",
                            "bridgeMode": "proxy",
                            "source": {
                                "bundleArtifactRef": "artifact://test-bundle",
                                "bundleDigest": "sha256:" + "b" * 64,
                            },
                            "harness": "codex-native",
                            "execution": {
                                "defaultExecutionProfileRef": "omnigent-codex@1",
                                "allowedLaunchPolicyRefs": ["codex-on-demand@1"],
                            },
                            "providerRequirements": {
                                "runtimeId": "codex_cli",
                                "credentialSource": "oauth_volume",
                                "materializationMode": "oauth_home",
                                "providerIds": ["openai"],
                            },
                            "policyRef": "default@1",
                        },
                        validation_result={"ready": True},
                        rollout_metadata={
                            "bundleImport": {
                                "upstreamAgent": {"id": "test-codex-agent"}
                            }
                        },
                    ),
                ]
            )
            await session.commit()
            saved = await session.get(ManagedAgentProviderProfile, "saved-account-a")
            if profile_state == "deleted":
                await session.delete(saved)
            elif profile_state == "disabled":
                saved.enabled = False
            else:
                saved.auth_state = ProviderProfileAuthState.NOT_CONFIGURED
            await session.commit()

            # Prove B is eligible through the real default-selection owner.
            async with session_factory() as probe_session:
                snapshot = await resolve_default_agent_profile_snapshot(
                    probe_session,
                    provider_profile_ref=None,
                    launch_policy_ref=None,
                    consumer_type="workflow",
                    consumer_id="eligibility-probe",
                    user=None,
                )
                assert snapshot["providerProfileRef"] == "alternative-account-b"
                await probe_session.rollback()

            app = FastAPI()
            app.include_router(router)
            service = TemporalExecutionService(session)
            app.dependency_overrides[_get_service] = lambda: service
            app.dependency_overrides[get_async_session] = lambda: session
            _override_user_dependencies(app, is_superuser=True)
            body = (
                build_execution_payload(instructions="Use the saved account.")
                if entrypoint == "cli"
                else {
                    "type": "workflow",
                    "payload": {"workflow": {"instructions": "Use the saved account."}},
                }
            )
            if entrypoint == "recurring":
                body["payload"]["schedule"] = {"mode": "recurring", "cron": "0 * * * *"}
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/api/executions", json=body)

            usages = list(
                (await session.scalars(select(OmnigentAgentProfileUsage))).all()
            )
            assert response.status_code == 422, (
                response.text,
                [usage.effective_snapshot["providerProfileRef"] for usage in usages],
            )
            detail = response.json()["detail"]
            if profile_state == "deleted":
                assert detail["code"] == "invalid_execution_request"
                assert (
                    "Provider profile not found: 'saved-account-a'" in detail["message"]
                )
            else:
                assert detail["code"] == "invalid_execution_request"
                assert (
                    detail["message"] == "selected provider profile is not launch-ready"
                )
            persist_artifact.assert_not_awaited()
            compile_plan.assert_not_awaited()
            start_workflow.assert_not_awaited()
            create_schedule.assert_not_awaited()
            assert usages == []
            for model in (
                TemporalArtifact,
                TemporalExecutionCanonicalRecord,
                RecurringWorkflowDefinition,
            ):
                assert (
                    await session.scalar(select(func.count()).select_from(model)) == 0
                )
    finally:
        await engine.dispose()
