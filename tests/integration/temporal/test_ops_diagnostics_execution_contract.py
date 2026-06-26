from __future__ import annotations

from typing import Any, Mapping

import pytest

from moonmind.workflows.skills.artifact_store import InMemoryArtifactStore
from moonmind.workflows.skills.ops_diagnostics_execution import (
    OPS_DIAGNOSIS_ARTIFACT_TYPE,
    OpsStackDiagnosisExecutor,
    register_ops_diagnose_stack_tool_handler,
)
from moonmind.workflows.skills.deployment_tools import (
    OPS_DIAGNOSE_STACK_TOOL_NAME,
    build_ops_diagnose_stack_tool_definition_payload,
)
from moonmind.workflows.skills.tool_dispatcher import (
    ToolActivityDispatcher,
    execute_tool_activity,
)
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure, ToolResult
from moonmind.workflows.skills.tool_plan_contracts import parse_tool_definition
from moonmind.workflows.skills.tool_registry import create_registry_snapshot

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


class RecordingEvidenceWriter:
    def __init__(self) -> None:
        self.records: list[tuple[str, Mapping[str, Any]]] = []

    async def write(self, kind: str, payload: Mapping[str, Any]) -> str:
        self.records.append((kind, dict(payload)))
        return f"art:sha256:{kind:0<64}"[:75]


class FakeDeploymentControlDiagnosisRunner:
    def __init__(self, *, fail_include: str | None = None) -> None:
        self.fail_include = fail_include
        self.calls: list[dict[str, Any]] = []

    async def collect(
        self,
        *,
        stack: str,
        include: str,
        services: tuple[str, ...],
        tail_lines: int,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "stack": stack,
                "include": include,
                "services": services,
                "tailLines": tail_lines,
            }
        )
        if include == self.fail_include:
            raise RuntimeError("collector failed")
        if include == "container_health":
            return {
                "containers": [
                    {"service": "api", "state": "running", "health": "healthy"},
                    {
                        "service": "temporal-worker-agent-runtime",
                        "state": "running",
                        "health": "unhealthy",
                    },
                ]
            }
        if include == "recent_logs":
            return {
                "tailLines": tail_lines,
                "logs": "\n".join(f"line {i}" for i in range(tail_lines)),
            }
        return {"ok": True}


def _snapshot():
    return create_registry_snapshot(
        skills=(
            parse_tool_definition(build_ops_diagnose_stack_tool_definition_payload()),
        ),
        artifact_store=InMemoryArtifactStore(),
    )


def _payload(**input_overrides: object) -> dict[str, object]:
    inputs: dict[str, object] = {
        "stack": "moonmind",
        "reason": "MM-925 remediation diagnostics",
        "include": ["container_health", "recent_logs"],
        "services": ["api", "temporal-worker-agent-runtime"],
        "tailLines": 80,
        "targetWorkflowId": "mm:target",
        "remediationWorkflowId": "mm:remediation",
    }
    inputs.update(input_overrides)
    return {
        "id": "diagnose-moonmind",
        "tool": {"type": "skill", "name": OPS_DIAGNOSE_STACK_TOOL_NAME},
        "inputs": inputs,
    }


def _context(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "is_remediation_workflow": True,
        "remediation_policy": {"allowOpsDiagnostics": True},
        "workflow_id": "mm:remediation",
    }
    payload.update(overrides)
    return payload


