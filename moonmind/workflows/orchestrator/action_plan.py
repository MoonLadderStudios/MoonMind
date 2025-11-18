"""Action plan generation for orchestrator runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from api_service.db import models as db_models

from .service_profiles import ServiceProfile, resolve_service_context


@dataclass(frozen=True)
class PlanStep:
    """Lightweight representation of an orchestrator plan step."""

    name: db_models.OrchestratorPlanStep
    parameters: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name.value, "parameters": dict(self.parameters)}


@dataclass(frozen=True)
class ActionPlan:
    """ActionPlan returned to the orchestrator worker and API."""

    instruction: str
    service: ServiceProfile
    steps: list[PlanStep]
    generated_at: datetime
    generated_by: db_models.OrchestratorPlanOrigin = (
        db_models.OrchestratorPlanOrigin.SYSTEM
    )

    @property
    def service_context(self) -> dict[str, Any]:
        return resolve_service_context(self.service)

    def steps_as_dict(self) -> list[dict[str, Any]]:
        return [step.as_dict() for step in self.steps]


def _build_analyze_step(instruction: str, profile: ServiceProfile) -> PlanStep:
    parameters = {
        "instruction": instruction,
        "service": profile.compose_service,
        "workspace": str(profile.workspace_path),
        "notes": "Review recent logs and dependency manifests for the service.",
        "logArtifact": "analyze.log",
    }
    return PlanStep(db_models.OrchestratorPlanStep.ANALYZE, parameters)


def _build_patch_step(profile: ServiceProfile) -> PlanStep:
    parameters = {
        "service": profile.compose_service,
        "workspace": str(profile.workspace_path),
        "allowlist": list(profile.allowlist_globs),
        "diffArtifact": "patch.diff",
        "commands": [
            ["git", "status", "--short"],
        ],
    }
    return PlanStep(db_models.OrchestratorPlanStep.PATCH, parameters)


def _build_build_step(profile: ServiceProfile) -> PlanStep:
    parameters = {
        "service": profile.compose_service,
        "composeProject": profile.compose_project,
        "logArtifact": "build.log",
        "command": [
            "docker",
            "compose",
            "--project-name",
            profile.compose_project,
            "build",
            profile.compose_service,
        ],
    }
    return PlanStep(db_models.OrchestratorPlanStep.BUILD, parameters)


def _build_restart_step(profile: ServiceProfile) -> PlanStep:
    parameters = {
        "service": profile.compose_service,
        "composeProject": profile.compose_project,
        "logArtifact": "restart.log",
        "command": [
            "docker",
            "compose",
            "--project-name",
            profile.compose_project,
            "up",
            "-d",
            "--no-deps",
            profile.compose_service,
        ],
        "restartTimeoutSeconds": profile.restart_timeout_seconds,
    }
    return PlanStep(db_models.OrchestratorPlanStep.RESTART, parameters)


def _build_verify_step(profile: ServiceProfile) -> PlanStep:
    parameters: dict[str, Any] = {
        "service": profile.compose_service,
        "logArtifact": "verify.log",
    }
    if profile.healthcheck:
        parameters["healthcheck"] = {
            "url": profile.healthcheck.url,
            "method": profile.healthcheck.method,
            "timeoutSeconds": profile.healthcheck.timeout_seconds,
            "intervalSeconds": profile.healthcheck.interval_seconds,
            "expectedStatus": profile.healthcheck.expected_status,
        }
    return PlanStep(db_models.OrchestratorPlanStep.VERIFY, parameters)


def _build_rollback_step(profile: ServiceProfile) -> PlanStep:
    parameters = {
        "service": profile.compose_service,
        "logArtifact": "rollback.log",
        "strategies": [
            {
                "type": "git-revert",
                "commands": [
                    ["git", "reset", "--hard", "HEAD"],
                ],
            },
            {
                "type": "rebuild",
                "commands": [
                    [
                        "docker",
                        "compose",
                        "--project-name",
                        profile.compose_project,
                        "build",
                        profile.compose_service,
                    ],
                    [
                        "docker",
                        "compose",
                        "--project-name",
                        profile.compose_project,
                        "up",
                        "-d",
                        "--no-deps",
                        profile.compose_service,
                    ],
                ],
            },
        ],
    }
    return PlanStep(db_models.OrchestratorPlanStep.ROLLBACK, parameters)


def generate_action_plan(instruction: str, profile: ServiceProfile) -> ActionPlan:
    """Expand ``instruction`` into an orchestrator action plan."""

    normalized = instruction.strip()
    if not normalized:
        raise ValueError("Instruction must not be empty")

    steps: list[PlanStep] = [
        _build_analyze_step(normalized, profile),
        _build_patch_step(profile),
        _build_build_step(profile),
        _build_restart_step(profile),
        _build_verify_step(profile),
        _build_rollback_step(profile),
    ]
    generated_at = datetime.now(tz=timezone.utc)
    return ActionPlan(
        instruction=normalized,
        service=profile,
        steps=steps,
        generated_at=generated_at,
    )


__all__ = ["ActionPlan", "PlanStep", "generate_action_plan"]
