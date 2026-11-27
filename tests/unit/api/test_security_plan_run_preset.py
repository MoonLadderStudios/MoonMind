"""Running a retained security plan through the one preset compiler (#5432).

The ``security-plan-run`` preset reads a retained ``security.plan`` artifact
with the caller's authority and derives its steps through the existing
include path: each launchable planned job becomes its technical preset's step
with the plan's input, followed by one concluding ``security.assess``. Create
(the expand route) and the API (``taskTemplate``) reach the same expansion,
which the rules-of-engagement review binds by content.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from api_service.services.assessment_engagements import build_assessment_proposal
from api_service.services.presets.catalog import (
    ExpandOptions,
    PresetCatalogService,
    PresetValidationError,
)
from moonmind.config.settings import settings
from moonmind.security.assessment_authorizations import AssessmentAuthorizations
from moonmind.security.security_plan import parse_security_plan, plan_identity
from moonmind.workflows.executions.preset_expansion import expand_preset_for_child_run
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.artifact_access import access_for_test
from tests.unit.api.test_presets_service import template_db
from tests.unit.security.test_assessment_authorizations import authorization, saved
from tests.unit.security.test_security_plan import ORIGIN, hand_authored, source

ROOT = Path(__file__).resolve().parents[3]
SLUG = "security-plan-run"
PRESETS = (
    SLUG,
    "security-code-supply-chain",
    "security-image-sbom",
    "security-web-api",
    "security-aws-posture",
)
CONTEXT = {"targetRuntime": "codex_cli"}
pytestmark = pytest.mark.asyncio


def operator():
    return access_for_test("operator")


def plan_document(**changes):
    raw = hand_authored()
    raw["jobs"].append(
        {
            "id": "image-sbom",
            "kind": "image-assessment",
            "input": {
                "operation": "sbom-generate",
                "target": "archive",
                "inputArtifactRef": "art_openapi",
            },
            "assets": ["shop-app"],
            "sources": source("environment_field", "environments.prod.artifactRefs"),
        }
    )
    for key, value in changes.items():
        raw[key] = value
    return raw


@pytest.fixture
def artifact_root(tmp_path, monkeypatch):
    root = tmp_path / "artifact-store"
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(settings.workflow, "temporal_artifact_root", str(root))
    return root


async def retain(session, raw, *, link_type="security.plan"):
    """Store a plan as the planning run's security.plan tool would."""
    service = TemporalArtifactService(TemporalArtifactRepository(session))
    artifact, _ = await service.put_content_addressed_payload_complete(
        principal="system:security-assessment",
        payload=json.dumps(raw).encode(),
        content_type="application/json",
        scope="security-assessments",
        link={
            "namespace": "default",
            "workflow_id": "planning-run",
            "run_id": "run-1",
            "link_type": link_type,
        },
        metadata_json={"artifact_type": "security_assessment"},
    )
    return artifact.artifact_id


async def seeded(session, tmp_path):
    seeds = tmp_path / "seeds"
    seeds.mkdir(exist_ok=True)
    for slug in PRESETS:
        preset = ROOT / f"api_service/data/presets/{slug}.yaml"
        (seeds / preset.name).write_text(preset.read_text())
    service = PresetCatalogService(session)
    await service.import_seed_templates(seed_dir=seeds)
    return service


async def expand(service, inputs, access="operator"):
    return await service.expand_template(
        slug=SLUG,
        inputs={"assessment_ref": "client-review", **inputs},
        context=dict(CONTEXT),
        options=ExpandOptions(
            artifact_access=operator() if access == "operator" else access
        ),
    )


def comparable(steps):
    """Steps as executed: ids, kinds, instructions, tools, Skill args, annotations."""
    keys = ("id", "type", "title", "instructions", "tool", "skill", "annotations")
    return [{key: step.get(key) for key in keys} for step in steps]


