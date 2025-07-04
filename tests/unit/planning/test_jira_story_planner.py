import logging
import pytest

from moonmind.planning import JiraStoryPlanner


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

