import pytest
from unittest.mock import patch, MagicMock

from moonmind.planning import JiraStoryPlanner, JiraStoryPlannerError, StoryDraft


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_KEY", "key")
    monkeypatch.setenv("ATLASSIAN_USERNAME", "user")
    monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")


def test_plan_valid_flow(monkeypatch):
    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ", dry_run=False)
    draft = StoryDraft(summary="A", description="da", issue_type="Task")

    with patch.object(planner, "_call_llm", return_value=[draft]) as mock_llm, \
         patch.object(planner, "_create_issues", return_value=[draft]) as mock_create:
        result = planner.plan()

    assert result == [draft]
    mock_llm.assert_called_once()
    mock_create.assert_called_once_with([draft])


def test_plan_invalid_json(monkeypatch):
    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ")

    with patch.object(planner, "_call_llm", side_effect=JiraStoryPlannerError("invalid json")):
        with pytest.raises(JiraStoryPlannerError):
            planner.plan()


def test_plan_auth_error(monkeypatch):
    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ", dry_run=False)
    draft = StoryDraft(summary="s", description="d", issue_type="Task")

    with patch.object(planner, "_call_llm", return_value=[draft]), \
         patch.object(planner, "_get_jira_client", side_effect=JiraStoryPlannerError("auth")):
        with pytest.raises(JiraStoryPlannerError):
            planner.plan()


def test_plan_duplicate_summaries(monkeypatch):
    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ")
    draft1 = StoryDraft(summary="A", description="d1", issue_type="Task")
    draft2 = StoryDraft(summary="A", description="d2", issue_type="Task")

    with patch.object(planner, "_call_llm", return_value=[draft1, draft2]):
        with patch.object(planner, "_create_issues", return_value=[draft1, draft2]):
            result = planner.plan()

    assert result == [draft1, draft2]


def test_plan_dry_run(monkeypatch):
    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ", dry_run=True)
    draft = StoryDraft(summary="A", description="d", issue_type="Task")

    with patch.object(planner, "_call_llm", return_value=[draft]) as mock_llm, \
         patch.object(planner, "_get_jira_client", side_effect=AssertionError("should not auth")):
        result = planner.plan()

    assert result == [draft]
    mock_llm.assert_called_once()
