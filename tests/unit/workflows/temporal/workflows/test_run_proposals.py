import json
from datetime import datetime, timezone
from typing import Any

import pytest

pytest.importorskip("temporalio")

from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

@pytest.fixture
def mock_run_workflow(monkeypatch: pytest.MonkeyPatch) -> MoonMindRunWorkflow:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "org/repo"
    
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "now", lambda: datetime.now(timezone.utc))
    workflow_info = type(
        "WorkflowInfo",
        (),
        {"namespace": "default", "workflow_id": "wf-1", "run_id": "run-1", "search_attributes": {}},
    )
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)
    
    logger = type("Logger", (), {"info": lambda *a, **k: None, "warning": lambda *a, **k: None})
    monkeypatch.setattr(run_workflow_module.workflow, "logger", logger)

    monkeypatch.setattr(run_workflow_module.settings.workflow, "enable_proposals", True)

    return workflow

def _to_serializable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return str(obj)

@pytest.mark.asyncio
async def test_run_proposals_stage_propagates_policy(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        dumped = json.loads(json.dumps(payload, default=_to_serializable))
        captured.append((activity_type, dumped))
        if activity_type == "proposal.generate":
            return [{"title": "Generated proposal 1"}]
        if activity_type == "proposal.submit":
            return {"submitted_count": 1}
        return {}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )

    parameters = {
        "repo": "org/repo",
        "task": {
            "proposeTasks": True,
            "proposalPolicy": {
                "max_items": {"workflow_repo": 5},
                "targets": ["workflow_repo"],
                "default_runtime": "claude",
            }
        }
    }

    await mock_run_workflow._run_proposals_stage(parameters=parameters)

    assert len(captured) == 2
    assert captured[0][0] == "proposal.generate"
    assert captured[1][0] == "proposal.submit"
    
    submit_payload = captured[1][1]
    policy_payload = submit_payload["policy"]
    assert policy_payload["maxItems"] == {"workflow_repo": 5}
    assert policy_payload["targets"] == ["workflow_repo"]
    assert policy_payload["defaultRuntime"] == "claude"

    assert mock_run_workflow._proposals_generated == 1
    assert mock_run_workflow._proposals_submitted == 1


@pytest.mark.asyncio
async def test_run_proposals_stage_passes_compact_telemetry_signals(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_generate_payload: dict[str, Any] = {}
    mock_run_workflow._last_step_summary = (
        "Retried a flaky test and wrote diagnostics for review."
    )
    mock_run_workflow._last_diagnostics_ref = "artifact://diag-mm-794"

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "proposal.generate":
            captured_generate_payload.update(
                json.loads(json.dumps(payload, default=_to_serializable))
            )
            return []
        return {}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )

    await mock_run_workflow._run_proposals_stage(
        parameters={"repo": "org/repo", "task": {"proposeTasks": True}}
    )

    signals = captured_generate_payload["telemetrySignals"]
    assert signals == [
        {
            "type": "flaky_test",
            "tags": ["flaky_test"],
            "severity": "medium",
            "summary": "Retried a flaky test and wrote diagnostics for review.",
            "diagnostics_ref": "artifact://diag-mm-794",
        },
        {
            "type": "retry",
            "tags": ["retry"],
            "severity": "medium",
            "summary": "Retried a flaky test and wrote diagnostics for review.",
            "diagnostics_ref": "artifact://diag-mm-794",
        },
        {
            "type": "artifact_gap",
            "tags": ["artifact_gap"],
            "severity": "medium",
            "summary": "Retried a flaky test and wrote diagnostics for review.",
            "diagnostics_ref": "artifact://diag-mm-794",
        },
    ]


@pytest.mark.asyncio
async def test_run_proposals_stage_does_not_fabricate_signal_from_diagnostics_ref(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_generate_payload: dict[str, Any] = {}
    mock_run_workflow._last_step_summary = "Completed successfully."
    mock_run_workflow._last_diagnostics_ref = "artifact://diag-mm-794"

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "proposal.generate":
            captured_generate_payload.update(
                json.loads(json.dumps(payload, default=_to_serializable))
            )
            return []
        return {}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )

    await mock_run_workflow._run_proposals_stage(
        parameters={"repo": "org/repo", "task": {"proposeTasks": True}}
    )

    assert "telemetrySignals" not in captured_generate_payload


