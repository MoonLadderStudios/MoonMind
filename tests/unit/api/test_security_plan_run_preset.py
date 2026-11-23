"""Running a retained security plan: one shared expansion bound to its digest (#5432)."""

from pathlib import Path

import pytest

from api_service.services import security_plan_runs
from api_service.services.presets.catalog import (
    PresetCatalogService,
    PresetValidationError,
)
from moonmind.security.security_plan import plan_identity
from moonmind.security.security_plan_run import OUTCOME_PATH
from tests.unit.api.test_presets_service import template_db
from tests.unit.security.test_security_plan import ORIGIN, hand_authored, validate

ROOT = Path(__file__).resolve().parents[3]
SLUGS = (
    "security-plan-run",
    "security-code-supply-chain",
    "security-image-sbom",
    "security-web-api",
    "security-aws-posture",
)
pytestmark = pytest.mark.asyncio


READS = []


@pytest.fixture
def plan(monkeypatch):
    plan = validate(hand_authored())
    reads = READS
    reads.clear()

    async def load(session, selection, access):
        reads.append((selection.plan_ref, selection.identity, access))
        if selection.identity != plan_identity(plan):
            from moonmind.security.security_plan import SecurityPlanError

            raise SecurityPlanError(
                ["securityPlan.identity: differs from the retained plan's content"]
            )
        return plan

    monkeypatch.setattr(security_plan_runs, "load_retained_plan", load)
    return plan


def selection(plan):
    return {"planRef": "art_plan", "identity": plan_identity(plan)}


async def expand(tmp_path, access="operator-access", **inputs):
    tmp_path.mkdir(parents=True, exist_ok=True)
    async with template_db(tmp_path) as maker, maker() as session:
        seeds = tmp_path / "seeds"
        seeds.mkdir(exist_ok=True)
        for slug in SLUGS:
            preset = ROOT / f"api_service/data/presets/{slug}.yaml"
            (seeds / preset.name).write_text(preset.read_text())
        service = PresetCatalogService(session, artifact_access=access)
        await service.import_seed_templates(seed_dir=seeds)
        return await service.expand_template(slug="security-plan-run", inputs=inputs)


async def test_plan_jobs_expand_to_their_existing_presets_and_one_conclusion(
    tmp_path, plan
):
    expanded = await expand(
        tmp_path, security_plan=selection(plan), assessment_ref="shop-assessment"
    )
    code, web, conclusion = expanded["steps"]
    # Each planned job is its existing technical preset with the plan's input,
    # so it keeps that preset's compiled job, readiness hint and admission.
    assert code["source"]["presetSlug"] == "security-code-supply-chain"
    assert web["source"]["presetSlug"] == "security-web-api"
    code_hint = code["annotations"]["securityAssessment"]
    web_hint = web["annotations"]["securityAssessment"]
    assert (code_hint["kind"], code_hint["planJobId"]) == (
        "repository-scan",
        "code-scan",
    )
    assert (web_hint["kind"], web_hint["planJobId"]) == (
        "web-assessment",
        "web-baseline",
    )
    # The preset's own input contract normalizes the plan's input; the operator's
    # launch-bound reference is the only addition.
    assert (
        code_hint["admission"].items()
        >= {"scanner": "gitleaks", "outputDirectory": "scan"}.items()
    )
    assert (
        web_hint["admission"].items()
        >= {
            **plan.jobs[1].input,
            "assessmentRef": "shop-assessment",
        }.items()
    )
    assert web_hint["jobSpec"]["webAssessment"]["origin"] == ORIGIN
    assert web["skill"]["args"]["securityPlanJobOutcomePath"] == OUTCOME_PATH.format(
        job_id="web-baseline"
    )
    assert "planned job web-baseline" in web["instructions"]
    # Deterministic work acquires no model capacity and stays read-only.
    assert code["repositoryOperation"] == web["repositoryOperation"] == "read"
    assert (
        conclusion["type"] == "tool" and conclusion["tool"]["id"] == "security.assess"
    )
    inputs = conclusion["tool"]["inputs"]
    assert inputs["securityPlan"] == {"planRef": "art_plan"}
    assert inputs["planRun"] == {
        "identity": plan_identity(plan),
        "jobs": [
            {"jobId": "code-scan", "kind": "repository-scan", "stepIds": [code["id"]]},
            {"jobId": "web-baseline", "kind": "web-assessment", "stepIds": [web["id"]]},
        ],
        "skipped": [],
    }
    # One multi-pack request over every selected standard.
    assert [pack["packId"] for pack in inputs["frameworkPacks"]] == [
        "synthetic-app",
        "synthetic-ops",
    ]
    assert inputs["scope"] == plan.subject.scope.model_dump(mode="json", by_alias=True)
    assert inputs["credentialRefs"] == inputs["networkRefs"] == []
    assert expanded["publish"] == {"mode": "none"}
    assert READS == [("art_plan", plan_identity(plan), "operator-access")]


async def test_same_plan_expands_identically_and_a_changed_plan_does_not(
    tmp_path, plan
):
    first = await expand(
        tmp_path / "a", security_plan=selection(plan), assessment_ref="shop-assessment"
    )
    again = await expand(
        tmp_path / "b", security_plan=selection(plan), assessment_ref="shop-assessment"
    )
    strip = lambda steps: [  # noqa: E731
        {k: v for k, v in step.items() if k != "presetProvenance"} for step in steps
    ]
    assert strip(first["steps"]) == strip(again["steps"])
    with pytest.raises(PresetValidationError, match="identity"):
        await expand(
            tmp_path / "c",
            security_plan={"planRef": "art_plan", "identity": "0" * 64},
            assessment_ref="shop-assessment",
        )


async def test_uncovered_work_is_excluded_with_its_reason(tmp_path, plan):
    with pytest.raises(PresetValidationError, match="web-baseline needs it"):
        await expand(tmp_path / "a", security_plan=selection(plan))
    reason = "The selected authorization does not admit https://app.example.test."
    expanded = await expand(
        tmp_path / "b",
        security_plan=selection(plan),
        exclude_jobs=[{"jobId": "web-baseline", "reason": reason}],
    )
    code, conclusion = expanded["steps"]
    assert code["annotations"]["securityAssessment"]["planJobId"] == "code-scan"
    assert conclusion["tool"]["inputs"]["planRun"]["skipped"] == [
        {
            "jobId": "web-baseline",
            "kind": "web-assessment",
            "state": "blocked_admission",
            "reason": reason,
        }
    ]
    with pytest.raises(PresetValidationError, match="not a job of this plan"):
        await expand(
            tmp_path / "c",
            security_plan=selection(plan),
            exclude_jobs=[{"jobId": "nope"}],
        )


async def test_previous_run_is_the_conclusions_baseline(tmp_path, plan):
    expanded = await expand(
        tmp_path,
        security_plan=selection(plan),
        assessment_ref="shop-assessment",
        previous_assessment_ref="art_previous",
    )
    inputs = expanded["steps"][-1]["tool"]["inputs"]
    assert inputs["previousAssessmentRef"] == "art_previous"


async def test_expansion_without_operator_artifact_access_is_refused(tmp_path):
    with pytest.raises(PresetValidationError, match="operator's artifact access"):
        await expand(
            tmp_path,
            access=None,
            security_plan={"planRef": "art_plan", "identity": "a" * 64},
        )
