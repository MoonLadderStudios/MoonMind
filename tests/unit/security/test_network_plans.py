"""Security plans with a network subject over exported configurations (#5441)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from moonmind.security.assessment_projects import resolve_assessment_project
from moonmind.security.framework_registry import installed_digest, installed_pack
from moonmind.security.frameworks import DeclaredRequirementChoice, EvidencePeriod
from moonmind.security.network_configs import REVIEW_PROCEDURES, RULES
from moonmind.security.security_plan import (
    SecurityPlanError,
    derive_security_plan,
    render_security_plan,
    requirement_evidence,
    selected_for_subject,
    validate_security_plan,
)
from moonmind.security.security_plan_drafting import (
    parse_network_exports,
    plan_from_draft,
    plan_standard,
    planning_context,
    require_subject_type,
)

NOW = datetime(2026, 11, 28, tzinfo=UTC)
PERIOD = {"start": "2026-11-28", "end": "2026-11-28"}
FIXTURES = Path(__file__).parents[2] / "fixtures" / "security" / "network_config"
EXPORTS = json.loads((FIXTURES / "assessments.json").read_text())["corrected"][
    "exports"
]
EXPORT_REFS = [export["artifactRef"] for export in EXPORTS]


def snapshot(**overrides):
    environment = {
        "id": "edge",
        "name": "Edge network",
        "assets": ["edge-rtr-1"],
        "artifactRefs": EXPORT_REFS,
        "dataClassification": "confidential",
        **overrides,
    }
    return resolve_assessment_project(
        {
            "projects": [
                {
                    "id": "network",
                    "name": "Network",
                    "purpose": "Exported configuration review",
                    "boundaries": "Edge devices whose exports the operator supplies",
                    "environments": [environment],
                }
            ]
        },
        project_id="network",
        environment_id="edge",
        version=1,
    )


def packs(*ids):
    loaded = {}
    for pack_id in ids:
        pack = installed_pack(pack_id)[1]
        loaded[(pack.id, pack.edition)] = pack
    return loaded


def standards(loaded):
    return [
        plan_standard(
            type("Selection", (), {"edition": pack.edition})(),
            pack,
            sha256=installed_digest(pack.id),
            period=EvidencePeriod.model_validate(PERIOD),
            artifact_ref=None,
        )
        for pack in loaded.values()
    ]


def network_job():
    return {
        "id": "edge-config",
        "kind": "network-config",
        "input": {"exports": EXPORTS, "flows": [{"host": "10.10.10.20", "port": 23}]},
        "assets": EXPORT_REFS,
        "sources": [{"kind": "operator_input", "ref": "Create: network exports"}],
    }


def draft(loaded, project, *, jobs=None):
    """A planner's draft: selected requirements planned, others not applicable."""
    no_choice = DeclaredRequirementChoice.model_validate({})
    rows = []
    for pack in loaded.values():
        for requirement in pack.requirements:
            base = {
                "packId": pack.id,
                "edition": pack.edition,
                "id": requirement.id,
                "sources": [{"kind": "document", "ref": "Pack subject tags"}],
            }
            if not selected_for_subject(requirement, "network"):
                rows.append(
                    {
                        **base,
                        "applicability": "not_applicable",
                        "reason": "Not a network requirement of this standard.",
                    }
                )
                continue
            allowed, _, requests = requirement_evidence(pack, requirement, no_choice)
            row = {
                **base,
                "applicability": "applicable",
                "reason": "The exported edge configuration is in scope.",
                "methods": allowed,
            }
            if (
                "deterministic_check" in allowed
                and requirement.objectives
                and jobs != []
            ):
                row["jobIds"] = ["edge-config"]
            elif not (
                "external_only" in allowed
                or ("document_review" in allowed and requests)
                or ("agent_interpretation" in allowed and jobs != [])
            ):
                row["gaps"] = [
                    {
                        "kind": "missing_input",
                        "detail": "Agent review of the sanitized exports decides this.",
                    }
                ]
            rows.append(row)
    return {
        "schemaVersion": "security-plan/v1",
        "subject": {
            "type": "network",
            "project": {
                "projectId": "network",
                "environmentId": "edge",
                "sourceVersion": 1,
                "digest": project.digest,
            },
            "candidateRef": "edge exports as supplied",
            "systemBoundary": "The edge router whose configuration export was supplied.",
            "scope": {"assets": EXPORT_REFS, "exclusions": []},
            "sources": [
                {"kind": "environment_field", "ref": "environment.artifactRefs"}
            ],
        },
        "standards": [
            s.model_dump(mode="json", by_alias=True) for s in standards(loaded)
        ],
        "requirements": rows,
        "jobs": [network_job()] if jobs is None else jobs,
    }


