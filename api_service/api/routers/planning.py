import logging
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from moonmind.planning import JiraStoryPlanner, JiraStoryPlannerError, StoryDraft

router = APIRouter()


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

