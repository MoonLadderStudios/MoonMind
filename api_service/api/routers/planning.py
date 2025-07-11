import json
import logging
from typing import List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from moonmind.planning import (
    JiraStoryPlanner,
    JiraStoryPlannerError,
    StoryDraft,
)

router = APIRouter()

TEMPLATES_DIR = "api_service/templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


class JiraPlanRequest(BaseModel):
    plan_text: str = Field(..., description="Description of the work to plan")
    jira_project_key: str = Field(..., description="Jira project key")
    dry_run: bool = Field(True, description="If true, do not create issues")


@router.post("/jira", response_model=List[StoryDraft])
async def plan_jira_stories(request: JiraPlanRequest):
    """Generate Jira stories from plan text using the JiraStoryPlanner."""
    planner = JiraStoryPlanner(
        plan_text=request.plan_text,
        jira_project_key=request.jira_project_key,
        dry_run=request.dry_run,
    )
    try:
        return planner.plan()
    except JiraStoryPlannerError as exc:
        logging.getLogger(__name__).exception("Jira planning failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/jira/ui", response_class=HTMLResponse, name="jira_planner_ui")
async def get_jira_planner_page(request: Request):
    """Render the Jira planning form."""
    return templates.TemplateResponse(
        "planning.html",
        {
            "request": request,
            "result": None,
            "message": None,
        },
    )


@router.post("/jira/ui", response_class=HTMLResponse, name="jira_planner_submit")
async def handle_jira_planner_form(request: Request):
    """Handle Jira planning form submission."""
    form_data = await request.form()
    plan_text = form_data.get("plan_text", "")
    jira_project_key = form_data.get("jira_project_key", "")
    dry_run = form_data.get("dry_run") not in (None, "", "false", "off")

    message = None
    result = None

    if not plan_text or not jira_project_key:
        message = "plan_text and jira_project_key are required."
    else:
        planner = JiraStoryPlanner(
            plan_text=plan_text, jira_project_key=jira_project_key, dry_run=dry_run
        )
        try:
            drafts = planner.plan()
            result = json.dumps([d.model_dump() for d in drafts], indent=2)
            message = "Planning successful."
        except JiraStoryPlannerError as exc:
            logging.getLogger(__name__).exception("Jira planning failed: %s", exc)
            message = f"Planning failed: {exc}"

    return templates.TemplateResponse(
        "planning.html",
        {"request": request, "result": result, "message": message},
    )
