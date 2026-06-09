from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[3] / "tools" / "verify_workflow_terminology.py"
SPEC = importlib.util.spec_from_file_location("verify_workflow_terminology", MODULE_PATH)
assert SPEC is not None
verify_workflow_terminology = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = verify_workflow_terminology
SPEC.loader.exec_module(verify_workflow_terminology)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_mm731_terminology_check_passes_current_repository() -> None:
    assert verify_workflow_terminology.run("all") == []


def test_runtime_check_rejects_attempt_route_and_legacy_ui_copy(tmp_path: Path) -> None:
    _write(
        tmp_path / "api_service/api/routers/executions.py",
        '@router.get("/{workflow_id}/steps/{logical_step_id}/attempts")\n',
    )
    _write(
        tmp_path / "frontend/src/entrypoints/workflow-start.tsx",
        'const label = "Create Task";\n',
    )
    _write(
        tmp_path / "frontend/src/entrypoints/workflow-list.tsx",
        'const row = item.taskId || item.workflowId;\n'
        'params.set("scope", "tasks");\n',
    )
    _write(
        tmp_path / "api_service/api/routers/task_runs.py",
        'raise HTTPException(detail="Invalid task run id")\n',
    )
    _write(
        tmp_path / "moonmind/schemas/temporal_models.py",
        'TASK_RUN_ID_MEMO_KEYS = ("taskRunId",)\n'
        'task_skills = Field(None, alias="taskSkills")\n',
    )
    _write(
        tmp_path / "frontend/src/generated/openapi.ts",
        'attempts?: components["schemas"]["StepExecutionProjectionModel"][];\n'
        'taskInputSnapshot?: components["schemas"]["TaskInputSnapshotDescriptorModel"];\n',
    )
    _write(
        tmp_path / "tests/unit/api/test_executions_temporal.py",
        'BANNED_EXECUTION_RESPONSE_KEYS = {"taskId", "taskRunId", "taskStatus"}\n',
    )
    _write(
        tmp_path / "tests/contract/test_temporal_execution_api.py",
        'BANNED_EXECUTION_SCHEMA_FIELDS = {"taskId", "taskRunId", "taskStatus"}\n',
    )

    findings = verify_workflow_terminology.run("runtime", root=tmp_path)

    assert {finding.rule for finding in findings} == {
        "execution-attempt-route",
        "execution-attempt-schema-field",
        "legacy-ui-copy",
        "workflow-list-task-compatibility",
        "agent-run-router-task-run-copy",
        "execution-schema-task-public-fields",
        "generated-execution-task-public-fields",
        "execution-response-legacy-fields",
    }


def test_required_guard_sets_validate_assigned_literals(tmp_path: Path) -> None:
    _write(
        tmp_path / "tests/unit/api/test_executions_temporal.py",
        'BANNED_EXECUTION_RESPONSE_KEYS = {"taskId"}\n'
        'assert "attempt" in some_other_guard\n',
    )
    _write(
        tmp_path / "tests/contract/test_temporal_execution_api.py",
        "BANNED_EXECUTION_SCHEMA_FIELDS = {\n"
        "    'attempt',\n"
        "    'attempts',\n"
        "    'stepAttemptId',\n"
        "    'taskId',\n"
        "    'taskRunId',\n"
        "    'taskStatus',\n"
        "}\n",
    )

    findings = verify_workflow_terminology.run("runtime", root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule == "execution-response-legacy-fields"
    assert findings[0].path == Path("tests/unit/api/test_executions_temporal.py")
    assert "attempt" in findings[0].line


def test_docs_check_allows_qualified_terms_and_rejects_unqualified_task(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "docs/Temporal/WorkflowExecutionProductModel.md",
        "Temporal Task and Jira task are qualified.\nMoonMind task is not.\n",
    )
    _write(
        tmp_path / "docs/Api/ExecutionsApiContract.md",
        "The bridge rule is taskId == workflowId and refs.taskRunId is returned.\n",
    )
    _write(
        tmp_path / "docs/Temporal/WorkflowLanguageHardSwitchPlan.md",
        "Step Execution replaces Step Execution.\n",
    )

    findings = verify_workflow_terminology.run("docs", root=tmp_path)

    assert {finding.rule for finding in findings} == {
        "canonical-doc-unqualified-task",
        "execution-api-contract-task-compatibility",
        "hard-switch-plan-tautology",
    }


def test_docs_check_rejects_unqualified_term_on_line_with_allowed_term(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "docs/Temporal/WorkflowExecutionProductModel.md",
        "Temporal Task is qualified, but MoonMind task is not.\n",
    )

    findings = verify_workflow_terminology.run("docs", root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule == "canonical-doc-unqualified-task"
