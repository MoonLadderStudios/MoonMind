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
        tmp_path / "frontend/src/generated/openapi.ts",
        'attempts?: components["schemas"]["StepExecutionProjectionModel"][];\n',
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
        "execution-response-legacy-fields",
    }


def test_docs_check_allows_qualified_terms_and_rejects_unqualified_task(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "docs/Temporal/WorkflowExecutionProductModel.md",
        "Temporal Task and Jira task are qualified.\nMoonMind task is not.\n",
    )

    findings = verify_workflow_terminology.run("docs", root=tmp_path)

    assert len(findings) == 1
    assert findings[0].rule == "canonical-doc-unqualified-task"
