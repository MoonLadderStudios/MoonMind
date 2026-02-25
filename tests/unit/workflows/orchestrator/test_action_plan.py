"""Unit tests for orchestrator action plan builders."""

from __future__ import annotations

from pathlib import Path

from api_service.db import models as db_models
from moonmind.workflows.orchestrator.action_plan import generate_skill_action_plan
from moonmind.workflows.orchestrator.service_profiles import ServiceProfile


def test_generate_skill_action_plan_includes_rollback_step() -> None:
    profile = ServiceProfile(
        key="orchestrator",
        compose_service="orchestrator",
        workspace_path=Path("/workspace/MoonMind"),
        allowlist_globs=("**",),
        compose_project="moonmind",
    )

    plan = generate_skill_action_plan(
        "run skill",
        profile,
        skill_id="update-moonmind",
        skill_args={"repo": "."},
    )

    assert [step.name for step in plan.steps] == [
        db_models.OrchestratorPlanStep.ANALYZE,
        db_models.OrchestratorPlanStep.BUILD,
        db_models.OrchestratorPlanStep.VERIFY,
        db_models.OrchestratorPlanStep.ROLLBACK,
    ]


def test_generate_skill_action_plan_applies_default_compose_project_for_update_moonmind() -> None:
    profile = ServiceProfile(
        key="orchestrator",
        compose_service="orchestrator",
        workspace_path=Path("/workspace/MoonMind"),
        allowlist_globs=("**",),
        compose_project="moonmind",
    )

    plan = generate_skill_action_plan(
        "run skill",
        profile,
        skill_id="update-moonmind",
        skill_args={"repo": "."},
    )

    build_step = plan.steps[1]
    assert build_step.parameters["command"] == [
        "python",
        "-m",
        "moonmind.workflows.orchestrator.skill_executor",
        "--skill-id",
        "update-moonmind",
        "--skill-args-json",
        '{"composeProject":"moonmind","repo":"."}',
    ]