async def test_ops_diagnosis_tool_dispatch_returns_summary_and_artifact_refs() -> None:
    writer = RecordingEvidenceWriter()
    runner = FakeDeploymentControlDiagnosisRunner()
    dispatcher = ToolActivityDispatcher()
    register_ops_diagnose_stack_tool_handler(
        dispatcher,
        executor=OpsStackDiagnosisExecutor(evidence_writer=writer, runner=runner),
    )

    result = await execute_tool_activity(
        invocation_payload=_payload(),
        registry_snapshot=_snapshot(),
        dispatcher=dispatcher,
        context=_context(),
    )

    assert isinstance(result, ToolResult)
    assert result.status == "COMPLETED"
    assert result.outputs["status"] == "SUCCEEDED"
    assert result.outputs["stack"] == "moonmind"
    assert result.outputs["artifactRefs"]["diagnosis"].startswith("art:sha256:")
    assert "Ops diagnosis SUCCEEDED" in result.outputs["summary"]
    assert writer.records[0][1]["artifactType"] == OPS_DIAGNOSIS_ARTIFACT_TYPE
    assert writer.records[0][1]["targetWorkflowId"] == "mm:target"
    assert runner.calls[1]["tailLines"] == 80
    assert any(
        finding["kind"] == "container_health"
        and finding["service"] == "temporal-worker-agent-runtime"
        and finding["severity"] == "warning"
        for finding in result.outputs["findings"]
    )

    definition = parse_tool_definition(
        build_ops_diagnose_stack_tool_definition_payload()
    )
    output_properties = set(definition.output_schema["properties"])
    assert set(result.outputs) - output_properties == set()


async def test_ops_diagnosis_tool_dispatch_denies_non_remediation_workflow() -> None:
    dispatcher = ToolActivityDispatcher()
    register_ops_diagnose_stack_tool_handler(
        dispatcher,
        executor=OpsStackDiagnosisExecutor(
            evidence_writer=RecordingEvidenceWriter(),
            runner=FakeDeploymentControlDiagnosisRunner(),
        ),
    )

    with pytest.raises(ToolFailure) as exc_info:
        await execute_tool_activity(
            invocation_payload=_payload(),
            registry_snapshot=_snapshot(),
            dispatcher=dispatcher,
            context={"remediation_policy": {"allowOpsDiagnostics": True}},
        )

    assert exc_info.value.error_code == "PERMISSION_DENIED"


async def test_ops_diagnosis_tool_dispatch_denies_policy_without_ops_diagnostics() -> None:
    dispatcher = ToolActivityDispatcher()
    register_ops_diagnose_stack_tool_handler(
        dispatcher,
        executor=OpsStackDiagnosisExecutor(
            evidence_writer=RecordingEvidenceWriter(),
            runner=FakeDeploymentControlDiagnosisRunner(),
        ),
    )

    with pytest.raises(ToolFailure) as exc_info:
        await execute_tool_activity(
            invocation_payload=_payload(),
            registry_snapshot=_snapshot(),
            dispatcher=dispatcher,
            context=_context(remediation_policy={"allowOpsDiagnostics": False}),
        )

    assert exc_info.value.error_code == "PERMISSION_DENIED"


async def test_ops_diagnosis_tool_dispatch_rejects_arbitrary_command_input() -> None:
    dispatcher = ToolActivityDispatcher()
    register_ops_diagnose_stack_tool_handler(
        dispatcher,
        executor=OpsStackDiagnosisExecutor(
            evidence_writer=RecordingEvidenceWriter(),
            runner=FakeDeploymentControlDiagnosisRunner(),
        ),
    )

    with pytest.raises(ToolFailure) as exc_info:
        await execute_tool_activity(
            invocation_payload=_payload(dockerCommand="docker ps"),
            registry_snapshot=_snapshot(),
            dispatcher=dispatcher,
            context=_context(),
        )

    assert exc_info.value.error_code == "INVALID_INPUT"


async def test_ops_diagnosis_tool_dispatch_partial_diagnostics_are_structured() -> None:
    writer = RecordingEvidenceWriter()
    dispatcher = ToolActivityDispatcher()
    register_ops_diagnose_stack_tool_handler(
        dispatcher,
        executor=OpsStackDiagnosisExecutor(
            evidence_writer=writer,
            runner=FakeDeploymentControlDiagnosisRunner(fail_include="recent_logs"),
        ),
    )

    result = await execute_tool_activity(
        invocation_payload=_payload(),
        registry_snapshot=_snapshot(),
        dispatcher=dispatcher,
        context=_context(),
    )

    assert result.status == "FAILED"
    assert result.outputs["status"] == "PARTIALLY_VERIFIED"
    assert writer.records[0][1]["evidence"]["recent_logs"]["status"] == "FAILED"
    assert any(
        finding["kind"] == "recent_logs" and finding["severity"] == "error"
        for finding in result.outputs["findings"]
    )