async def test_plan_expands_to_its_jobs_presets_and_one_conclusion(
    tmp_path, artifact_root
):
    async with template_db(tmp_path) as maker, maker() as session:
        raw = plan_document()
        ref = await retain(session, raw)
        identity = plan_identity(parse_security_plan(json.dumps(raw)))
        service = await seeded(session, tmp_path)
        expanded = await expand(service, {"plan_ref": ref})
    *jobs, conclusion = expanded["steps"]
    sources = [step["presetProvenance"]["source"]["slug"] for step in jobs]
    assert sources == [
        "security-code-supply-chain",
        "security-web-api",
        "security-image-sbom",
    ]
    planned = {job["id"]: job["input"] for job in raw["jobs"]}
    for step, job_id in zip(jobs, ("code-scan", "web-baseline", "image-sbom")):
        hint = step["annotations"]["securityAssessment"]
        # The one compiler: the technical preset's step and admission hint
        # carry the plan's job input, with the native job spec it compiles.
        assert step["skill"]["args"]["assessment"] == hint["admission"]
        assert {
            k: v for k, v in hint["admission"].items() if k in planned[job_id]
        } == planned[job_id]
        assert (
            "jobSpec" in hint and step["skill"]["args"]["job_spec"] == hint["jobSpec"]
        )
        assert step["annotations"]["securityPlanJob"] == {
            "jobId": job_id,
            "planIdentity": identity,
        }
        assert step["skill"]["args"]["securityPlanJobPath"] == (
            f"artifacts/security-plan-run/{job_id}.json"
        )
        assert (
            f"planned job {job_id} of security plan {identity}" in step["instructions"]
        )
    assert conclusion["tool"]["id"] == "security.assess"
    run = conclusion["tool"]["inputs"]["planRun"]
    assert run == {"planRef": ref, "planIdentity": identity, "excluded": []}
    assert [p["packId"] for p in conclusion["tool"]["inputs"]["frameworkPacks"]] == [
        "synthetic-app",
        "synthetic-ops",
    ]
    assert expanded["publish"] == {"mode": "none"}


async def test_create_and_api_reach_the_same_expansion_and_review(
    tmp_path, artifact_root
):
    async with template_db(tmp_path) as maker, maker() as session:
        ref = await retain(session, plan_document())
        service = await seeded(session, tmp_path)
        inputs = {
            "plan_ref": ref,
            "assessment_ref": "client-review",
            "excluded_jobs": ["image-sbom"],
        }
        ui = await expand(service, inputs)
        api = await expand_preset_for_child_run(
            session=session,
            initial_parameters={
                "workflow": {"taskTemplate": {"slug": SLUG}, "inputs": dict(inputs)},
                **CONTEXT,
            },
            artifact_access=operator(),
        )
    assert comparable(api["workflow"]["steps"]) == comparable(ui["steps"])
    excluded = ui["steps"][-1]["tool"]["inputs"]["planRun"]["excluded"]
    assert excluded == [
        {
            "jobId": "image-sbom",
            "kind": "image-assessment",
            "reason": "Removed by the operator before launch.",
        }
    ]
    record = shop_authorization()
    first = build_assessment_proposal({"workflow": {"steps": ui["steps"]}}, record)
    second = build_assessment_proposal(
        {"workflow": {"steps": api["workflow"]["steps"]}}, record
    )
    assert first["proposalDigest"] == second["proposalDigest"]
    not_covered = section(first, "Not covered by this authorization")
    assert not_covered == [
        "Planned job image-sbom (image-assessment) is not launched: Removed by the "
        "operator before launch. Its requirements are reported as not assessed."
    ]


