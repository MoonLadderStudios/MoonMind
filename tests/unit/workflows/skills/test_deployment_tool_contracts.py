from __future__ import annotations

import dataclasses

import pytest

from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    OPS_DIAGNOSE_STACK_TOOL_NAME,
    RELEASE_SUPERVISION_MAX_ATTEMPTS,
    build_deployment_update_tool_definition_payload,
    build_ops_diagnose_stack_tool_definition_payload,
)
from moonmind.workflows.skills.plan_validation import (
    PlanValidationError,
    validate_plan_payload,
)
from moonmind.workflows.skills.tool_plan_contracts import (
    ContractValidationError,
    parse_tool_definition,
)
from moonmind.workflows.skills.tool_registry import create_registry_snapshot


def _snapshot():
    return create_registry_snapshot(
        skills=(
            parse_tool_definition(build_deployment_update_tool_definition_payload()),
        ),
        artifact_store=InMemoryArtifactStore(),
    )


def _valid_plan_payload(snapshot) -> dict[str, object]:
    return {
        "plan_version": "1.0",
        "metadata": {
            "title": "MM-519 deployment update tool contract validation",
            "created_at": "2026-04-25T00:00:00Z",
            "registry_snapshot": {
                "digest": snapshot.digest,
                "artifact_ref": snapshot.artifact_ref,
            },
        },
        "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
        "nodes": [
            {
                "id": "update-moonmind-deployment",
                "tool": {
                    "type": "skill",
                    "name": DEPLOYMENT_UPDATE_TOOL_NAME,
                },
                "inputs": {
                    "stack": "moonmind",
                    "image": {
                        "repository": "ghcr.io/moonladderstudios/moonmind",
                        "reference": "20260425.1234",
                        "resolvedDigest": "sha256:" + "a" * 64,
                    },
                    "mode": "changed_services",
                    "removeOrphans": True,
                    "wait": True,
                    "runSmokeCheck": True,
                    "pauseWork": False,
                    "pruneOldImages": False,
                    "reason": "Update to the latest tested MoonMind build",
                },
            }
        ],
        "edges": [],
    }


def test_deployment_update_tool_definition_matches_mm519_contract() -> None:
    definition = parse_tool_definition(
        build_deployment_update_tool_definition_payload()
    )

    assert definition.name == DEPLOYMENT_UPDATE_TOOL_NAME
    assert definition.executor.activity_type == "mm.tool.execute"
    assert definition.executor.selector_mode == "by_capability"
    assert definition.required_capabilities == (
        "deployment_control",
        "docker_admin",
    )
    assert definition.allowed_roles == ("admin",)
    assert definition.ops_runtime is not None
    raw_payload = build_deployment_update_tool_definition_payload()
    assert raw_payload["security"]["opsRuntime"] == {
        "kind": "MoonMindOpsRuntime",
        "name": "docker-admin-runtime",
        "purpose": "moonmind-application-operations",
        "backend": "docker",
        "exposedToManagedAgents": False,
        "allowedOperations": [
            "status",
            "deploy",
            "restart",
            "rollback",
            "imageRefresh",
            "logs",
        ],
        "dockerBackend": {
            "hostDockerAccess": True,
            "component": "moonmind-ops-runner",
        },
    }
    assert definition.to_payload()["security"]["opsRuntime"] == raw_payload[
        "security"
    ]["opsRuntime"]
    # The release is detached and outlives its supervising Activity, so the
    # attempt budget is derived from the job budget rather than pinned here.
    # See test_deployment_update_policy_supervises_the_detached_release_budget.
    assert definition.policies.retries.max_attempts == RELEASE_SUPERVISION_MAX_ATTEMPTS
    assert definition.policies.retries.non_retryable_error_codes == (
        "INVALID_INPUT",
        "PERMISSION_DENIED",
        "POLICY_VIOLATION",
        "DEPLOYMENT_LOCKED",
    )

    input_schema = definition.input_schema
    assert input_schema["required"] == ["stack", "image"]
    assert input_schema["additionalProperties"] is False
    assert input_schema["properties"]["stack"]["enum"] == ["moonmind"]
    assert input_schema["properties"]["mode"]["enum"] == [
        "changed_services",
        "force_recreate",
    ]
    image_schema = input_schema["properties"]["image"]
    assert image_schema["required"] == ["repository", "reference"]
    assert image_schema["additionalProperties"] is False
    assert "resolvedDigest" in image_schema["properties"]

    output_schema = definition.output_schema
    assert output_schema["required"] == [
        "status",
        "stack",
        "requestedImage",
        "updatedServices",
        "runningServices",
    ]
    assert output_schema["properties"]["status"]["enum"] == [
        "SUCCEEDED",
        "FAILED",
        "PARTIALLY_VERIFIED",
    ]
    assert "verificationArtifactRef" in output_schema["properties"]
    assert "audit" in output_schema["properties"]


