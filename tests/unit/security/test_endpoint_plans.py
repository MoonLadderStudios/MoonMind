"""Planning and running an endpoint security plan (#5439)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest

from moonmind.security.framework_registry import (
    installed_digest,
    installed_pack,
    pack_subjects,
    readiness_catalog,
)
from moonmind.security.frameworks import EvidencePeriod
from moonmind.security.security_plan import (
    SecurityPlanError,
    derive_security_plan,
    parse_security_plan,
    render_security_plan,
    validate_security_plan,
)
from moonmind.security.security_plan_drafting import (
    SUBJECT_TYPES,
    operator_points,
    plan_standard,
    planning_context,
    require_subject_type,
)
from moonmind.security.security_plan_run import (
    conclusion_step,
    derive_plan_run,
    plan_identity,
)
from tests.unit.security.test_security_plan import snapshot, source

NOW = datetime(2026, 11, 28, tzinfo=UTC)
PERIOD = {"start": "2026-11-27", "end": "2026-11-28"}

PACK = "endpoint-best-practices"
PROFILE = "xccdf_org.ssgproject.content_profile_standard"
RULE = "xccdf_org.ssgproject.content_rule_"
ENVIRONMENT = {"assets": ["web-01", "shop-app"], "artifactRefs": ["art_rootfs"]}


def endpoint_pack():
    return installed_pack(PACK)[1]


def pci():
    return installed_pack("pci-dss-readiness")[1]


def draft(**job_changes):
    pack = endpoint_pack()
    job_input = {
        "target": "rootfs",
        "asset": "web-01",
        "inputArtifactRef": "art_rootfs",
        "datastream": "ssg-debian12-ds.xml",
        "profile": PROFILE,
        "ruleIds": [
            rule
            for row in pack.requirements
            for objective in row.objectives
            for rule in objective.rule_ids
        ],
        **job_changes,
    }
    return {
        "schemaVersion": "security-plan/v1",
        "subject": {
            "type": "endpoint",
            "project": {
                "projectId": "shop",
                "environmentId": "prod",
                "sourceVersion": 3,
                "digest": snapshot(**ENVIRONMENT).digest,
            },
            "candidateRef": "web-01 root filesystem export of 2026-11-26",
            "systemBoundary": "The web-01 host's root filesystem.",
            "scope": {"assets": ["web-01"], "exclusions": ["shop-legacy"]},
            "sources": source("environment_field", "environments.prod.assets"),
        },
        "standards": [
            {
                "packId": PACK,
                "edition": pack.edition,
                "revision": pack.revision,
                "sha256": installed_digest(PACK),
                "period": PERIOD,
            }
        ],
        "requirements": [
            {
                "packId": PACK,
                "edition": pack.edition,
                "id": row.id,
                "applicability": "applicable",
                "reason": "web-01 runs Debian 12.",
                "sources": source("environment_field", "environments.prod.assets"),
                "methods": ["deterministic_check"],
                "jobIds": ["endpoint-scan"],
            }
            for row in pack.requirements
        ],
        "jobs": [
            {
                "id": "endpoint-scan",
                "kind": "endpoint-assessment",
                "input": job_input,
                "assets": ["web-01"],
                "sources": source(
                    "environment_field", "environments.prod.artifactRefs"
                ),
            }
        ],
    }


def validate(raw, packs=None):
    packs = packs or {(PACK, endpoint_pack().edition): endpoint_pack()}
    plan = derive_security_plan(raw, packs=packs)
    return validate_security_plan(
        plan, packs=packs, snapshot=snapshot(**ENVIRONMENT), now=NOW
    )


def test_endpoint_is_a_planning_subject():
    assert SUBJECT_TYPES == ("application", "endpoint")
    assert require_subject_type("endpoint") == "endpoint"
    with pytest.raises(SecurityPlanError):
        require_subject_type("network")


def test_an_endpoint_plan_lists_the_planned_job_its_authority_and_inputs():
    plan = validate(draft())
    (job,) = plan.jobs
    authority = job.authority
    assert authority.scoped is False
    assert [(t.kind, t.ref) for t in authority.targets] == [("artifact", "art_rootfs")]
    assert authority.network_refs == [] and authority.credential_purposes == []
    assert authority.max_job_seconds == 1815
    text = render_security_plan(plan)
    assert "### Planned endpoint checks" in text
    assert "evaluates xccdf" in text and "for endpoint web-01" in text
    assert "requires 12 named rules to pass or fail" in text
    assert "### Required inputs" in text
    assert "ssg-<product>-ds.xml" in text
    assert "not MoonMind's own observation" in text


def test_imported_results_and_tailoring_are_artifacts_of_the_environment():
    raw = draft(
        target="results",
        datastream=None,
        inputArtifactRef="art_rootfs",
    )
    raw["jobs"][0]["input"] = {
        k: v for k, v in raw["jobs"][0]["input"].items() if v is not None
    }
    plan = validate(raw)
    assert "imports operator-produced OpenSCAP results" in render_security_plan(plan)
    outside = draft(tailoringArtifactRef="art_elsewhere")
    with pytest.raises(SecurityPlanError) as raised:
        validate(outside)
    assert any("outside the saved environment" in d for d in raised.value.diagnostics)


def test_remediation_in_a_planned_job_is_refused():
    with pytest.raises(SecurityPlanError) as raised:
        validate(draft(remediate=True))
    assert any(d.startswith("jobs[0].input") for d in raised.value.diagnostics)


def test_requirements_a_standard_does_not_tag_for_endpoints_must_be_not_applicable():
    standard = pci()
    tagged = {
        row.id for row in standard.requirements if "endpoint" in row.subject_types
    }
    assert {"2.2.1", "6.3.3", "10.2.1"} <= tagged
    assert "9.2.1" not in tagged  # Facility entry is not a host requirement.
    assert pack_subjects(standard) == ("application", "endpoint")
    assert pack_subjects(endpoint_pack()) == ("endpoint",)
    raw = draft()
    raw["standards"].append(
        {
            "packId": standard.id,
            "edition": standard.edition,
            "revision": standard.revision,
            "sha256": installed_digest(standard.id),
            "period": PERIOD,
        }
    )
    row = {
        "packId": standard.id,
        "edition": standard.edition,
        "id": "9.2.1",
        "applicability": "applicable",
        "reason": "Copied from an application plan.",
        "sources": source(),
        "gaps": [{"kind": "missing_input", "detail": "Facility evidence"}],
    }
    raw["requirements"].append(row)
    packs = {
        (PACK, endpoint_pack().edition): endpoint_pack(),
        (standard.id, standard.edition): standard,
    }
    with pytest.raises(SecurityPlanError) as raised:
        validate(raw, packs)
    assert any(
        "does not tag this requirement for a endpoint subject" in d
        for d in raised.value.diagnostics
    )


def test_the_catalog_offers_general_endpoint_best_practices_for_endpoints_only():
    entries = {entry["id"]: entry for entry in readiness_catalog()}
    entry = entries[PACK]
    assert entry["title"] == "General best practices for endpoints"
    assert entry["subjects"]["endpoint"]["available"]
    assert not entry["subjects"]["application"]["available"]
    assert entries["iso-27001-readiness"]["subjects"]["endpoint"]["available"]
    assert not entries["owasp-asvs-l1"]["subjects"]["endpoint"]["available"]
    pack = endpoint_pack()
    assert PROFILE in pack.conclusions
    for row in pack.requirements:
        assert row.subject_types == ["endpoint"] and row.objectives_complete
        for objective in row.objectives:
            assert (objective.source, objective.tool) == ("endpoint", "openscap")
            assert all(rule.startswith(RULE) for rule in objective.rule_ids)


def test_the_planning_context_names_endpoint_inputs_and_subject_selection():
    pack = endpoint_pack()
    standard = plan_standard(
        type("Selection", (), {"edition": pack.edition})(),
        pack,
        sha256=installed_digest(PACK),
        period=EvidencePeriod.model_validate(PERIOD),
        artifact_ref=None,
    )
    context = planning_context(snapshot(**ENVIRONMENT), "endpoint", [(standard, pack)])
    assert context["endpoint"]["jobKind"] == "endpoint-assessment"
    assert "ssg-debian12-ds.xml" in context["endpoint"]["datastreams"]
    assert context["endpoint"]["generalProfile"] == PROFILE
    assert "windows" in context["endpoint"]["unsupportedPlatforms"]
    assert "endpoint-assessment" in context["jobKinds"]
    rows = context["standards"][0]["requirements"]
    assert all(
        row["selectedForSubject"] and row["subjectTypes"] == ["endpoint"]
        for row in rows
    )
    application = planning_context(
        snapshot(**ENVIRONMENT), "application", [(standard, pack)]
    )
    assert "endpoint" not in application
    assert "selectedForSubject" not in application["standards"][0]["requirements"][0]


def test_an_endpoint_plan_without_an_endpoint_job_asks_for_the_input():
    raw = draft()
    raw["jobs"] = []
    for row in raw["requirements"]:
        row.pop("jobIds")
        row.pop("methods")
        row["gaps"] = [{"kind": "missing_input", "detail": "No captured copy yet."}]
    plan = derive_security_plan(
        raw, packs={(PACK, endpoint_pack().edition): endpoint_pack()}
    )
    (point,) = [
        p
        for p in operator_points(plan, snapshot(**ENVIRONMENT))
        if "endpoint plan" in p.text
    ]
    assert "does not reach live hosts" in point.text


def test_a_plan_run_launches_the_endpoint_preset_and_concludes_per_rule():
    plan = validate(draft())
    launch, excluded = derive_plan_run(plan)
    assert excluded == []
    assert [(job, preset) for job, preset, _ in launch] == [
        ("endpoint-scan", "security-endpoint")
    ]
    assert launch[0][2] == plan.jobs[0].input
    step = conclusion_step(
        plan, plan_ref="art_plan", identity=plan_identity(plan), excluded=excluded
    )
    (selection,) = step["tool"]["inputs"]["frameworkPacks"]
    assert set(selection["requirementChoices"]) == {row.id for row in plan.requirements}
    assert all(
        choice["applicability"] == "applicable"
        for choice in selection["requirementChoices"].values()
    )


def test_an_application_plan_run_keeps_operator_choices_untouched():
    raw = copy.deepcopy(draft())
    raw["subject"]["type"] = "application"
    plan = parse_security_plan(raw)
    step = conclusion_step(plan, plan_ref="art_plan", identity="a" * 64, excluded=[])
    assert (
        "requirementChoices" not in step["tool"]["inputs"]["frameworkPacks"][0]
        or not step["tool"]["inputs"]["frameworkPacks"][0]["requirementChoices"]
    )
