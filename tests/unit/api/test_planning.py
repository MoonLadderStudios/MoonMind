from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.planning import router as planning_router
from moonmind.planning import JiraStoryPlannerError, StoryDraft

app = FastAPI()
app.include_router(planning_router, prefix="/v1/planning")
client = TestClient(app)


def test_plan_jira_stories_success():
    draft = StoryDraft(summary="s", description="d", issue_type="Task")
    planner_instance = MagicMock()
    planner_instance.plan.return_value = [draft]
    with patch(
        "api_service.api.routers.planning.JiraStoryPlanner",
        return_value=planner_instance,
    ) as mock_cls:
        response = client.post(
            "/v1/planning/jira",
            json={"plan_text": "do work", "jira_project_key": "PROJ", "dry_run": True},
        )
    assert response.status_code == 200
    assert response.json() == [draft.model_dump()]
    mock_cls.assert_called_once_with(
        plan_text="do work", jira_project_key="PROJ", dry_run=True
    )
    planner_instance.plan.assert_called_once()


def test_plan_jira_stories_error():
    planner_instance = MagicMock()
    planner_instance.plan.side_effect = JiraStoryPlannerError("boom")
    with patch(
        "api_service.api.routers.planning.JiraStoryPlanner",
        return_value=planner_instance,
    ):
        response = client.post(
            "/v1/planning/jira",
            json={"plan_text": "do work", "jira_project_key": "PROJ", "dry_run": False},
        )
    assert response.status_code == 500
    assert "boom" in response.json()["detail"]
