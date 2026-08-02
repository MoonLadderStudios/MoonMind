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
from moonmind.omnigent.authority_chain import build_omnigent_authority_chain_evidence
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.policies import compile_policy_snapshot
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.workflows.executions.execution_contract import build_canonical_workflow_view
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.unit.omnigent.test_oauth_profile_lifecycle import (
    _run_coordinator_failure_case,
)
from tests.unit.omnigent.test_policy_authority import policy_document

# The rollout gate deliberately composes the production-owner fixtures rather
# than replacing bridge and embedded-host behavior with journey-local fakes.
pytest_plugins = (
    "tests.integration.omnigent.test_bridge_conformance",
    "tests.integration.omnigent.test_embedded_recovery",
    "tests.integration.omnigent.test_execute_fake_server",
)


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _browser_payload() -> dict[str, object]:
    """Exact authority shape emitted by the Create Workflow page."""

    return {
        "repository": "MoonLadderStudios/MoonMind",
        "targetRuntime": "omnigent",
        "omnigent": {
            "executionTargetRef": "omnigent-codex-default",
            "launchPolicyRef": "on-demand-v1",
        },
        "task": {
            "instructions": "Make the bounded deterministic change.",
            "git": {"branch": "main"},
            "runtime": {"mode": "omnigent", "profileId": "oauth-1"},
        },
    }


