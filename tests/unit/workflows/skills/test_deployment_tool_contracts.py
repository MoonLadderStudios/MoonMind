from __future__ import annotations

import pytest

from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.deployment_tools import (
    DEPLOYMENT_UPDATE_TOOL_NAME,
    DEPLOYMENT_UPDATE_TOOL_VERSION,
    build_deployment_update_tool_definition_payload,
)
from moonmind.workflows.skills.plan_validation import (
    PlanValidationError,
    validate_plan_payload,
)
from moonmind.workflows.skills.tool_plan_contracts import parse_tool_definition
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
                    "version": DEPLOYMENT_UPDATE_TOOL_VERSION,
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
    assert definition.version == DEPLOYMENT_UPDATE_TOOL_VERSION
    assert definition.executor.activity_type == "mm.tool.execute"
    assert definition.executor.selector_mode == "by_capability"
    assert definition.required_capabilities == ("deployment_control", "docker_admin")
    assert definition.allowed_roles == ("admin",)
    assert definition.policies.retries.max_attempts == 1
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
    assert node.skill_version == DEPLOYMENT_UPDATE_TOOL_VERSION


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
