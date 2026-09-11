from datetime import UTC, datetime
from types import SimpleNamespace

from api_service.api.routers.executions import _serialize_execution
from api_service.db.models import MoonMindWorkflowState, TemporalWorkflowType


def _make_execution_record(*, parameters: dict) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        namespace="default",
        workflow_id="mm:wf-1",
        run_id="run-1",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        state=MoonMindWorkflowState.EXECUTING,
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={"title": "MM-1211 branch projection"},
        artifact_refs=[],
        manifest_ref=None,
        plan_ref=None,
        scheduled_for=None,
        created_at=now,
        started_at=now,
        updated_at=now,
        closed_at=None,
        entry="run",
        parameters=parameters,
        owner_id="system",
    )


def test_serialize_execution_projects_git_branch_as_starting_branch():
    record = _make_execution_record(
        parameters={
            "workflow": {"git": {"branch": "agent/mm-38abc67f-recovered"}}
        }
    )

    result = _serialize_execution(record)

    assert result.starting_branch == "agent/mm-38abc67f-recovered"
    assert (
        result.model_dump(by_alias=True)["startingBranch"]
        == "agent/mm-38abc67f-recovered"
    )


def test_explicit_git_starting_branch_keeps_precedence_over_git_branch():
    record = _make_execution_record(
        parameters={
            "workflow": {
                "git": {
                    "startingBranch": "legacy-explicit-base",
                    "branch": "canonical-authored-branch",
                }
            }
        }
    )

    assert _serialize_execution(record).starting_branch == "legacy-explicit-base"


def test_whitespace_git_starting_branch_does_not_mask_git_branch():
    record = _make_execution_record(
        parameters={
            "workflow": {
                "git": {
                    "startingBranch": " ",
                    "branch": "agent/mm-38abc67f-recovered",
                }
            }
        }
    )

    assert (
        _serialize_execution(record).starting_branch
        == "agent/mm-38abc67f-recovered"
    )


def test_git_context_without_authored_branch_keeps_default_fallback():
    record = _make_execution_record(
        parameters={"workflow": {"git": {"defaultBranch": "develop"}}}
    )

    assert _serialize_execution(record).starting_branch == "develop (default)"


def test_git_context_without_recorded_default_does_not_fabricate_main():
    # MoonLadderStudios/MoonMind#4228: missing historical metadata must stay
    # missing so the UI can render an explicit "Not recorded" state.
    record = _make_execution_record(
        parameters={"workflow": {"git": {"repository": "acme/repo"}}}
    )

    assert _serialize_execution(record).starting_branch is None


def test_canonical_repository_target_projects_source_context():
    # MoonLadderStudios/MoonMind#4228: the Create page persists repository and
    # branch only in the structured top-level payload.repository mapping.
    record = _make_execution_record(
        parameters={
            "repository": {
                "provider": "git",
                "connectionRef": "repository-connection:git-default",
                "repository": {"name": "acme/repo"},
                "branch": {"name": "feature/source-context"},
            }
        }
    )

    result = _serialize_execution(record)

    assert result.repository == "acme/repo"
    assert result.starting_branch == "feature/source-context"


def test_canonical_repository_target_without_branch_projects_repository_only():
    record = _make_execution_record(
        parameters={
            "repository": {
                "provider": "git",
                "connectionRef": "repository-connection:git-default",
                "repository": {"name": "acme/repo"},
            }
        }
    )

    result = _serialize_execution(record)

    assert result.repository == "acme/repo"
    assert result.starting_branch is None
