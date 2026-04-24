from datetime import UTC, datetime
from types import SimpleNamespace

from api_service.api.routers.executions import _serialize_execution
from api_service.db.models import MoonMindWorkflowState, TemporalWorkflowType

def test_serialize_execution_includes_repository():
    # Setup a mock execution record
    record = SimpleNamespace(
        namespace="default",
        workflow_id="mm:wf-1",
        run_id="run-1",
        workflow_type=TemporalWorkflowType.RUN,
        state=MoonMindWorkflowState.EXECUTING,
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={"title": "Test Task"},
        artifact_refs=[],
        manifest_ref=None,
        plan_ref=None,
        scheduled_for=None,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        closed_at=None,
        entry="run",
        parameters={},
        owner_id="system",
    )
    
    # Test 1: repository in git_payload (highest precedence)
    record.parameters = {
        "task": {
            "git": {
                "repository": "MoonLadderStudios/GitRepo"
            },
            "repository": "MoonLadderStudios/TaskRepo"
        }
    }
    result = _serialize_execution(record)
    assert result.repository == "MoonLadderStudios/GitRepo"

    # Test 2: repository in task payload
    record.parameters = {
        "task": {
            "repository": "MoonLadderStudios/MoonMind"
        }
    }
    result = _serialize_execution(record)
    assert result.repository == "MoonLadderStudios/MoonMind"

    # Test 3: repository in params directly
    record.parameters = {
        "repository": "MoonLadderStudios/OtherRepo"
    }
    result = _serialize_execution(record)
    assert result.repository == "MoonLadderStudios/OtherRepo"

    # Test 4: legacy 'repo' key in params
    record.parameters = {
        "repo": "MoonLadderStudios/LegacyRepo"
    }
    result = _serialize_execution(record)
    assert result.repository == "MoonLadderStudios/LegacyRepo"

    # Test 5: legacy 'repo' key in task payload
    record.parameters = {
        "task": {
            "repo": "MoonLadderStudios/LegacyTaskRepo"
        }
    }
    result = _serialize_execution(record)
    assert result.repository == "MoonLadderStudios/LegacyTaskRepo"

    # Test 6: search attribute 'mm_repo'
    record.parameters = {}
    record.search_attributes["mm_repo"] = "MoonLadderStudios/SearchRepo"
    result = _serialize_execution(record)
    assert result.repository == "MoonLadderStudios/SearchRepo"

    # Test 7: search attribute 'repository'
    record.search_attributes = {"mm_entry": "run"}
    record.search_attributes["repository"] = "MoonLadderStudios/SearchRepoAttr"
    result = _serialize_execution(record)
    assert result.repository == "MoonLadderStudios/SearchRepoAttr"

    # Test 8: no repository
    record.parameters = {}
    record.search_attributes = {"mm_entry": "run"}
    result = _serialize_execution(record)
    assert result.repository is None

def test_serialize_execution_includes_pr_url_from_memo():
    record = SimpleNamespace(
        namespace="default",
        workflow_id="mm:wf-1",
        run_id="run-1",
        workflow_type=TemporalWorkflowType.RUN,
        state=MoonMindWorkflowState.EXECUTING,
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={
            "title": "Test Task",
            "pull_request_url": "https://github.com/MoonLadderStudios/MoonMind/pull/789",
        },
        artifact_refs=[],
        manifest_ref=None,
        plan_ref=None,
        scheduled_for=None,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        closed_at=None,
        entry="run",
        parameters={},
        owner_id="system",
    )

    result = _serialize_execution(record)

    assert result.pr_url == "https://github.com/MoonLadderStudios/MoonMind/pull/789"

def test_serialize_execution_includes_pr_url_from_legacy_camel_case_memo_key():
    record = SimpleNamespace(
        namespace="default",
        workflow_id="mm:wf-1",
        run_id="run-1",
        workflow_type=TemporalWorkflowType.RUN,
        state=MoonMindWorkflowState.EXECUTING,
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={
            "title": "Test Task",
            "pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/790",
        },
        artifact_refs=[],
        manifest_ref=None,
        plan_ref=None,
        scheduled_for=None,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        closed_at=None,
        entry="run",
        parameters={},
        owner_id="system",
    )

    result = _serialize_execution(record)

    assert result.pr_url == "https://github.com/MoonLadderStudios/MoonMind/pull/790"

def test_serialize_execution_ignores_unsafe_pr_url_sources():
    record = SimpleNamespace(
        namespace="default",
        workflow_id="mm:wf-1",
        run_id="run-1",
        workflow_type=TemporalWorkflowType.RUN,
        state=MoonMindWorkflowState.EXECUTING,
        close_status=None,
        search_attributes={"mm_entry": "run"},
        memo={"title": "Test Task"},
        artifact_refs=[],
        manifest_ref=None,
        plan_ref=None,
        scheduled_for=None,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        closed_at=None,
        entry="run",
        parameters={"pullRequestUrl": "javascript:alert(1)"},
        owner_id="system",
    )

    result = _serialize_execution(record)

    assert result.pr_url is None
