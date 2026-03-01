"""Regression coverage for seeded task template strategy alignment."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _load_speckit_orchestrate_seed() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[3]
    seed_path = (
        repo_root / "api_service" / "data" / "task_step_templates" / "speckit-orchestrate.yaml"
    )
    document = yaml.safe_load(seed_path.read_text(encoding="utf-8")) or {}
    assert isinstance(document, dict)
    return document


def test_seed_required_capabilities_are_runtime_neutral() -> None:
    document = _load_speckit_orchestrate_seed()
    assert document["requiredCapabilities"] == ["git"]


def test_seed_shape_fields_required_for_alignment_migration() -> None:
    document = _load_speckit_orchestrate_seed()

    assert document.get("slug") == "speckit-orchestrate"
    assert document.get("scope") == "global"
    assert document.get("version") == "1.0.0"

    required_capabilities = document.get("requiredCapabilities")
    assert isinstance(required_capabilities, list) and required_capabilities

    steps = document.get("steps")
    assert isinstance(steps, list) and steps

    all_instructions = "\n".join(
        str(step.get("instructions") or "")
        for step in steps
        if isinstance(step, dict)
    )
    assert "--mode {{ inputs.orchestration_mode }}" in all_instructions


def test_seed_final_step_defers_publish_actions_to_wrapper_stage() -> None:
    document = _load_speckit_orchestrate_seed()
    steps = document.get("steps") or []
    assert isinstance(steps, list) and steps

    final_step = steps[-1]
    assert isinstance(final_step, dict)
    instructions = str(final_step.get("instructions") or "")

    assert "Do NOT create commits, push branches, or open pull requests" in instructions
    assert "publish stage can handle commit/PR behavior" in instructions
    assert "Create a commit for completed changes" not in instructions
    assert "Create a pull request with concise scope/remediation/test summary" not in instructions


def test_seed_final_step_does_not_require_github_capability() -> None:
    document = _load_speckit_orchestrate_seed()
    steps = document.get("steps") or []
    final_step = steps[-1]
    skill = final_step.get("skill") or {}
    assert isinstance(skill, dict)
    assert "requiredCapabilities" not in skill
