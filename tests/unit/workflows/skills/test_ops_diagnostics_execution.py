from __future__ import annotations

from typing import Any, Mapping

import pytest

from moonmind.workflows.skills.ops_diagnostics_execution import (
    OPS_DIAGNOSIS_ARTIFACT_TYPE,
    OpsStackDiagnosisExecutor,
)
from moonmind.workflows.skills.tool_plan_contracts import ToolFailure


class RecordingEvidenceWriter:
    def __init__(self) -> None:
        self.records: list[tuple[str, Mapping[str, Any]]] = []

    async def write(self, kind: str, payload: Mapping[str, Any]) -> str:
        self.records.append((kind, dict(payload)))
        return f"art:sha256:{kind:0<64}"[:75]


class RecordingDiagnosisRunner:
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
            raise RuntimeError("collector failed with token=secret-token")
        if include == "container_health":
            return {
                "containers": [
                    {
                        "service": "api",
                        "state": "running",
                        "health": "healthy",
                        "environment": {"API_TOKEN": "token=secret-token"},
                    },
                    {
                        "service": "temporal-worker-agent-runtime",
                        "state": "exited",
                    },
                ]
            }
        if include == "recent_logs":
            return {
                "tailLines": tail_lines,
                "logs": "starting\npassword=hunter2\nready",
            }
        return {"ok": True, "authorization": "Bearer raw-secret"}


def _context(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "is_remediation_workflow": True,
        "remediation_policy": {"allowOpsDiagnostics": True},
        "workflow_id": "mm:remediation",
    }
    payload.update(overrides)
    return payload


def _inputs(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "stack": "moonmind",
        "reason": "MM-925 diagnose failed remediation target",
        "include": ["container_health", "recent_logs"],
        "services": ["api", "temporal-worker-agent-runtime"],
        "tailLines": 75,
        "targetWorkflowId": "mm:target",
        "remediationWorkflowId": "mm:remediation",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_ops_diagnosis_denies_non_remediation_workflow() -> None:
    executor = OpsStackDiagnosisExecutor(
        evidence_writer=RecordingEvidenceWriter(),
        runner=RecordingDiagnosisRunner(),
    )

    with pytest.raises(ToolFailure) as exc_info:
        await executor.execute(_inputs(), context={"remediation_policy": {"allowOpsDiagnostics": True}})

    assert exc_info.value.error_code == "PERMISSION_DENIED"
    assert "remediation workflows" in exc_info.value.message


@pytest.mark.asyncio
async def test_ops_diagnosis_denies_when_policy_disallows() -> None:
    executor = OpsStackDiagnosisExecutor(
        evidence_writer=RecordingEvidenceWriter(),
        runner=RecordingDiagnosisRunner(),
    )

    with pytest.raises(ToolFailure) as exc_info:
        await executor.execute(_inputs(), context=_context(remediation_policy={}))

    assert exc_info.value.error_code == "PERMISSION_DENIED"
    assert "policy" in exc_info.value.message


@pytest.mark.asyncio
async def test_ops_diagnosis_rejects_arbitrary_command_input() -> None:
    executor = OpsStackDiagnosisExecutor(
        evidence_writer=RecordingEvidenceWriter(),
        runner=RecordingDiagnosisRunner(),
    )

    with pytest.raises(ToolFailure) as exc_info:
        await executor.execute(_inputs(command="docker ps"), context=_context())

    assert exc_info.value.error_code == "INVALID_INPUT"
    assert exc_info.value.details["fields"] == ["command"]


@pytest.mark.asyncio
async def test_ops_diagnosis_publishes_redacted_artifact_and_findings() -> None:
    writer = RecordingEvidenceWriter()
    runner = RecordingDiagnosisRunner()
    executor = OpsStackDiagnosisExecutor(evidence_writer=writer, runner=runner)

    result = await executor.execute(_inputs(), context=_context())

    assert result.status == "COMPLETED"
    assert result.outputs["status"] == "SUCCEEDED"
    assert result.outputs["artifactRefs"]["diagnosis"].startswith("art:sha256:")
    assert result.outputs["findings"] == [
        {
            "kind": "container_health",
            "severity": "error",
            "message": "Service temporal-worker-agent-runtime is not running.",
            "service": "temporal-worker-agent-runtime",
            "evidenceRef": result.outputs["artifactRefs"]["diagnosis"],
        }
    ]
    assert len(writer.records) == 1
    kind, payload = writer.records[0]
    assert kind == "diagnosis"
    assert payload["artifactType"] == OPS_DIAGNOSIS_ARTIFACT_TYPE
    serialized = str(payload)
    assert "secret-token" not in serialized
    assert "hunter2" not in serialized
    assert "[REDACTED]" in serialized
    assert runner.calls[1]["tailLines"] == 75


@pytest.mark.asyncio
async def test_ops_diagnosis_returns_partial_for_failed_evidence_class() -> None:
    writer = RecordingEvidenceWriter()
    executor = OpsStackDiagnosisExecutor(
        evidence_writer=writer,
        runner=RecordingDiagnosisRunner(fail_include="recent_logs"),
    )

    result = await executor.execute(_inputs(), context=_context())

    assert result.status == "FAILED"
    assert result.outputs["status"] == "PARTIALLY_VERIFIED"
    assert any(
        finding["kind"] == "recent_logs" and finding["severity"] == "error"
        for finding in result.outputs["findings"]
    )
    assert writer.records[0][1]["evidence"]["recent_logs"]["status"] == "FAILED"
    assert "secret-token" not in str(writer.records[0][1])


@pytest.mark.asyncio
async def test_ops_diagnosis_rejects_unknown_service_and_unbounded_tail() -> None:
    executor = OpsStackDiagnosisExecutor(
        evidence_writer=RecordingEvidenceWriter(),
        runner=RecordingDiagnosisRunner(),
    )

    with pytest.raises(ToolFailure) as service_exc:
        await executor.execute(_inputs(services=["host-root"]), context=_context())
    assert "Unsupported MoonMind service" in service_exc.value.message

    with pytest.raises(ToolFailure) as tail_exc:
        await executor.execute(_inputs(tailLines=1001), context=_context())
    assert "tailLines" in tail_exc.value.message