@pytest.mark.asyncio
async def test_browser_payload_compiles_replays_and_releases_only_after_cleanup(
    tmp_path,
) -> None:
    authored = _browser_payload()
    canonical = build_canonical_workflow_view(job_type="task", payload=authored)

    # The create boundary persists authored intent; it does not mint host,
    # credential, mount, image, or lease authority on behalf of the browser.
    assert canonical["targetRuntime"] == "omnigent"
    assert canonical["omnigent"] == authored["omnigent"]
    assert canonical["workflow"]["runtime"] == authored["task"]["runtime"]
    serialized = json.dumps(canonical)
    assert all(
        forbidden not in serialized
        for forbidden in ("hostId", "leaseId", "registrationToken")
    )

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
            node_inputs={
                "runtime": {
                    "mode": "omnigent",
                    "profileId": "oauth-1",
                    "workspaceSpec": {
                        "repository": authored["repository"],
                        "branch": "main",
                        "workspaceLocator": {
                            "kind": "sandbox",
                            "workspaceId": "browser-product-path",
                            "relativePath": "repo",
                        },
                    },
                    "omnigent": authored["omnigent"],
                },
                "instructions": canonical["workflow"]["instructions"],
            },
            node_id="implement",
            tool_name="omnigent",
            workflow_parameters=canonical,
            step_execution=1,
        )

    assert request.agent_kind == "external"
    assert request.agent_id == "omnigent"
    assert request.execution_profile_ref == "oauth-1"
    assert request.parameters["omnigent"] == authored["omnigent"]
    assert request.workspace_spec["workspaceLocator"]["workspaceId"] == (
        "browser-product-path"
    )

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
    assert same_row.id == row.id  # retry/worker restart keeps one session

    evidence = build_omnigent_authority_chain_evidence(
        effective_launch={
            "hostMode": "on_demand_docker",
            "executionProfileRef": "omnigent-codex-default",
            "launchPolicyRef": "on-demand-v1",
            "providerProfileId": "oauth-1",
        },
        workspace_resolution={
            "locatorKind": "sandbox",
            "workspaceId": "browser-product-path",
            "relativePath": "repo",
            "identityVerified": True,
        },
        repository=str(authored["repository"]),
        source_branch="main",
        output_branch="agent/implement",
        publish_mode="none",
        profile_authorization={
            "providerProfileId": "oauth-1",
            "providerLeaseRef": "provider-lease-1",
            "hostBindingRef": "host-binding-1",
            "hostLeaseRef": "host-lease-1",
            "bridgeSessionId": str(row.id),
        },
        result_output_refs=["artifact://terminal-output"],
        terminal_status="completed",
        cleanup_mode="on_demand_remove",
        cleanup_completed=True,
        lease_released=True,
        janitor_required=False,
        release_ordering=[
            "artifact_harvest_completed",
            "host_cleanup_completed",
            "provider_lease_released",
            "terminal",
        ],
    )
    terminal = evidence["terminal"]
    assert terminal["cleanupCompleted"] is True
    assert terminal["leaseReleased"] is True
    assert terminal["releaseOrdering"].index("host_cleanup_completed") < (
        terminal["releaseOrdering"].index("provider_lease_released")
    )
    assert evidence["runtime"]["bridgeSessionId"] == str(row.id)

    # Workflow Detail reload resolves the durable projection after host removal.
    replay = await store.resolve_projection_session(
        step_execution_id=request.step_execution.step_execution_id
    )
    assert replay is not None
    assert replay.id == row.id
    assert replay.endpoint_ref == "controlled-fake"
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail_at", "code"),
    [
        ("profile_missing", "profile_resolution_missing"),
        ("profile_readiness", "profile_readiness_failed"),
        ("lease", "profile_lease_timeout"),
        ("binding", "host_binding_mismatch"),
        ("host_lease", "container_allocation_failed"),
        ("image_pull", "image_pull_failed"),
        ("container_start", "container_start_failed"),
        ("network_start", "network_unavailable"),
        ("credential_volume_missing", "credential_volume_missing"),
        ("credential_volume_owner", "credential_owner_mismatch"),
        ("credential_generation", "credential_generation_stale"),
        ("credential_login", "oauth_login_preflight_failed"),
        ("host_registration", "host_registration_failed"),
        ("host_registration_timeout", "host_registration_timeout"),
        ("host_capability", "codex_native_capability_missing"),
        ("harness_readiness", "harness_incompatible"),
        ("bridge_authentication", "bridge_auth_401"),
        ("server_endpoint", "server_endpoint_invalid"),
        ("session_create", "session_create_failed"),
        ("first_message_digest", "first_message_digest_mismatch"),
        ("first_message_reconcile", "ambiguous_posting_reconciliation"),
        ("resource_harvest", "resource_harvest_failed"),
        ("host_remove", "host_remove_failed"),
        ("release", "profile_lease_release_failed"),
    ],
)
async def test_product_path_failures_never_fallback_and_preserve_release_order(
    monkeypatch, fail_at: str, code: str
) -> None:
    """Drive the real coordinator with failures only at external-owner seams."""

    async def resolve_policy(_self, policy_ref):
        document = policy_document()
        document["host"]["mode"] = "on_demand_docker"
        document["host"]["backendRef"] = "container-backend"
        document["session"]["cleanup"] = "remove"
        return compile_policy_snapshot(
            policy_id=policy_ref.rsplit("@", 1)[0],
            version=int(policy_ref.rsplit("@", 1)[1]),
            document=document,
            validation={"valid": True, "diagnostics": []},
        )

    monkeypatch.setattr(
        OmnigentProfileBoundExecutionCoordinator,
        "_resolve_policy_snapshot",
        resolve_policy,
    )
    events, actions, owner_calls = await _run_coordinator_failure_case(
        fail_at=fail_at,
        code=code,
        release_failures=3 if fail_at == "release" else 0,
    )

    # The coordinator never invokes a direct Codex/alternate-profile fallback.
    assert "direct_codex" not in actions
    assert "alternate_profile" not in actions
    assert actions.count("envelope_created") == 1
    assert owner_calls.count(fail_at) <= 1 or fail_at == "release"
    assert events[-1][0] == "terminal"
    terminal = events[-1][1]["metadata"]
    if fail_at in {"host_remove", "release"}:
        assert terminal["janitorRequired"] is True
        assert terminal["leaseReleased"] is False
    elif fail_at in {
        "image_pull",
        "container_start",
        "network_start",
        "credential_login",
        "host_registration_timeout",
        "host_capability",
        "bridge_authentication",
        "first_message_reconcile",
        "resource_harvest",
    }:
        assert actions.index("host_stopped") < actions.index("provider_released")


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


async def test_controlling_restart_owner(store, session_factory) -> None:
    """Execute the durable disconnect/restart authorization owner."""

    from tests.integration.omnigent import test_embedded_recovery as recovery

    await recovery.test_disconnect_restart_reconnect_and_retry_matrix(
        store, session_factory
    )


async def test_controlling_first_message_reconciliation_owner(
    store, session_factory
) -> None:
    """Execute response-before-persist reconciliation at the durable owner."""

    from tests.integration.omnigent import test_embedded_recovery as recovery

    await recovery.test_embedded_response_before_persist_reconciles_and_digest_change_fails_closed(
        store, session_factory
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