def test_ops_diagnose_stack_tool_definition_matches_mm925_contract() -> None:
    definition = parse_tool_definition(
        build_ops_diagnose_stack_tool_definition_payload()
    )

    assert definition.name == OPS_DIAGNOSE_STACK_TOOL_NAME
    assert definition.executor.activity_type == "mm.tool.execute"
    assert definition.executor.selector_mode == "by_capability"
    assert definition.required_capabilities == (
        "deployment_control",
        "docker_admin",
    )
    assert definition.allowed_roles == ("admin",)
    assert definition.ops_runtime is not None
    raw_payload = build_ops_diagnose_stack_tool_definition_payload()
    assert raw_payload["security"]["remediationPolicyRequired"] is True
    assert raw_payload["security"]["exposedToManagedAgents"] is False
    assert raw_payload["security"]["opsRuntime"] == {
        "kind": "MoonMindOpsRuntime",
        "name": "docker-admin-runtime",
        "purpose": "moonmind-application-operations",
        "backend": "docker",
        "exposedToManagedAgents": False,
        "allowedOperations": ["status", "logs"],
        "dockerBackend": {
            "hostDockerAccess": True,
            "component": "moonmind-ops-runner",
        },
    }
    assert definition.policies.retries.max_attempts == 1
    assert definition.policies.retries.non_retryable_error_codes == (
        "INVALID_INPUT",
        "PERMISSION_DENIED",
        "POLICY_VIOLATION",
    )

    input_schema = definition.input_schema
    assert input_schema["required"] == ["stack", "reason"]
    assert input_schema["additionalProperties"] is False
    assert input_schema["properties"]["stack"]["enum"] == ["moonmind"]
    assert input_schema["properties"]["tailLines"]["minimum"] == 50
    assert input_schema["properties"]["tailLines"]["maximum"] == 1000
    assert "command" not in input_schema["properties"]
    assert "hostPath" not in input_schema["properties"]
    assert input_schema["properties"]["include"]["items"]["enum"] == [
        "compose_ps",
        "compose_images",
        "container_health",
        "container_inspect_summary",
        "recent_logs",
        "api_health",
        "worker_health",
        "temporal_connectivity",
        "artifact_store_health",
        "disk_memory_cpu",
    ]

    output_schema = definition.output_schema
    assert output_schema["required"] == [
        "status",
        "stack",
        "summary",
        "findings",
        "artifactRefs",
    ]
    assert output_schema["properties"]["status"]["enum"] == [
        "SUCCEEDED",
        "FAILED",
        "PARTIALLY_VERIFIED",
    ]


@pytest.mark.parametrize("stack", ["other", "", "default"])
def test_ops_diagnose_stack_plan_rejects_unknown_stack(stack: str) -> None:
    snapshot = create_registry_snapshot(
        skills=(
            parse_tool_definition(build_ops_diagnose_stack_tool_definition_payload()),
        ),
        artifact_store=InMemoryArtifactStore(),
    )
    payload = _valid_plan_payload(snapshot)
    payload["nodes"][0]["tool"]["name"] = OPS_DIAGNOSE_STACK_TOOL_NAME
    payload["nodes"][0]["inputs"] = {
        "stack": stack,
        "reason": "MM-925 diagnosis",
    }

    with pytest.raises(PlanValidationError):
        validate_plan_payload(payload=payload, registry_snapshot=snapshot)


def test_ops_diagnose_stack_plan_rejects_unknown_include_value() -> None:
    snapshot = create_registry_snapshot(
        skills=(
            parse_tool_definition(build_ops_diagnose_stack_tool_definition_payload()),
        ),
        artifact_store=InMemoryArtifactStore(),
    )
    payload = _valid_plan_payload(snapshot)
    payload["nodes"][0]["tool"]["name"] = OPS_DIAGNOSE_STACK_TOOL_NAME
    payload["nodes"][0]["inputs"] = {
        "stack": "moonmind",
        "reason": "MM-925 diagnosis",
        "include": ["compose_ps", "raw_docker"],
    }

    with pytest.raises(PlanValidationError):
        validate_plan_payload(payload=payload, registry_snapshot=snapshot)


@pytest.mark.parametrize("field", ["command", "dockerCommand", "hostPath", "path"])
def test_ops_diagnose_stack_plan_rejects_arbitrary_command_inputs(
    field: str,
) -> None:
    snapshot = create_registry_snapshot(
        skills=(
            parse_tool_definition(build_ops_diagnose_stack_tool_definition_payload()),
        ),
        artifact_store=InMemoryArtifactStore(),
    )
    payload = _valid_plan_payload(snapshot)
    payload["nodes"][0]["tool"]["name"] = OPS_DIAGNOSE_STACK_TOOL_NAME
    payload["nodes"][0]["inputs"] = {
        "stack": "moonmind",
        "reason": "MM-925 diagnosis",
        field: "docker ps",
    }

    with pytest.raises(PlanValidationError, match=f"Unexpected field '{field}'"):
        validate_plan_payload(payload=payload, registry_snapshot=snapshot)