@pytest.mark.asyncio
async def test_run_proposals_stage_records_operator_visible_outcomes(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute_activity(
        activity_type: str,
        _payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "proposal.generate":
            return [{"title": "Generated proposal 1"}, {"title": "Generated proposal 2"}]
        if activity_type == "proposal.submit":
            return {
                "submitted_count": 2,
                "deliveredCount": 1,
                "validationErrors": [
                    {
                        "code": "proposal_missing_task",
                        "message": "proposal skipped: [REDACTED]",
                    }
                ],
                "deliveryFailures": [
                    {
                        "provider": "jira",
                        "code": "delivery_failed",
                        "message": "delivery failed: [REDACTED]",
                    }
                ],
                "externalLinks": [
                    {
                        "provider": "jira",
                        "externalKey": "MM-901",
                        "externalUrl": "https://jira.example/browse/MM-901",
                    }
                ],
                "dedupUpdates": [
                    {
                        "provider": "github",
                        "externalKey": "42",
                        "created": False,
                        "duplicateSource": "existing-open-issue",
                    }
                ],
            }
        return {}

    monkeypatch.setattr(
        run_workflow_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )

    await mock_run_workflow._run_proposals_stage(
        parameters={"repo": "org/repo", "task": {"proposeTasks": True}}
    )

    assert mock_run_workflow._proposals_generated == 2
    assert mock_run_workflow._proposals_submitted == 2
    assert mock_run_workflow._proposals_delivered == 1
    assert mock_run_workflow._proposal_validation_errors == [
        {"code": "proposal_missing_task", "message": "proposal skipped: [REDACTED]"}
    ]
    assert mock_run_workflow._proposal_delivery_failures == [
        {
            "provider": "jira",
            "code": "delivery_failed",
            "message": "delivery failed: [REDACTED]",
        }
    ]
    assert mock_run_workflow._proposal_external_links == [
        {
            "provider": "jira",
            "externalKey": "MM-901",
            "externalUrl": "https://jira.example/browse/MM-901",
        }
    ]
    assert mock_run_workflow._proposal_dedup_updates == [
        {
            "provider": "github",
            "externalKey": "42",
            "created": False,
            "duplicateSource": "existing-open-issue",
        }
    ]

@pytest.mark.asyncio
async def test_run_proposals_stage_ignores_flattened_legacy_policy_fields(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        dumped = json.loads(json.dumps(payload, default=_to_serializable))
        captured.append((activity_type, dumped))
        if activity_type == "proposal.generate":
            return [{"title": "Generated proposal 1"}]
        if activity_type == "proposal.submit":
            return {"submitted_count": 1}
        return {}

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)

    await mock_run_workflow._run_proposals_stage(
        parameters={
            "repo": "org/repo",
            "proposeTasks": True,
            "proposalMaxItems": 8,
            "proposalTargets": "moonmind",
            "proposalDefaultRuntime": "gemini_cli",
        }
    )

    assert len(captured) == 2
    submit_payload = captured[1][1]
    assert submit_payload["policy"] == {}

@pytest.mark.asyncio
async def test_run_proposals_stage_honors_nested_task_propose_tasks(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        dumped = json.loads(json.dumps(payload, default=_to_serializable))
        captured.append((activity_type, dumped))
        if activity_type == "proposal.generate":
            return [{"title": "Generated proposal 1"}]
        if activity_type == "proposal.submit":
            return {"submitted_count": 1}
        return {}

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)

    await mock_run_workflow._run_proposals_stage(
        parameters={
            "repo": "org/repo",
            "task": {
                "proposeTasks": True,
            },
        }
    )

    assert [activity for activity, _payload in captured] == [
        "proposal.generate",
        "proposal.submit",
    ]

@pytest.mark.asyncio
async def test_run_proposals_stage_uses_root_compatibility_fallback_when_task_flag_missing(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        dumped = json.loads(json.dumps(payload, default=_to_serializable))
        captured.append((activity_type, dumped))
        if activity_type == "proposal.generate":
            return [{"title": "Generated proposal 1"}]
        if activity_type == "proposal.submit":
            return {"submitted_count": 1}
        return {}

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)

    await mock_run_workflow._run_proposals_stage(
        parameters={
            "repo": "org/repo",
            "proposeTasks": True,
            "task": {
                "instructions": "Canonical task payload without proposal opt-in.",
            },
        }
    )

    assert [activity for activity, _payload in captured] == [
        "proposal.generate",
        "proposal.submit",
    ]

@pytest.mark.asyncio
async def test_run_proposals_stage_nested_task_flag_takes_precedence_on_conflict(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, Any]] = []

    async def fake_execute_activity(activity_type: str, *args: Any, **kwargs: Any) -> Any:
        captured.append((activity_type, args))

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)

    await mock_run_workflow._run_proposals_stage(
        parameters={
            "repo": "org/repo",
            "proposeTasks": True,
            "task": {
                "instructions": "Canonical task payload disables proposal opt-in.",
                "proposeTasks": False,
            },
        }
    )

    assert captured == []

