"""The policy-pack drafting preset and its evidence-processing admission."""

from pathlib import Path

import pytest

from api_service.services.assessment_model_delivery import (
    require_execution_model_delivery,
)
from api_service.services.presets.catalog import PresetCatalogService
from moonmind.workflows.executions.execution_contract import workflow_requires_agent
from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
from tests.unit.api.test_presets_service import template_db
from tests.unit.services.test_assessment_model_delivery import source

ROOT = Path(__file__).resolve().parents[3]
SLUG = "security-policy-pack"
pytestmark = pytest.mark.asyncio


async def expand(tmp_path, **inputs):
    async with template_db(tmp_path) as maker, maker() as session:
        seeds = tmp_path / "seeds"
        seeds.mkdir()
        preset = ROOT / f"api_service/data/presets/{SLUG}.yaml"
        (seeds / preset.name).write_text(preset.read_text())
        service = PresetCatalogService(session)
        await service.import_seed_templates(seed_dir=seeds)
        return await service.expand_template(slug=SLUG, inputs=inputs)


async def test_preset_expands_to_one_skill_step_with_policy_identity(tmp_path):
    expanded = await expand(
        tmp_path,
        policy_name="Acme Information Security Policy",
        policy_owner="Security team",
        policy_version="3.0",
    )
    (step,) = expanded["steps"]
    assert step["skill"]["id"] == "security-policy-pack"
    args = step["skill"]["args"]
    assert args["policy_name"] == "Acme Information Security Policy"
    assert args["policy_owner"] == "Security team"
    assert args["policy_version"] == "3.0"
    assert "untrusted data" in step["instructions"]
    assert (ROOT / ".agents/skills/security-policy-pack/SKILL.md").is_file()


@pytest.mark.parametrize(
    ("policy", "profile", "message"),
    [
        ({"modelDelivery": "disabled"}, "approved", "Model delivery is disabled"),
        (
            {"allowedProfileRefs": ["approved"]},
            "other-profile",
            "outside this assessment's admitted Provider Profiles",
        ),
        (
            {"allowedProfileRefs": ["approved"]},
            None,
            "cannot verify an admitted Provider Profile",
        ),
    ],
)
async def test_saved_environment_stops_drafting_before_any_agent_launch(
    tmp_path, policy, profile, message
):
    expanded = await expand(
        tmp_path, policy_name="Policy", policy_owner="Owner", approved_on="2026-11-01"
    )
    parameters = {
        "workflow": {"steps": expanded["steps"]},
        **source(policy).parameters,
    }
    # The drafting step is an agent step, so the plan-admission gate applies
    # before a provider session exists or any attachment is delivered.
    assert workflow_requires_agent(parameters["workflow"], parameters)
    with pytest.raises(TemporalArtifactAuthorizationError, match=message):
        require_execution_model_delivery(parameters, profile_ref=profile)
    if "allowedProfileRefs" in policy:
        require_execution_model_delivery(parameters, profile_ref="approved")