def validated(plan, loaded, project):
    derived = derive_security_plan(plan, packs=loaded)
    return validate_security_plan(derived, packs=loaded, snapshot=project, now=NOW)


def test_network_plan_over_exports_needs_no_network_credential_or_new_target():
    loaded = packs("network-best-practices")
    project = snapshot()
    plan = validated(draft(loaded, project), loaded, project)
    [job] = plan.jobs
    authority = job.authority
    assert authority.scoped is False
    assert authority.network_refs == [] and authority.credential_purposes == []
    assert authority.profile_refs == [] and authority.action_classes == []
    assert [(t.kind, t.ref) for t in authority.targets] == [
        ("artifact", ref) for ref in EXPORT_REFS
    ]
    assert job.readiness.status == "unverified"
    text = render_security_plan(plan, plan_ref="art_plan")
    for section in (
        "### Planned checks",
        "### Review procedures",
        "### Required inputs",
    ):
        assert section in text
    assert all(rule in text for rule in RULES)
    assert all(name in text for name in REVIEW_PROCEDURES)
    export = EXPORTS[0]
    assert export["device"] in text and export["supplier"] in text
    assert "did not observe the running device" in text


def test_network_plan_job_cannot_name_an_export_outside_the_environment():
    loaded = packs("network-best-practices")
    project = snapshot(artifactRefs=["art_other"])
    with pytest.raises(SecurityPlanError) as raised:
        validated(draft(loaded, project), loaded, project)
    assert any("outside the saved environment" in d for d in raised.value.diagnostics)


def test_network_plan_selects_only_requirements_tagged_for_networks():
    loaded = packs("network-best-practices", "pci-dss-readiness")
    project = snapshot()
    plan = validated(draft(loaded, project), loaded, project)
    applicable = {
        (row.pack_id, row.id)
        for row in plan.requirements
        if row.applicability == "applicable"
    }
    pci = installed_pack("pci-dss-readiness")[1]
    assert {rid for pack, rid in applicable if pack == pci.id} == {
        r.id for r in pci.requirements if r.id.startswith("1.")
    }
    # A planner cannot widen a network plan to untagged requirements.
    widened = draft(loaded, project)
    row = next(r for r in widened["requirements"] if r["id"] == "12.1.1")
    row.update(applicability="applicable", methods=["document_review"])
    with pytest.raises(SecurityPlanError) as raised:
        validated(widened, loaded, project)
    assert any(
        "does not tag this requirement for a network subject" in d
        for d in raised.value.diagnostics
    )


def test_planning_context_carries_tags_review_procedures_and_stated_exports():
    loaded = packs("network-best-practices", "iso-27001-readiness")
    project = snapshot()
    exports = parse_network_exports(EXPORTS, project)
    context = planning_context(
        project,
        require_subject_type("network"),
        [(s, loaded[(s.pack_id, s.edition)]) for s in standards(loaded)],
        exports,
    )
    network = context["network"]
    assert network["jobKind"] == "network-config"
    assert set(network["checks"]) == set(RULES)
    assert set(network["reviewProcedures"]) == set(REVIEW_PROCEDURES)
    assert network["exports"][0]["device"] == EXPORTS[0]["device"]
    iso = next(s for s in context["standards"] if s["packId"] == "iso-27001-readiness")
    selected = {r["id"] for r in iso["requirements"] if r["selectedForSubject"]}
    assert selected == {"A.8.20", "A.8.21", "A.8.22"}
    text = json.dumps(context)
    assert "credential" not in text.lower() or "credentialRefs" not in text


def test_stated_exports_must_be_environment_artifacts():
    with pytest.raises(SecurityPlanError) as raised:
        parse_network_exports(EXPORTS, snapshot(artifactRefs=["art_other"]))
    assert "not an artifact of the saved environment" in raised.value.diagnostics[0]
    with pytest.raises(SecurityPlanError):
        parse_network_exports([{"artifactRef": EXPORT_REFS[0]}], snapshot())


