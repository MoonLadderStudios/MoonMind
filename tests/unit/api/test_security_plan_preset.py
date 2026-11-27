"""The security plan preset: Create inputs, its three steps and model delivery (#5431)."""

from pathlib import Path

import pytest
import yaml

from api_service.services.assessment_model_delivery import (
    require_execution_model_delivery,
)
from api_service.services.presets.catalog import PresetCatalogService
from moonmind.workflows.executions.execution_contract import workflow_requires_agent
from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
from tests.unit.api.test_presets_service import template_db
from tests.unit.services.test_assessment_model_delivery import source

ROOT = Path(__file__).resolve().parents[3]
SLUG = "security-plan"
STANDARDS = [
    {"id": "owasp-asvs-l1", "edition": "5.0.0"},
    {
        "packId": "operator-policy",
        "edition": "2026",
        "revision": "1",
        "artifactRef": "art_policy",
        "sha256": "c" * 64,
    },
]
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


def test_create_inputs_are_a_subject_type_and_catalog_fed_standards():
    preset = yaml.safe_load(
        (ROOT / f"api_service/data/presets/{SLUG}.yaml").read_text()
    )
    assert {"security", "assessment"} <= set(preset["tags"])
    assert preset["annotations"]["workflowPublish"] == {"mode": "none"}
    schema = preset["annotations"]["inputSchema"]
    assert set(schema["properties"]) == {"subject_type", "standards"}
    # Only subjects an installed journey honours.
    assert schema["properties"]["subject_type"]["enum"] == ["application", "endpoint"]
    standards = schema["properties"]["standards"]
    assert standards["type"] == "array" and standards["minItems"] == 1
    assert standards["x-moonmind-options"] == "framework-packs"


async def test_preset_expands_to_context_planning_and_retain_steps(tmp_path):
    expanded = await expand(tmp_path, subject_type="application", standards=STANDARDS)
    context, planning, retain = expanded["steps"]
    for step, phase in ((context, "context"), (retain, "retain")):
        assert step["type"] == "tool" and step["tool"]["id"] == "security.plan"
        inputs = step["tool"]["inputs"]
        assert inputs["phase"] == phase
        assert inputs["subjectType"] == "application"
        assert inputs["standards"] == STANDARDS
        # A planning run holds no assessment scope, credential or egress grant.
        assert not {"scope", "credentialRefs", "networkRefs", "budget"} & set(inputs)
    assert planning["skill"]["id"] == "security-review"
    # The declared draft path rides the Skill arguments, which Create, rerun
    # and schedules preserve, and reaches the runtime's draft publisher.
    assert planning["skill"]["args"] == {
        "mode": "plan",
        "subject_type": "application",
        "standards": STANDARDS,
        "securityPlanDraftPath": "artifacts/security-plan/draft.json",
    }
    assert planning["repositoryOperation"] == "read"
    assert "untrusted data" in planning["instructions"]
    assert "never wait for an answer" in planning["instructions"]
    assert "securityAssessment" not in (planning.get("annotations") or {})
    assert (ROOT / ".agents/skills/security-review/references/plan.md").is_file()


@pytest.mark.parametrize(
    ("policy", "profile", "message"),
    [
        ({"modelDelivery": "disabled"}, "approved", "Model delivery is disabled"),
        ({"modelDelivery": "local_only"}, "approved", "Local-only model delivery"),
        (
            {"allowedProfileRefs": ["approved"]},
            "other-profile",
            "outside this assessment's admitted Provider Profiles",
        ),
    ],
)
async def test_model_delivery_restriction_blocks_planning_with_its_reason(
    tmp_path, policy, profile, message
):
    expanded = await expand(tmp_path, subject_type="application", standards=STANDARDS)
    parameters = {"workflow": {"steps": expanded["steps"]}, **source(policy).parameters}
    assert workflow_requires_agent(parameters["workflow"], parameters)
    with pytest.raises(TemporalArtifactAuthorizationError, match=message):
        require_execution_model_delivery(parameters, profile_ref=profile)
    if "allowedProfileRefs" in policy:
        # The admitted profile proceeds; there is no fallback provider.
        require_execution_model_delivery(parameters, profile_ref="approved")


async def test_planned_nodes_carry_draft_path_to_the_runtime_and_snapshot_to_tools(
    tmp_path,
):
    from types import SimpleNamespace

    from moonmind.security.assessment_projects import apply_assessment_defaults
    from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
    from tests.unit.security.test_security_plan import snapshot

    expanded = await expand(tmp_path, subject_type="application", standards=STANDARDS)
    workflow = {"instructions": "Plan security.", "steps": expanded["steps"]}
    project = snapshot()
    apply_assessment_defaults(workflow, project)
    plan = _build_runtime_planner()(
        inputs={},
        parameters={"task": workflow},
        snapshot=SimpleNamespace(digest="reg:sha256:test", artifact_ref="art_registry"),
    )
    context, planning, retain = plan["nodes"]
    assert planning["tool"]["type"] == "agent_runtime"
    # run.py forwards this top-level node input to the agent runtime publisher.
    assert (
        planning["inputs"]["securityPlanDraftPath"]
        == "artifacts/security-plan/draft.json"
    )
    assert planning["inputs"]["repositoryOperation"] == "read"
    assert "assessmentProject" not in planning["inputs"]
    for node, phase in ((context, "context"), (retain, "retain")):
        assert node["tool"]["name"] == "security.plan"
        assert node["inputs"]["phase"] == phase
        assert node["inputs"]["assessmentProject"]["digest"] == project.digest
        assert not {"scope", "credentialRefs", "networkRefs"} & set(node["inputs"])
