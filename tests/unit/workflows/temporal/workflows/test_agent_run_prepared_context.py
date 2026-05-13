from __future__ import annotations

from datetime import datetime, timezone

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun


def _configure_workflow_runtime(monkeypatch):
    workflow_info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-agent-run-prepared-context",
            "run_id": "run-prepared-context",
            "search_attributes": {},
            "parent": None,
        },
    )
    logger = type(
        "Logger",
        (),
        {"info": lambda *a, **k: None, "warning": lambda *a, **k: None},
    )
    monkeypatch.setattr(agent_run_module.workflow, "info", workflow_info)
    monkeypatch.setattr(agent_run_module.workflow, "logger", logger)
    monkeypatch.setattr(agent_run_module.workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        agent_run_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="jules",
        executionProfileRef="jules-default",
        correlationId="corr-prepared-context",
        idempotencyKey="idem-prepared-context",
        parameters={
            "metadata": {
                "moonmind": {
                    "preparedContext": {
                        "manifestRef": "prepared-context-manifest://task-inputs",
                        "logicalStepId": "collect-evidence",
                        "objectiveContextRefs": [
                            "prepared-context://objective/objective-image"
                        ],
                        "stepContextRefs": [
                            "prepared-context://steps/collect-evidence/collect-notes"
                        ],
                        "rawInputRefs": [
                            "artifact://objective-image",
                            "artifact://collect-notes",
                        ],
                        "inputRefs": [
                            "prepared-context://objective/objective-image",
                            "prepared-context://steps/collect-evidence/collect-notes",
                            "artifact://objective-image",
                            "artifact://collect-notes",
                        ],
                        "targetCounts": {"objective": 1, "step": 1},
                    }
                }
            }
        },
    )


def test_result_metadata_preserves_parent_prepared_context_authority(monkeypatch):
    _configure_workflow_runtime(monkeypatch)
    run = MoonMindAgentRun()

    result = run._enrich_result_metadata(
        request=_request(),
        result=AgentRunResult(
            summary="done",
            metadata={
                "moonmind": {
                    "preparedContext": {
                        "manifestRef": "prepared-context-manifest://child",
                        "logicalStepId": "write-report",
                        "stepContextRefs": [
                            "prepared-context://steps/write-report/report-notes"
                        ],
                    }
                }
            },
        ),
    )

    assert result is not None
    moonmind_metadata = result.metadata["moonmind"]
    assert moonmind_metadata["preparedContext"]["logicalStepId"] == "collect-evidence"
    assert moonmind_metadata["preparedContext"]["stepContextRefs"] == [
        "prepared-context://steps/collect-evidence/collect-notes"
    ]
    assert "write-report" not in str(moonmind_metadata)
    assert "report-notes" not in str(moonmind_metadata)
