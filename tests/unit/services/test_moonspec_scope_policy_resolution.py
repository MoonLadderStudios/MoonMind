"""Source classification entrypoints receive the portable acceptance owner."""

from pathlib import Path

import pytest

from moonmind.schemas.agent_skill_models import SkillSelector
from moonmind.services.skill_resolution import (
    AgentSkillResolver,
    BuiltInSkillLoader,
    SkillResolutionContext,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["moonspec-assess", "moonspec-specify"])
async def test_source_classification_resolves_its_acceptance_policy(entrypoint):
    skills = Path(__file__).resolve().parents[3] / ".agents" / "skills"
    resolved = await AgentSkillResolver(
        loaders=[BuiltInSkillLoader(skills_root=skills)]
    ).resolve(
        SkillSelector(include=[{"name": entrypoint}]),
        SkillResolutionContext(snapshot_id="source-classification-policy"),
    )

    by_name = {skill.skill_name: skill for skill in resolved.skills}
    assert set(by_name) == {entrypoint, "moonspec-verify"}
    policy_owner = by_name["moonspec-verify"]
    assert policy_owner.selection_reason == "required"
    assert policy_owner.required_by == [entrypoint]
    assert (
        Path(policy_owner.provenance.source_path) / "references/acceptance-policy.md"
    ).is_file()
