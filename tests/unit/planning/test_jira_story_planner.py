import logging
import pytest
from unittest.mock import MagicMock, patch

from moonmind.planning import (
    JiraStoryPlanner,
    JiraStoryPlannerError,
    StoryDraft,
)


def test_init_requires_mandatory_fields():
    with pytest.raises(ValueError):
        JiraStoryPlanner(plan_text="", jira_project_key="PROJ")
    with pytest.raises(ValueError):
        JiraStoryPlanner(plan_text="plan", jira_project_key="")


def test_init_loads_credentials(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_KEY", "key")
    monkeypatch.setenv("ATLASSIAN_USERNAME", "user")
    monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")

    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ")

    assert planner.jira_api_key == "key"
    assert planner.jira_username == "user"
    assert planner.jira_url == "https://example.atlassian.net"


def test_build_prompt(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_KEY", "key")
    monkeypatch.setenv("ATLASSIAN_USERNAME", "user")
    monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")

    sample_plan = "Implement login and registration"
    planner = JiraStoryPlanner(plan_text=sample_plan, jira_project_key="PROJ")

    messages = planner._build_prompt(sample_plan)

    from moonmind.schemas.chat_models import Message

    expected_system = (
        "You are a Jira planning assistant. Return ONLY a JSON array of issues "
        "using the fields 'summary', 'description', 'issue_type', 'story_points', "
        "and 'labels'."
    )

    assert messages == [
        Message(role="system", content=expected_system),
        Message(role="user", content=sample_plan),
    ]


def _mock_gemini_response(text: str) -> MagicMock:
    """Helper to create a mock response object for the Google model."""
    part_mock = MagicMock()
    part_mock.text = text
    content_mock = MagicMock()
    content_mock.parts = [part_mock]
    candidate_mock = MagicMock()
    candidate_mock.content = content_mock
    response_mock = MagicMock()
    response_mock.candidates = [candidate_mock]
    return response_mock


def test_call_llm_success(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_KEY", "key")
    monkeypatch.setenv("ATLASSIAN_USERNAME", "user")
    monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")

    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ")
    prompt = planner._build_prompt("plan")

    response_text = (
        '[{"summary": "Add login", "description": "desc", "issue_type": "Story", '
        '"story_points": 3, "labels": ["auth"]}]'
    )
    mock_model = MagicMock()
    mock_model.generate_content.return_value = _mock_gemini_response(response_text)

    with patch(
        "moonmind.planning.jira_story_planner.get_google_model", return_value=mock_model
    ) as mock_factory:
        stories = planner._call_llm(prompt)

    mock_factory.assert_called_once()
    assert stories == [
        StoryDraft(
            summary="Add login",
            description="desc",
            issue_type="Story",
            story_points=3,
            labels=["auth"],
        )
    ]


def test_call_llm_invalid_json(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_KEY", "key")
    monkeypatch.setenv("ATLASSIAN_USERNAME", "user")
    monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")

    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ")
    prompt = planner._build_prompt("plan")

    mock_model = MagicMock()
    mock_model.generate_content.return_value = _mock_gemini_response("not-json")

    with patch(
        "moonmind.planning.jira_story_planner.get_google_model", return_value=mock_model
    ):
        with pytest.raises(JiraStoryPlannerError):
            planner._call_llm(prompt)


def test_call_llm_model_error(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_KEY", "key")
    monkeypatch.setenv("ATLASSIAN_USERNAME", "user")
    monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")

    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ")
    prompt = planner._build_prompt("plan")

    mock_model = MagicMock()
    mock_model.generate_content.side_effect = Exception("boom")

    with patch(
        "moonmind.planning.jira_story_planner.get_google_model", return_value=mock_model
    ):
        with pytest.raises(JiraStoryPlannerError):
            planner._call_llm(prompt)


def test_get_jira_client_mapping(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_KEY", "key")
    monkeypatch.setenv("ATLASSIAN_USERNAME", "user")
    monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")

    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ")

    fake_client = MagicMock()
    with patch("atlassian.Jira", return_value=fake_client) as mock_jira:
        client = planner._get_jira_client(cloud=False)

    mock_jira.assert_called_once_with(
        url="https://example.atlassian.net",
        username="user",
        password="key",
        cloud=False,
        backoff_and_retry=True,
        max_backoff_seconds=16,
        max_backoff_retries=3,
    )
    assert client is fake_client


def test_get_jira_client_auth_error(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_KEY", "key")
    monkeypatch.setenv("ATLASSIAN_USERNAME", "user")
    monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")

    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ")

    fake_client = MagicMock()
    fake_client.myself.side_effect = Exception("401 unauthorized")

    with patch("atlassian.Jira", return_value=fake_client):
        with pytest.raises(JiraStoryPlannerError) as exc:
            planner._get_jira_client()

    assert "Failed to authenticate with Jira" in str(exc.value)


def test_resolve_story_points_field(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_KEY", "key")
    monkeypatch.setenv("ATLASSIAN_USERNAME", "user")
    monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")

    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ")

    fake_jira = MagicMock()
    fake_jira.get_all_fields.return_value = [
        {"id": "customfield_10016", "name": "Story Points", "schema": {"type": "number"}}
    ]

    field_id = planner._resolve_story_points_field(fake_jira)
    assert field_id == "customfield_10016"

    # Should use cached value on subsequent calls
    second_id = planner._resolve_story_points_field(fake_jira)
    assert second_id == "customfield_10016"
    fake_jira.get_all_fields.assert_called_once()


def test_resolve_story_points_field_missing(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_KEY", "key")
    monkeypatch.setenv("ATLASSIAN_USERNAME", "user")
    monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")

    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ")

    fake_jira = MagicMock()
    fake_jira.get_all_fields.return_value = []

    with pytest.raises(JiraStoryPlannerError):
        planner._resolve_story_points_field(fake_jira)


def test_create_issues_dry_run(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_KEY", "key")
    monkeypatch.setenv("ATLASSIAN_USERNAME", "user")
    monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")

    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ", dry_run=True)
    drafts = [
        StoryDraft(summary="s", description="d", issue_type="Task")
    ]

    with patch.object(planner, "_get_jira_client") as mock_client:
        result = planner._create_issues(drafts)

    mock_client.assert_not_called()
    assert result == drafts


def test_create_issues_bulk_success(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_KEY", "key")
    monkeypatch.setenv("ATLASSIAN_USERNAME", "user")
    monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")

    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ", dry_run=False)
    drafts = [
        StoryDraft(summary="A", description="da", issue_type="Task"),
        StoryDraft(summary="B", description="db", issue_type="Task"),
    ]

    fake_jira = MagicMock()
    fake_jira.issue_create_bulk.return_value = {
        "issues": [{"key": "PROJ-1"}, {"key": "PROJ-2"}]
    }

    with patch.object(planner, "_get_jira_client", return_value=fake_jira):
        with patch.object(planner, "_resolve_story_points_field", return_value="sp"):
            result = planner._create_issues(drafts)

    assert [d.key for d in result] == ["PROJ-1", "PROJ-2"]
    fake_jira.issue_create_bulk.assert_called_once()


def test_create_issues_bulk_partial_failure(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_API_KEY", "key")
    monkeypatch.setenv("ATLASSIAN_USERNAME", "user")
    monkeypatch.setenv("ATLASSIAN_URL", "https://example.atlassian.net")

    planner = JiraStoryPlanner(plan_text="plan", jira_project_key="PROJ", dry_run=False)
    drafts = [
        StoryDraft(summary="A", description="da", issue_type="Task"),
        StoryDraft(summary="B", description="db", issue_type="Task"),
    ]

    fake_jira = MagicMock()
    fake_jira.issue_create_bulk.return_value = {"issues": [{"key": "PROJ-1"}, None]}
    fake_jira.create_issue.return_value = {"key": "PROJ-2"}

    with patch.object(planner, "_get_jira_client", return_value=fake_jira):
        with patch.object(planner, "_resolve_story_points_field", return_value="sp"):
            result = planner._create_issues(drafts)

    assert [d.key for d in result] == ["PROJ-1", "PROJ-2"]
    fake_jira.issue_create_bulk.assert_called_once()
    fake_jira.create_issue.assert_called_once()