def test_planned_export_provenance_must_be_what_the_operator_stated():
    loaded = packs("network-best-practices")
    project = snapshot()
    stated = parse_network_exports(EXPORTS, project)
    plan = draft(loaded, project)

    def retain(plan, exports):
        return plan_from_draft(
            json.dumps(plan),
            subject_type="network",
            standards=standards(loaded),
            packs=loaded,
            snapshot=project,
            now=NOW,
            network_exports=exports,
        )

    assert retain(plan, stated).jobs[0].kind == "network-config"
    invented = draft(loaded, project)
    invented["jobs"][0]["input"]["exports"][0]["supplier"] = "Someone in the repository"
    with pytest.raises(SecurityPlanError) as raised:
        retain(invented, stated)
    assert any("the operator stated" in d for d in raised.value.diagnostics)
    # Without stated exports the plan asks for them instead of guessing.
    empty = retain(draft(loaded, project, jobs=[]), [])
    assert any("configuration exports" in p.text for p in empty.open_points)


def test_plan_run_launches_the_analyzer_without_an_agent_and_matches_its_summary():
    from moonmind.schemas.container_job_models import ContainerJobSpec
    from moonmind.security.security_plan_run import (
        CONTAINER_TOOL,
        conclusion_step,
        derive_plan_run,
        plan_against_actual,
        tool_job_records,
        tool_job_step,
    )

    loaded = packs("network-best-practices", "pci-dss-readiness")
    project = snapshot()
    plan = validated(draft(loaded, project), loaded, project)
    [(job_id, launcher, value)], skipped = derive_plan_run(plan)
    assert (job_id, launcher, skipped) == ("edge-config", CONTAINER_TOOL, [])
    step = tool_job_step(job_id, "a" * 64, "network-config", value)
    assert step["type"] == "tool" and step["tool"]["id"] == "container.run_job"
    spec = ContainerJobSpec.model_validate(step["tool"]["inputs"]["spec"])
    assert spec.network_mode == "none" and spec.network_config_assessment is not None
    # A summary counts only for the planned job it was admitted as.
    summary = {
        "schemaVersion": "network-config/v1",
        "settings": value,
        "status": "complete",
    }
    other = {**summary, "settings": {**value, "flows": []}}
    records = tool_job_records(
        plan, "a" * 64, [("art_other", other), ("art_sum", summary)]
    )
    assert records["edge-config"].report_refs == ["art_sum"]
    assert records["edge-config"].state == "completed"
    # Model delivery disabled: review-only rows are not assessed, with why.
    record = plan_against_actual(
        plan,
        plan_ref="art_plan",
        identity="a" * 64,
        excluded=[],
        records=records,
        reports={"art_sum": "readable"},
        model_delivery="disabled",
    )
    rows = {row["requirementId"]: row for row in record["requirements"]}
    assert rows["NET-1"]["state"] == "evidence_collected"
    assert rows["NET-6"]["state"] == "not_assessed"
    assert "model delivery to disabled" in rows["NET-6"]["reason"]
    allowed = plan_against_actual(
        plan,
        plan_ref="art_plan",
        identity="a" * 64,
        excluded=[],
        records=records,
        reports={"art_sum": "readable"},
    )
    assert {r["requirementId"]: r["state"] for r in allowed["requirements"]}[
        "NET-6"
    ] == "review"
    # The conclusion reads no environment artifact as a report, and concludes
    # the network checklist under the reviewed plan's applicability.
    inputs = conclusion_step(plan, plan_ref="art_plan", identity="a" * 64, excluded=[])[
        "tool"
    ]["inputs"]
    assert inputs["reportRefs"] == [] and inputs["networkRefs"] == []
    selections = {s["packId"]: s for s in inputs["frameworkPacks"]}
    choices = selections["network-best-practices"]["requirementChoices"]
    assert choices["NET-1"]["applicability"] == "applicable"
    assert (
        "requirementChoices" not in selections["pci-dss-readiness"]
        or not selections["pci-dss-readiness"]["requirementChoices"]
    )
