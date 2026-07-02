from __future__ import annotations

from pathlib import Path
from typing import get_args

from api_service.db.models import MoonMindWorkflowState
from moonmind.schemas.temporal_models import StepLedgerRowModel
from tools import status_domain_audit


def test_backend_workflow_states_match_active_visibility_doc() -> None:
    assert {state.value for state in MoonMindWorkflowState} == (
        status_domain_audit.canonical_workflow_states()
    )


def test_backend_step_ledger_statuses_match_active_step_ledger_doc() -> None:
    status_annotation = StepLedgerRowModel.model_fields["status"].annotation

    assert set(get_args(status_annotation)) == status_domain_audit.canonical_step_statuses()


def test_status_domain_audit_passes_current_repository() -> None:
    assert status_domain_audit.audit_repository() == []


def test_status_domain_audit_fail_on_unknown_mode_rejects_unallowlisted_token() -> None:
    allowed_tokens = status_domain_audit.approved_status_tokens()
    findings = status_domain_audit.audit_text_for_status_tokens(
        'workflow_status_payload = {"status": "surprise_new_status"}\n',
        path=Path("moonmind/workflows/temporal/example.py"),
        allowed_tokens=allowed_tokens,
        require_domain_path=False,
    )

    assert [finding.code for finding in findings] == ["unknown-status-token"]
    assert findings[0].token == "surprise_new_status"

    assert (
        status_domain_audit.audit_text_for_status_tokens(
            'payload = {"status": "running"}\n',
            path=Path("moonmind/workflows/temporal/example.py"),
            allowed_tokens=allowed_tokens,
            require_domain_path=False,
        )
        == []
    )


def test_status_domain_audit_checks_common_status_key_in_domain_files() -> None:
    findings = status_domain_audit.audit_text_for_status_tokens(
        'workflow_status_payload = {"status": "surprise_new_status"}\n',
        path=Path("moonmind/workflows/temporal/example.py"),
        allowed_tokens=status_domain_audit.approved_status_tokens(),
    )

    assert [finding.code for finding in findings] == ["unknown-status-token"]
    assert findings[0].token == "surprise_new_status"


def test_status_domain_audit_recognizes_status_comparisons_and_cases() -> None:
    findings = status_domain_audit.audit_text_for_status_tokens(
        (
            'if (workflowStatus === "surprise_new_status") {\n'
            "  return true;\n"
            "}\n"
            'case "other_surprise_status":\n'
        ),
        path=Path("moonmind/workflows/temporal/example.ts"),
        allowed_tokens=status_domain_audit.approved_status_tokens(),
    )

    assert [finding.token for finding in findings] == [
        "surprise_new_status",
        "other_surprise_status",
    ]


def test_status_domain_audit_allows_recursive_test_globs() -> None:
    findings = status_domain_audit.audit_text_for_status_tokens(
        'payload = {"status": "surprise_new_status"}\n',
        path=Path("frontend/src/components/subfolder/example.test.ts"),
        allowed_tokens=status_domain_audit.approved_status_tokens(),
    )

    assert findings == []


def test_status_domain_audit_prunes_ignored_dirs_relative_to_repo_root(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "var" / "repo"
    audited_dir = repo / "moonmind" / "workflows" / "temporal"
    ignored_dir = repo / "var" / "moonmind" / "workflows" / "temporal"
    audited_dir.mkdir(parents=True)
    ignored_dir.mkdir(parents=True)
    audited_file = audited_dir / "example.py"
    ignored_file = ignored_dir / "ignored.py"
    audited_file.write_text('payload = {"status": "running"}\n', encoding="utf-8")
    ignored_file.write_text('payload = {"status": "running"}\n', encoding="utf-8")

    candidates = {
        path.relative_to(repo).as_posix()
        for path in status_domain_audit._iter_candidate_files(repo)
    }

    assert "moonmind/workflows/temporal/example.py" in candidates
    assert "var/moonmind/workflows/temporal/ignored.py" not in candidates


def test_status_domain_audit_blocks_generic_global_status_vocabulary() -> None:
    findings = status_domain_audit.audit_text_for_status_tokens(
        "GLOBAL_STATUS_VOCABULARY = {'running'}\n",
        path=Path("moonmind/workflows/temporal/example.py"),
        allowed_tokens=status_domain_audit.approved_status_tokens(),
        require_domain_path=False,
    )

    assert [finding.code for finding in findings] == [
        "generic-global-status-vocabulary"
    ]


def test_status_domain_audit_blocks_archived_workflow_status_authority_refs(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs" / "Workflows"
    docs.mkdir(parents=True)
    (docs / "WorkflowStatus.md").write_text("archived pointer\n", encoding="utf-8")
    active = tmp_path / "docs" / "Temporal"
    active.mkdir()
    (active / "example.md").write_text(
        "Use docs/Workflows/WorkflowStatus.md as source\n",
        encoding="utf-8",
    )

    findings = status_domain_audit.audit_archived_workflow_status_refs(tmp_path)

    assert [finding.code for finding in findings] == [
        "archived-workflow-status-authority"
    ]


def test_status_domain_audit_preserves_mm1084_and_mm1073_traceability() -> None:
    traceability = status_domain_audit.STATUS_DOMAIN_AUDIT_TRACEABILITY

    assert traceability["jiraIssue"] == "MM-1084"
    assert traceability["sourceIssue"] == "MM-1073"
    assert "docs/Workflows/WorkflowStatus.md#archived-pointer-004" in traceability[
        "canonicalClaimIds"
    ]
