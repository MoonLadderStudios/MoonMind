"""Hermetic normal-interface Omnigent product-path acceptance journey.

This intentionally starts with the payload authored by ``/workflows/new`` and
crosses the canonical create normalization and Temporal request compiler before
using the durable bridge store.  Only the provider/host transport is absent;
those external boundaries have their own controlled-fake conformance suites.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.execution_profiles import (
    POLICIES,
    PROFILES,
    compile_effective_launch,
)
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.schemas.workspace_locator_models import (
    SandboxWorkspaceLocator,
    WorkspaceLocatorResolutionError,
)
from moonmind.workflows.executions.execution_contract import build_canonical_workflow_view
from moonmind.workflows.temporal.runtime.workspace_locators import (
    resolve_sandbox_workspace_locator,
)
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

# The rollout gate deliberately composes the production-owner fixtures rather
# than replacing bridge and executor behavior with journey-local fakes.
pytest_plugins = (
    "tests.integration.omnigent.test_bridge_conformance",
    "tests.integration.omnigent.test_execute_fake_server",
)


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci, pytest.mark.asyncio]


def _browser_payload() -> dict[str, object]:
    """Exact authority shape emitted by the Create Workflow page."""

    return {
        "repository": "MoonLadderStudios/MoonMind",
        "targetRuntime": "omnigent",
        "omnigent": {
            "executionTargetRef": "omnigent-codex@1",
            "launchPolicyRef": "codex-on-demand@1",
        },
        "task": {
            "instructions": "Make the bounded deterministic change.",
            "git": {"branch": "main"},
            "runtime": {"mode": "omnigent", "profileId": "oauth-1"},
        },
    }


@pytest.mark.asyncio
async def test_browser_payload_compiles_and_replays_durable_projection(
    tmp_path,
) -> None:
    authored = _browser_payload()
    canonical = build_canonical_workflow_view(job_type="task", payload=authored)

    # The create boundary persists authored intent; it does not mint host,
    # credential, mount, image, or lease authority on behalf of the browser.
    assert canonical["targetRuntime"] == "omnigent"
    assert canonical["omnigent"] == authored["omnigent"]
    assert canonical["workflow"]["runtime"]["mode"] == "omnigent"
    assert canonical["workflow"]["runtime"]["providerProfile"] == "oauth-1"
    serialized = json.dumps(canonical)
    assert all(
        forbidden not in serialized
        for forbidden in ("hostId", "leaseId", "registrationToken")
    )

    # Reload the persisted Create payload and send it through the production
    # runtime planner. This is the API-to-Temporal handoff that owns node inputs.
    persisted = json.loads(json.dumps(canonical))
    plan = _build_runtime_planner()(
        inputs=persisted,
        parameters=persisted,
        snapshot=SimpleNamespace(
            digest="registry-snapshot:test",
            artifact_ref="artifact://registry-snapshot/test",
        ),
    )
    node_inputs = plan["nodes"][0]["inputs"]

    compiler = MoonMindRunWorkflow()
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=SimpleNamespace(
            workflow_id="mm:browser-product-path",
            run_id="run-1",
            namespace="default",
        ),
    ):
        request = compiler._build_agent_execution_request(
            node_inputs=node_inputs,
            node_id="implement",
            tool_name="omnigent",
            workflow_parameters=canonical,
            step_execution=1,
        )

    assert request.agent_kind == "external"
    assert request.agent_id == "omnigent"
    assert request.execution_profile_ref == "oauth-1"
    assert request.parameters["omnigent"] == authored["omnigent"]
    assert request.workspace_spec["repository"] == authored["repository"]
    assert request.workspace_spec["branch"] == "main"

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = OmnigentBridgeSessionStore(sessions)
    assert request.step_execution is not None
    row = await store.get_or_create(
        request=request,
        endpoint_ref="controlled-fake",
        agent_id="codex",
        agent_name="Codex",
        target_metadata={"hostType": "on_demand_docker"},
    )
    same_row = await store.get_or_create(
        request=request,
        endpoint_ref="controlled-fake",
        agent_id="codex",
        agent_name="Codex",
        target_metadata={"hostType": "on_demand_docker"},
    )
    assert (
        same_row.bridge_session_id == row.bridge_session_id
    )  # retry/worker restart keeps one session

    # Workflow Detail reload resolves the durable projection.
    replay = await store.resolve_projection_session(
        step_execution_id=request.step_execution.step_execution_id
    )
    assert replay is not None
    assert replay.bridge_session_id == row.bridge_session_id
    assert replay.omnigent_endpoint_ref == "controlled-fake"
    await engine.dispose()


def test_product_path_launch_selection_fails_closed_at_catalog_owner(
    monkeypatch,
) -> None:
    """Disabled/incompatible refs never widen to another profile or policy."""

    selected_profile = next(
        profile for profile in PROFILES.values() if profile.provider_runtime == "codex_cli"
    )
    disabled = selected_profile.model_copy(update={"enabled": False})
    monkeypatch.setitem(PROFILES, disabled.ref, disabled)
    with pytest.raises(OmnigentOAuthHostError) as captured:
        compile_effective_launch(
            profile_ref=disabled.ref,
            policy_ref=disabled.default_policy_ref,
            provider_profile_id="oauth-1",
        )
    assert captured.value.code == "OMNIGENT_EXECUTION_PROFILE_UNAVAILABLE"

    enabled = disabled.model_copy(update={"enabled": True})
    monkeypatch.setitem(PROFILES, enabled.ref, enabled)
    incompatible = next(
        policy
        for policy in POLICIES.values()
        if not policy.policy_id.startswith("codex-")
    )
    with pytest.raises(OmnigentOAuthHostError) as captured:
        compile_effective_launch(
            profile_ref=enabled.ref,
            policy_ref=incompatible.ref,
            provider_profile_id="oauth-1",
        )
    assert captured.value.code == "OMNIGENT_LAUNCH_POLICY_PROVIDER_MISMATCH"


def test_product_path_workspace_owner_rejects_invalid_and_escaped_locators(
    tmp_path,
) -> None:
    """The worker owner rejects both malformed and filesystem-escaped authority."""

    with pytest.raises(ValueError, match="without traversal"):
        SandboxWorkspaceLocator(
            workspaceId="browser-product-path", relativePath="../repo"
        )

    authority = tmp_path / "temporal_sandbox" / "browser-product-path"
    authority.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (authority / "repo").symlink_to(outside, target_is_directory=True)
    locator = SandboxWorkspaceLocator(
        workspaceId="browser-product-path", relativePath="repo"
    )
    with pytest.raises(
        WorkspaceLocatorResolutionError, match="escapes its workspace"
    ):
        resolve_sandbox_workspace_locator(
            locator,
            workspace_root=tmp_path,
            expected_workspace_id="browser-product-path",
        )


async def test_controlling_bridge_failure_and_recovery_owners(
    bridge_harness, monkeypatch, tmp_path
) -> None:
    """Execute the production owners previously represented by catalog strings."""

    from tests.integration.omnigent import test_bridge_conformance as bridge

    await bridge.test_scenario_03_stream_disconnect_and_snapshot_reconciliation(
        bridge_harness
    )
    await bridge.test_scenario_07_optional_diff_unavailable(bridge_harness)
    await bridge.test_scenario_09_cancellation_via_interrupt_and_stop_session(
        bridge_harness
    )
    await bridge.test_real_store_api_page_and_sse_project_gap_cursor_terminal_and_redaction(
        bridge_harness, monkeypatch, tmp_path
    )


@pytest.mark.parametrize(
    ("boundary", "expected_posts"),
    [("not_prepared", 1), ("prepared", 1), ("posting", 0), ("posted", 0), ("terminal", 0)],
)
async def test_controlling_restart_owner(
    bridge_harness, boundary: str, expected_posts: int
) -> None:
    """Recreate the supported proxy at every durable first-message boundary."""

    from tests.integration.omnigent import test_bridge_conformance as bridge

    await bridge.test_each_first_message_restart_boundary_is_exactly_once(
        bridge_harness, boundary, expected_posts
    )


async def test_controlling_first_message_reconciliation_owner(
    bridge_harness,
) -> None:
    """Reconcile accepted messages once and reject a changed durable digest."""

    from tests.integration.omnigent import test_bridge_conformance as bridge

    await bridge.test_first_message_crash_matrix_reconciles_response_before_persist_once(
        bridge_harness
    )
    await bridge.test_partial_post_before_ack_is_ambiguous_and_never_reposted(
        bridge_harness
    )


@pytest.mark.parametrize("fake_omnigent_server", [True], indirect=True)
async def test_controlling_required_artifact_failure_owner(
    fake_omnigent_server, monkeypatch, tmp_path
) -> None:
    """Execute required-artifact failure through the production executor."""

    from tests.integration.omnigent import test_execute_fake_server as execute

    await execute.test_omnigent_execute_required_artifact_persistence_failure_is_terminal(
        fake_omnigent_server, monkeypatch, tmp_path
    )