@pytest.mark.asyncio
async def test_run_proposals_stage_keeps_root_only_compatibility_read(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, Any]] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        dumped = json.loads(json.dumps(payload, default=_to_serializable))
        captured.append((activity_type, dumped))
        if activity_type == "proposal.generate":
            return [{"title": "Generated proposal 1"}]
        if activity_type == "proposal.submit":
            return {"submitted_count": 1}
        return {}

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)

    await mock_run_workflow._run_proposals_stage(
        parameters={
            "repo": "org/repo",
            "proposeTasks": True,
        }
    )

    assert [activity for activity, _payload in captured] == [
        "proposal.generate",
        "proposal.submit",
    ]

@pytest.mark.asyncio
async def test_run_proposals_stage_skipped_when_proposeTasks_false(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, Any]] = []

    async def fake_execute_activity(activity_type: str, *args: Any, **kwargs: Any) -> Any:
        captured.append((activity_type, args))

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)

    parameters = {
        "repo": "org/repo",
        "task": {
            "proposeTasks": False,
        },
    }

    await mock_run_workflow._run_proposals_stage(parameters=parameters)

    assert len(captured) == 0

@pytest.mark.asyncio
async def test_run_proposals_stage_skipped_when_globally_disabled(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, Any]] = []

    monkeypatch.setattr(run_workflow_module.settings.workflow, "enable_proposals", False)

    async def fake_execute_activity(activity_type: str, *args: Any, **kwargs: Any) -> Any:
        captured.append((activity_type, args))

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)

    parameters = {
        "repo": "org/repo",
        "proposeTasks": True,
    }

    await mock_run_workflow._run_proposals_stage(parameters=parameters)

    assert len(captured) == 0


@pytest.mark.asyncio
async def test_run_proposals_stage_passes_artifact_refs_instead_of_raw_inputs(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_generate_payload: dict[str, Any] = {}
    mock_run_workflow._input_ref = "artifact://run-input"
    mock_run_workflow._plan_ref = "artifact://plan"
    mock_run_workflow._logs_ref = "artifact://logs"
    mock_run_workflow._summary_ref = "artifact://summary"
    mock_run_workflow._report_ref = "artifact://reports/run-summary"
    mock_run_workflow._last_step_id = "agent-step"
    mock_run_workflow._last_step_summary = "Agent completed with diagnostics."
    mock_run_workflow._last_diagnostics_ref = "artifact://diag-last"
    step_row = {
        "logicalStepId": "agent-step",
        "refs": {
            "latestStepExecutionManifestRef": "artifact://step-manifest-latest",
            "stepExecutionManifestRefs": [
                "artifact://step-manifest-1",
                "artifact://step-manifest-latest",
            ],
        },
        "artifacts": {
            "outputSummary": "artifact://output-summary",
            "outputPrimary": "artifact://agent-run-result",
            "runtimeStdout": "artifact://stdout",
            "runtimeStderr": "artifact://stderr",
            "runtimeMergedLogs": "artifact://merged-logs",
            "runtimeDiagnostics": "artifact://diag-step",
            "providerSnapshot": "artifact://provider-snapshot",
            "stepExecutionManifestRef": "artifact://step-manifest-latest",
        },
    }
    mock_run_workflow._step_ledger_rows = [step_row]
    mock_run_workflow._step_ledger_by_id = {"agent-step": step_row}

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "proposal.generate":
            captured_generate_payload.update(
                json.loads(json.dumps(payload, default=_to_serializable))
            )
            return []
        return {}

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)

    await mock_run_workflow._run_proposals_stage(
        parameters={
            "repo": "org/repo",
            "task": {
                "proposeTasks": True,
                "instructions": "raw secret-bearing task input should stay by ref",
                "steps": [
                    {
                        "id": "agent-step",
                        "title": "Agent step",
                        "type": "skill",
                        "instructions": "raw step prompt should stay by ref",
                        "skill": {"id": "jira-implement"},
                        "source": {
                            "kind": "preset-derived",
                            "presetId": "preset-1",
                            "includePath": ["root", "implement"],
                            "originalStepId": "implement-story",
                        },
                    }
                ],
                "runtime": {"mode": "codex", "effort": 2},
                "skills": {"include": [{"name": "pr-resolver"}]},
                "authoredPresets": [
                    {
                        "id": "preset-1",
                        "name": "Implementation preset",
                        "sourceRef": "artifact://preset-source",
                        "body": "raw preset body should not cross workflow history",
                    }
                ],
                "proposalIdea": "Add missing proposal hardening test coverage",
            },
            "logs": "raw logs should stay by artifact ref",
            "diagnostics": {"raw": "raw diagnostics should stay by ref"},
        }
    )

    assert captured_generate_payload["parameters"] == {
        "workflow": {
            "proposalIdea": "Add missing proposal hardening test coverage",
            "runtime": {"mode": "codex", "effort": 2},
            "skills": {"include": [{"name": "pr-resolver"}]},
            "steps": [
                {
                    "id": "agent-step",
                    "title": "Agent step",
                    "type": "skill",
                    "skill": {"id": "jira-implement"},
                    "source": {
                        "kind": "preset-derived",
                        "presetId": "preset-1",
                        "includePath": ["root", "implement"],
                        "originalStepId": "implement-story",
                    },
                }
            ],
            "authoredPresets": [
                {
                    "id": "preset-1",
                    "name": "Implementation preset",
                    "sourceRef": "artifact://preset-source",
                    "index": 0,
                }
            ],
        }
    }
    serialized_payload = json.dumps(captured_generate_payload, sort_keys=True)
    assert "raw secret-bearing task input" not in serialized_payload
    assert "raw step prompt" not in serialized_payload
    assert "raw logs should stay" not in serialized_payload
    assert "raw diagnostics should stay" not in serialized_payload
    assert "raw preset body" not in serialized_payload

    assert captured_generate_payload["evidenceRefs"] == {
        "inputRef": "artifact://run-input",
        "planRef": "artifact://plan",
        "logsRef": "artifact://logs",
        "summaryRef": "artifact://summary",
        "finishSummaryRef": "artifact://reports/run-summary",
        "diagnosticsRef": "artifact://diag-last",
        "lastStep": {
            "id": "agent-step",
            "outputRefs": {
                "summaryRef": "artifact://output-summary",
                "primaryRef": "artifact://agent-run-result",
                "stdoutRef": "artifact://stdout",
                "stderrRef": "artifact://stderr",
                "logsRef": "artifact://merged-logs",
                "diagnosticsRef": "artifact://diag-step",
                "providerSnapshotRef": "artifact://provider-snapshot",
                "stepExecutionManifestRef": "artifact://step-manifest-latest",
                "stepExecutionManifestRefs": [
                    "artifact://step-manifest-1",
                    "artifact://step-manifest-latest",
                ],
            },
        },
    }


