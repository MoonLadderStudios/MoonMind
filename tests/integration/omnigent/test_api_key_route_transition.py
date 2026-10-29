"""Queued API-key work keeps the route recorded at admission across an upgrade.

An upgrade that seeds the managed API-key configurations moves new admissions
of a translatable API-key Codex or Claude Provider Profile onto the shared
Omnigent lifecycle. A run admitted before that upgrade recorded its direct
runtime in its plan-node inputs; every later dispatch of that step (first
launch from the queue, runtime recovery and failed-step recovery) rebuilds its
request from those recorded inputs and never asks the Profile route again.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services import profile_execution_selection
from api_service.services.omnigent_agent_bootstrap_service import (
    reconcile_bootstrap_agent_profile,
    reconcile_builtin_claude_agent_profile,
)
from api_service.services.omnigent_agent_profile_service import (
    synchronize_upstream_inventory,
)
from api_service.services.omnigent_policies import seed_bootstrap_policies
from api_service.services.profile_execution_selection import profile_route_runtime
from moonmind.workflows.temporal.runtime.strategies import RUNTIME_STRATEGIES
from moonmind.workflows.temporal.workflows import run as run_workflow_module

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

_SHARED_IMAGE = "ghcr.io/moonladderstudios/omnigent-host-moonmind@sha256:" + "3" * 64


@pytest_asyncio.fixture()
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/transition.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
    await engine.dispose()


def _api_key_profile(runtime_id: str) -> SimpleNamespace:
    key_env, role, provider_id = {
        "claude_code": ("ANTHROPIC_API_KEY", "anthropic_api_key", "anthropic"),
        "codex_cli": ("OPENAI_API_KEY", "openai_api_key", "openai"),
    }[runtime_id]
    return SimpleNamespace(
        profile_id=f"{runtime_id}_api",
        runtime_id=runtime_id,
        provider_id=provider_id,
        credential_source="secret_ref",
        runtime_materialization_mode="api_key_env",
        execution_configuration=None,
        secret_refs={role: f"env://{key_env}"},
        env_template={key_env: {"from_secret_ref": role}},
        file_templates=None,
        home_path_overrides=None,
    )


async def _upgrade(session, runtime_id: str, monkeypatch) -> None:
    """Run the startup reconciliation that seeds the managed configuration."""

    if runtime_id == "codex_cli":
        inventory = [
            {
                "id": "codex-native-ui",
                "name": "codex-native-ui",
                "harness": "codex-native",
                "capabilities": ["session.start"],
            }
        ]
        assert await reconcile_bootstrap_agent_profile(
            session, env={}, inventory=inventory
        )
        return
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    monkeypatch.setenv("MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED", "true")

    async def resolver(_image_ref: str) -> str:
        return _SHARED_IMAGE

    await seed_bootstrap_policies(
        session,
        env={
            "MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED": "true",
            "OMNIGENT_SHARED_HOST_IMAGE_REF": _SHARED_IMAGE,
        },
        image_resolver=resolver,
    )
    inventory = [
        {
            "id": "ag_claude",
            "name": "claude-native-ui",
            "version": "2",
            "harness": "claude-native",
            "capabilities": ["session.start"],
        }
    ]
    await synchronize_upstream_inventory(
        session, endpoint_ref="default", bridge_mode="proxy", inventory=inventory
    )
    assert await reconcile_builtin_claude_agent_profile(session, inventory=inventory)


def _workflow(monkeypatch) -> run_workflow_module.MoonMindRunWorkflow:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _id: True)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "info",
        lambda: SimpleNamespace(
            task_queue="mm.workflow.user.v2",
            workflow_id="mm:queued-before-upgrade",
            run_id="queued-run",
            namespace="default",
            search_attributes={},
        ),
    )
    parent = run_workflow_module.MoonMindRunWorkflow()
    parent._owner_id = "user-1"
    return parent


@pytest.mark.parametrize("runtime_id", ["claude_code", "codex_cli"])
async def test_queued_api_key_run_keeps_its_recorded_direct_route(
    session, monkeypatch, runtime_id
):
    profile = _api_key_profile(runtime_id)

    # Admission before the upgrade: no managed API-key configuration accepts
    # the Profile, so the route recorded into the run's step is direct.
    assert await profile_route_runtime(session, None, profile) == runtime_id
    recorded_inputs = {
        "targetRuntime": runtime_id,
        "profileId": profile.profile_id,
        "instructions": "Generate a report.",
    }

    await _upgrade(session, runtime_id, monkeypatch)
    # New work for the same Profile is admitted through the shared lifecycle.
    assert await profile_route_runtime(session, None, profile) == "omnigent"

    async def route_must_not_be_reread(*_args, **_kwargs):
        pytest.fail("a queued run's dispatch must not re-route its Profile")

    monkeypatch.setattr(
        profile_execution_selection, "profile_route_runtime", route_must_not_be_reread
    )
    parent = _workflow(monkeypatch)
    dispatches = [
        (1, "initial_execution"),
        (2, "runtime_recovered"),
        (3, "recover_from_failed_step"),
    ]
    requests = [
        parent._build_agent_execution_request(
            node_inputs=dict(recorded_inputs),
            node_id="step-1",
            tool_name=runtime_id,
            workflow_parameters={},
            step_execution=step_execution,
            attempt_reason=reason,
        )
        for step_execution, reason in dispatches
    ]

    for request in requests:
        assert request.agent_kind == "managed"
        assert request.agent_id == runtime_id
        assert request.execution_profile_ref == profile.profile_id
    # The direct strategy that serves the recorded route is still registered.
    assert runtime_id in RUNTIME_STRATEGIES