def test_deployment_update_tool_definition_rejects_agent_exposed_ops_runtime() -> None:
    payload = build_deployment_update_tool_definition_payload()
    payload["security"]["opsRuntime"]["exposedToManagedAgents"] = True

    with pytest.raises(ContractValidationError, match="exposedToManagedAgents"):
        parse_tool_definition(payload)


def test_representative_deployment_update_plan_validates_against_registry_snapshot(
) -> None:
    snapshot = _snapshot()
    validated = validate_plan_payload(
        payload=_valid_plan_payload(snapshot),
        registry_snapshot=snapshot,
    )

    assert validated.topological_order == ("update-moonmind-deployment",)
    node = validated.plan.nodes[0]
    assert node.skill_name == DEPLOYMENT_UPDATE_TOOL_NAME


@pytest.mark.parametrize(
    "field",
    ["command", "composeFile", "hostPath", "updaterRunnerImage"],
)
def test_deployment_update_plan_rejects_shell_path_and_runner_overrides(
    field: str,
) -> None:
    snapshot = _snapshot()
    payload = _valid_plan_payload(snapshot)
    payload["nodes"][0]["inputs"][field] = "docker compose up"

    with pytest.raises(PlanValidationError, match=f"Unexpected field '{field}'"):
        validate_plan_payload(payload=payload, registry_snapshot=snapshot)


def test_deployment_update_policy_supervises_the_detached_release_budget() -> None:
    """The supervising activity must outlive the job it supervises.

    Regression: the detached updater owns a two-hour budget and retries three
    times inside it, while ``deployment.update_compose_stack`` was declared
    with a single 900-second attempt. Promotion replaces every worker fleet,
    including the one running this activity, so a single attempt could
    neither wait out a long release nor re-attach after its own worker was
    replaced. Temporal failed the workflow while the release succeeded.
    """

    from moonmind.workflows.skills.deployment_release import (
        RELEASE_JOB_BUDGET_SECONDS,
    )

    definition = parse_tool_definition(
        build_deployment_update_tool_definition_payload()
    )
    timeouts = definition.policies.timeouts
    retries = definition.policies.retries

    # Promotion replaces this activity's own worker, so re-attachment - and
    # therefore more than one attempt - is the normal path, not the edge case.
    assert retries.max_attempts > 1
    # The job's own deadline decides when a release stops, so the activity
    # must still be scheduled when that deadline arrives.
    assert timeouts.schedule_to_close_seconds >= RELEASE_JOB_BUDGET_SECONDS
    # Attempts cover the budget end to end; no release inside its own budget
    # can exhaust the supervisor first.
    assert (
        retries.max_attempts * timeouts.start_to_close_seconds
        >= RELEASE_JOB_BUDGET_SECONDS
    )
    # A supervision window that never ends cannot notice a replaced worker.
    assert timeouts.start_to_close_seconds < RELEASE_JOB_BUDGET_SECONDS
    # A terminal release failure stays terminal.
    assert retries.non_retryable_error_codes == (
        "INVALID_INPUT",
        "PERMISSION_DENIED",
        "POLICY_VIOLATION",
        "DEPLOYMENT_LOCKED",
    )


def test_supervision_window_never_interrupts_pre_launch_work() -> None:
    """One attempt must outlast a single pre-launch compose command.

    Regression (Codex P1 on #4422): the supervision window was shorter than
    the deployment runner's own command timeout, so a slow updater pull could
    not finish inside one attempt. Temporal cancelled it mid-pull and started
    an overlapping retry, and the durable deadline - established only after
    the pull - landed past the supervising Activity's schedule, leaving a
    near-budget release running after its workflow had already timed out.
    """

    from moonmind.workflows.skills.deployment_execution import HostDockerComposeRunner
    from moonmind.workflows.skills.deployment_tools import (
        RELEASE_JOB_BUDGET_SECONDS,
        RELEASE_SUPERVISION_SCHEDULE_TO_CLOSE_SECONDS,
        RELEASE_SUPERVISION_WINDOW_SECONDS,
    )

    # A window shorter than one compose command cancels that command instead
    # of supervising it.
    command_timeout = next(
        field.default
        for field in dataclasses.fields(HostDockerComposeRunner)
        if field.name == "command_timeout_seconds"
    )
    assert RELEASE_SUPERVISION_WINDOW_SECONDS >= command_timeout
    # The Activity must still be scheduled when the job's deadline arrives,
    # with a window of margin for the final re-attachment to read the receipt.
    assert (
        RELEASE_SUPERVISION_SCHEDULE_TO_CLOSE_SECONDS
        >= RELEASE_JOB_BUDGET_SECONDS + RELEASE_SUPERVISION_WINDOW_SECONDS
    )