@pytest.mark.asyncio
async def test_run_proposals_stage_passes_resolved_skill_metadata_by_ref(
    mock_run_workflow: MoonMindRunWorkflow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_generate_payload: dict[str, Any] = {}
    mock_run_workflow._last_step_id = "agent-step"
    step_row = {
        "logicalStepId": "agent-step",
        "refs": {
            "latestStepExecutionManifestRef": "artifact://skill-step-manifest",
            "stepExecutionManifestRefs": ["artifact://skill-step-manifest"],
        },
        "artifacts": {
            "outputPrimary": "artifact://agent-run-result",
            "runtimeDiagnostics": "artifact://diag-step",
            "stepExecutionManifestRef": "artifact://skill-step-manifest",
        },
    }
    mock_run_workflow._step_ledger_rows = [step_row]
    mock_run_workflow._step_ledger_by_id = {"agent-step": step_row}

    async def fake_execute_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "proposal.generate":
            captured_generate_payload.update(
                json.loads(json.dumps(payload, default=_to_serializable))
            )
            return []
        return {}

    monkeypatch.setattr(run_workflow_module.workflow, "execute_activity", fake_execute_activity)

    await mock_run_workflow._run_proposals_stage(
        parameters={
            "repo": "org/repo",
            "task": {
                "proposeTasks": True,
                "skills": {
                    "include": [{"name": "jira-implement"}],
                    "metadata": "raw resolved skill metadata must not be embedded",
                },
            },
        }
    )

    serialized_payload = json.dumps(captured_generate_payload, sort_keys=True)
    assert "raw resolved skill metadata" not in serialized_payload
    assert captured_generate_payload["parameters"]["workflow"]["skills"] == {
        "include": [{"name": "jira-implement"}],
    }
    assert captured_generate_payload["evidenceRefs"]["lastStep"]["outputRefs"][
        "stepExecutionManifestRef"
    ] == "artifact://skill-step-manifest"
    assert captured_generate_payload["evidenceRefs"]["lastStep"]["outputRefs"][
        "primaryRef"
    ] == "artifact://agent-run-result"
    assert captured_generate_payload["evidenceRefs"]["lastStep"]["outputRefs"][
        "diagnosticsRef"
    ] == "artifact://diag-step"
