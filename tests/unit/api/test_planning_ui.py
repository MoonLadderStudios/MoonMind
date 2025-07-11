import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from api_service.api.routers import planning as planning_router
from moonmind.planning import StoryDraft

app = FastAPI()
app.include_router(planning_router.router, prefix="/v1/planning")
client = TestClient(app)

# Mock templates like in profile tests
mock_templates = AsyncMock()

def template_side_effect(name, context, status_code=200, headers=None):
    return planning_router.HTMLResponse(
        content=f"<html>{name}</html>", status_code=status_code, headers=headers
    )

mock_templates.TemplateResponse = MagicMock(side_effect=template_side_effect)
planning_router.templates = mock_templates


def test_get_planner_page():
    response = client.get("/v1/planning/jira/ui")
    assert response.status_code == 200
    mock_templates.TemplateResponse.assert_called_once()
    args, kwargs = mock_templates.TemplateResponse.call_args
    assert args[0] == "planning.html"
    context = args[1]
    assert context["result"] is None


def test_post_planner_page_success():
    draft = StoryDraft(summary="s", description="d", issue_type="Task")
    planner_instance = MagicMock()
    planner_instance.plan.return_value = [draft]
    with patch("api_service.api.routers.planning.JiraStoryPlanner", return_value=planner_instance):
        response = client.post(
            "/v1/planning/jira/ui",
            data={
                "plan_text": "do work",
                "jira_project_key": "PROJ",
                "dry_run": "on",
            },
        )
    assert response.status_code == 200
    mock_templates.TemplateResponse.assert_called()
    planner_instance.plan.assert_called_once()