async def test_a_changed_plan_or_authorization_needs_a_fresh_review(
    tmp_path, artifact_root
):
    async with template_db(tmp_path) as maker, maker() as session:
        raw = plan_document()
        edited = deepcopy(raw)
        edited["subject"]["candidateRef"] = "shop@candidate-2"
        first_ref = await retain(session, raw)
        edited_ref = await retain(session, edited)
        service = await seeded(session, tmp_path)
        first = await expand(service, {"plan_ref": first_ref})
        again = await expand(service, {"plan_ref": first_ref})
        changed = await expand(service, {"plan_ref": edited_ref})
    assert comparable(first["steps"]) == comparable(again["steps"])
    record = shop_authorization()
    review = build_assessment_proposal({"workflow": {"steps": first["steps"]}}, record)
    assert (
        build_assessment_proposal({"workflow": {"steps": again["steps"]}}, record)
        == review
    )
    assert (
        build_assessment_proposal({"workflow": {"steps": changed["steps"]}}, record)[
            "proposalDigest"
        ]
        != review["proposalDigest"]
    )
    other = shop_authorization(ref="other-review", assessment="other-review")
    assert (
        build_assessment_proposal({"workflow": {"steps": first["steps"]}}, other)[
            "proposalDigest"
        ]
        != review["proposalDigest"]
    )


async def test_uncovered_work_is_named_in_the_review_before_launch(
    tmp_path, artifact_root
):
    async with template_db(tmp_path) as maker, maker() as session:
        ref = await retain(session, plan_document())
        service = await seeded(session, tmp_path)
        expanded = await expand(service, {"plan_ref": ref})
        removed = await expand(
            service, {"plan_ref": ref, "excluded_jobs": ["web-baseline"]}
        )
    # This authorization does not admit the planned web origin.
    narrow = shop_authorization(origin="https://elsewhere.example.test")
    review = build_assessment_proposal(
        {"workflow": {"steps": expanded["steps"]}}, narrow
    )
    items = section(review, "Not covered by this authorization")
    assert len(items) == 1 and "web origin is not an admitted target" in items[0]
    # The operator removes it and launches the rest; the review says so.
    review = build_assessment_proposal(
        {"workflow": {"steps": removed["steps"]}}, narrow
    )
    items = section(review, "Not covered by this authorization")
    assert items == [
        "Planned job web-baseline (web-assessment) is not launched: Removed by the "
        "operator before launch. Its requirements are reported as not assessed."
    ]
    assert not any(
        step.get("annotations", {}).get("securityPlanJob", {}).get("jobId")
        == "web-baseline"
        for step in removed["steps"]
    )


async def test_plan_runs_need_a_retained_plan_and_the_operators_read_authority(
    tmp_path, artifact_root
):
    async with template_db(tmp_path) as maker, maker() as session:
        ref = await retain(session, plan_document())
        draft = await retain(
            session, plan_document(), link_type="output.security_plan_draft"
        )
        service = await seeded(session, tmp_path)
        with pytest.raises(PresetValidationError, match="operator's authority"):
            await expand(service, {"plan_ref": ref}, access=None)
        with pytest.raises(PresetValidationError, match="not a retained security plan"):
            await expand(service, {"plan_ref": draft})
        with pytest.raises(PresetValidationError, match="missing or unreadable"):
            await expand(service, {"plan_ref": "art_missing"})
        with pytest.raises(PresetValidationError, match="unknown planned job IDs"):
            await expand(service, {"plan_ref": ref, "excluded_jobs": ["ghost"]})


def section(review, label):
    return next(s["items"] for s in review["sections"] if s["label"] == label)


def shop_authorization(
    *, ref="client-review", assessment="client-review", origin=ORIGIN
):
    now = datetime.now(UTC)
    value = authorization(
        ref=ref,
        validFrom=(now - timedelta(hours=1)).isoformat(),
        validUntil=(now + timedelta(hours=1)).isoformat(),
        maxTotalJobSeconds=4000,
    )
    value["scope"] = {
        **value["scope"],
        "assessmentRef": assessment,
        "targets": [
            {"webOrigin": origin, "webRoutes": [{"path": "/", "methods": ["GET"]}]}
        ],
        "networkRefs": ["shop-egress"],
        "maxJobSeconds": 1800,
        "maxCpuMillis": 4000,
        "maxMemoryMiB": 4096,
    }
    return AssessmentAuthorizations.model_validate(saved(value)).authorizations[0]


async def test_planned_job_nodes_carry_their_record_path_and_scope_hint(
    tmp_path, artifact_root
):
    from types import SimpleNamespace

    from moonmind.security.assessment_projects import apply_assessment_defaults
    from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
    from tests.unit.security.test_security_plan import snapshot

    async with template_db(tmp_path) as maker, maker() as session:
        ref = await retain(session, plan_document())
        service = await seeded(session, tmp_path)
        expanded = await expand(service, {"plan_ref": ref})
    workflow = {"instructions": "Run the plan.", "steps": expanded["steps"]}
    project = snapshot()
    apply_assessment_defaults(workflow, project)
    plan = _build_runtime_planner()(
        inputs={},
        parameters={"task": workflow},
        snapshot=SimpleNamespace(digest="reg:sha256:test", artifact_ref="art_registry"),
    )
    *jobs, conclusion = plan["nodes"]
    kinds = []
    for node, job_id in zip(jobs, ("code-scan", "web-baseline", "image-sbom")):
        assert node["tool"]["type"] == "agent_runtime"
        # run.py forwards this to the agent runtime's record publisher.
        assert node["inputs"]["securityPlanJobPath"] == (
            f"artifacts/security-plan-run/{job_id}.json"
        )
        annotations = node["inputs"].get("annotations") or node.get("annotations")
        kinds.append(annotations["securityAssessment"]["kind"])
    # The workflow signs the scope into the web step only.
    assert kinds == ["repository-scan", "web-assessment", "image-assessment"]
    assert conclusion["tool"]["name"] == "security.assess"
    assert conclusion["inputs"]["planRun"]["planRef"] == ref
    assert conclusion["inputs"]["assessmentProject"]["digest"] == project.digest


async def test_network_plan_expands_its_analyzer_as_an_agent_free_tool_step(
    tmp_path, artifact_root
):
    """A network-config job is its typed container job, with no agent step (#5441)."""
    raw = plan_document()
    export = {
        "artifactRef": "art_openapi",
        "fileName": "edge.cfg",
        "device": "edge-rtr-1",
        "exportedAt": "2026-11-27T08:00:00Z",
        "supplier": "Network team",
    }
    raw["jobs"] = [
        {
            "id": "edge-config",
            "kind": "network-config",
            "input": {"exports": [export]},
            "assets": ["shop-app"],
            "sources": source("operator_input", "Create: network exports"),
        }
    ]
    for row in raw["requirements"]:
        row.pop("jobIds", None)
        if row.get("methods") == ["deterministic_check"]:
            row["methods"] = []
            row["gaps"] = [{"kind": "missing_input", "detail": "Fixture."}]
    async with template_db(tmp_path) as maker, maker() as session:
        ref = await retain(session, raw)
        identity = plan_identity(parse_security_plan(json.dumps(raw)))
        service = await seeded(session, tmp_path)
        expanded = await expand(service, {"plan_ref": ref})
    job, conclusion = expanded["steps"]
    assert job["type"] == "tool" and job["tool"]["id"] == "container.run_job"
    spec = job["tool"]["inputs"]["spec"]
    assert spec["networkMode"] == "none" and "egressPolicyRef" not in spec
    assert spec["networkConfigAssessment"]["exports"][0]["device"] == "edge-rtr-1"
    assert "skill" not in job or not job["skill"]
    assert job["annotations"]["securityPlanJob"] == {
        "jobId": "edge-config",
        "planIdentity": identity,
    }
    assert conclusion["tool"]["id"] == "security.assess"
    assert conclusion["tool"]["inputs"]["planRun"]["excluded"] == []
    assert conclusion["tool"]["inputs"]["reportRefs"] == []
    # No agent: an evidence-processing policy that disables model delivery
    # still admits the run, so the deterministic checks run.
    from api_service.services.assessment_model_delivery import (
        require_execution_model_delivery,
    )
    from moonmind.workflows.executions.execution_contract import (
        workflow_requires_agent,
    )
    from tests.unit.services.test_assessment_model_delivery import (
        source as processing,
    )

    parameters = {
        "workflow": {"steps": expanded["steps"]},
        **processing({"modelDelivery": "disabled"}).parameters,
    }
    assert not workflow_requires_agent(parameters["workflow"], parameters)
    require_execution_model_delivery(parameters, profile_ref=None)
