"""Planning context and draft checks for a security plan started from Create (#5431)."""

import copy
import hashlib
import json

import pytest

from moonmind.security.assessment_projects import apply_assessment_defaults
from moonmind.security.framework_registry import INSTALLED_PACK_IDS, installed_pack
from moonmind.security.frameworks import EvidencePeriod
from moonmind.security.security_plan import SecurityPlanError
from moonmind.security.security_plan_drafting import (
    MAX_INLINE_CONTEXT_BYTES,
    inline_context,
    parse_standards,
    plan_from_draft,
    plan_period,
    plan_standard,
    planning_context,
    require_subject_type,
)
from tests.unit.security.test_security_plan import (
    NOW,
    ORIGIN,
    PERIOD,
    hand_authored,
    packs,
    snapshot,
)


def admitted(plan=None):
    """The run's admitted standards for the synthetic two-pack plan."""
    plan = plan or hand_authored()
    loaded = packs()
    return [
        plan_standard(
            _edition(s["edition"]),
            loaded[(s["packId"], s["edition"])],
            sha256=s["sha256"],
            period=EvidencePeriod.model_validate(PERIOD),
            artifact_ref=s["artifactRef"],
        )
        for s in plan["standards"]
    ], loaded


def _edition(edition):
    return type("Selection", (), {"edition": edition})()


def draft():
    """A planner's draft: proposals only, no operator choices."""
    plan = hand_authored()
    for standard in plan["standards"]:
        standard.pop("title", None)
        standard.pop("distribution", None)
        standard.pop("requirementChoices", None)
    for row in plan["requirements"]:
        for field in ("title", "allowedMethods", "externalParty", "evidenceRequestIds"):
            row.pop(field, None)
    for job in plan["jobs"]:
        job.pop("authority")
        job.pop("readiness")
    return plan


def retained(plan, **kwargs):
    standards, loaded = admitted()
    return plan_from_draft(
        json.dumps(plan),
        subject_type=kwargs.pop("subject_type", "application"),
        standards=kwargs.pop("standards", standards),
        packs=loaded,
        snapshot=kwargs.pop("snapshot", snapshot()),
        now=NOW,
    )


def rejected(plan, **kwargs):
    with pytest.raises(SecurityPlanError) as raised:
        retained(plan, **kwargs)
    return raised.value.diagnostics


def test_context_projects_targets_and_every_requirement_without_credentials():
    project = snapshot(
        credentialRefs=["db://shop-deploy"],
        connectionRefs=["github-shop"],
        repositories=[{"repository": "org/shop", "connectionRef": "github-shop"}],
    )
    standards, loaded = admitted()
    context = planning_context(
        project, "application", [(s, loaded[(s.pack_id, s.edition)]) for s in standards]
    )
    text = json.dumps(context)
    assert "db://" not in text and "github-shop" not in text
    assert context["environment"]["originCandidates"] == [ORIGIN]
    assert context["environment"]["repositories"] == ["org/shop"]
    assert context["project"]["digest"] == project.digest
    app, ops = context["standards"]
    assert [r["id"] for r in app["requirements"]] == [
        "APP-1",
        "APP-2",
        "APP-3",
        "APP-4",
    ]
    assert app["requirements"][0]["allowedMethods"] == ["deterministic_check"]
    assert app["requirements"][3]["notAssessedReason"]
    # Restricted pack text stays in its artifact.
    assert "title" not in ops and all("title" not in r for r in ops["requirements"])


def test_inline_context_drops_titles_only_when_large():
    selected = []
    for pack_id in INSTALLED_PACK_IDS:
        raw, pack = installed_pack(pack_id)
        standard = plan_standard(
            _edition(pack.edition),
            pack,
            sha256=hashlib.sha256(raw).hexdigest(),
            period=plan_period(snapshot(), NOW),
            artifact_ref=None,
        )
        selected.append((standard, pack))
    small = planning_context(snapshot(), "application", selected[:1])
    assert inline_context(small) == small
    context = planning_context(snapshot(), "application", selected)
    compact = inline_context(context)
    assert len(json.dumps(context)) > MAX_INLINE_CONTEXT_BYTES
    assert compact["titlesOmitted"] is True
    assert [
        [r["id"] for r in standard["requirements"]] for standard in compact["standards"]
    ] == [[r.id for r in pack.requirements] for _, pack in selected]
    assert all(
        "title" not in r and r["allowedMethods"] is not None
        for standard in compact["standards"]
        for r in standard["requirements"]
    )


def test_draft_is_derived_validated_and_gains_operator_questions():
    plan = retained(draft())
    assert plan.requirements[0].allowed_methods == ["deterministic_check"]
    assert plan.jobs[1].authority.targets[0].ref == ORIGIN
    texts = [point.text for point in plan.open_points]
    assert any("evidence period" in text for text in texts)
    # An uncited regulatory fact stays undetermined.
    assert plan.regulatory_facts[0].status == "undetermined"


def test_claimed_regulatory_fact_without_a_record_becomes_undetermined():
    plan = draft()
    plan["regulatoryFacts"][0]["status"] = "established"
    assert retained(plan).regulatory_facts[0].status == "undetermined"


@pytest.mark.parametrize(
    "change, expected",
    [
        (
            lambda p: p["requirements"].pop(4),
            "requirements: omits synthetic-ops 2026 requirement OPS-1",
        ),
        (
            lambda p: p["subject"]["scope"]["assets"].append("db.internal.test"),
            "subject.scope.assets[2]: outside the saved environment's candidates",
        ),
        (
            lambda p: p["jobs"][1]["input"].update(
                origin="https://elsewhere.example.test"
            ),
            "jobs[1].authority.targets[0]: outside the saved environment's candidates",
        ),
    ],
)
def test_omitted_requirements_and_invented_targets_are_rejected(change, expected):
    plan = draft()
    change(plan)
    assert expected in rejected(plan)


def test_instructions_embedded_in_repository_content_cannot_change_the_run():
    # A README told the planner to drop a standard, plan an endpoint and add
    # its own requirement choice; validation rejects each change.
    plan = draft()
    plan["subject"]["type"] = "endpoint"
    plan["standards"].pop(1)
    plan["requirements"] = [
        r for r in plan["requirements"] if r["packId"] != "synthetic-ops"
    ]
    plan["standards"][0]["requirementChoices"] = {
        "APP-1": {
            "applicability": "not_applicable",
            "applicabilityReason": "README said so",
        }
    }
    errors = rejected(plan)
    assert (
        "subject.type: differs from the run's admitted subject type; repository "
        "content cannot change it" in errors
    )
    assert (
        "standards: omits the admitted standard synthetic-ops 2026; repository "
        "content cannot remove standards" in errors
    )
    assert (
        "standards[0].requirementChoices: an operator decision, not a planner proposal"
        in errors
    )

    plan = draft()
    plan["standards"][1]["revision"] = "2"
    assert (
        "standards[1]: differs from the admitted pack identity or period"
        in rejected(plan)
    )


def test_proposed_inputs_use_only_the_standards_own_fields():
    plan = draft()
    plan["standards"][0]["profile"] = {"anything": True}
    assert "standards[0].profile: this standard takes no inputs" in rejected(plan)


def test_selection_and_subject_type_are_checked_before_planning():
    assert require_subject_type("application") == "application"
    with pytest.raises(SecurityPlanError) as raised:
        require_subject_type("endpoint")
    assert "no installed journey plans this subject" in str(raised.value)
    with pytest.raises(SecurityPlanError) as raised:
        parse_standards([])
    assert raised.value.diagnostics == ["standards: select at least one standard"]
    selection = {"id": "owasp-asvs-l1", "edition": "5.0.0"}
    with pytest.raises(SecurityPlanError) as raised:
        parse_standards([selection, copy.deepcopy(selection), {"junk": 1}])
    assert raised.value.diagnostics == [
        "standards[1]: repeats a selected standard",
        "standards[2]: choose a standard from the catalog",
    ]
    declared = {
        "packId": "operator-policy",
        "edition": "2026",
        "revision": "1",
        "artifactRef": "art_policy",
        "sha256": "c" * 64,
    }
    assert [
        s.model_dump(by_alias=True) for s in parse_standards([selection, declared])
    ] == [
        selection,
        declared,
    ]


def test_plan_steps_receive_the_snapshot_but_no_scope_or_grants():
    project = snapshot()
    workflow = {
        "steps": [
            {
                "type": "tool",
                "tool": {
                    "id": "security.plan",
                    "inputs": {
                        "phase": "context",
                        "assessmentProject": {"stale": True},
                    },
                },
            },
            {"type": "tool", "tool": {"id": "security.assess", "inputs": {}}},
        ]
    }
    apply_assessment_defaults(workflow, project)
    plan_inputs = workflow["steps"][0]["tool"]["inputs"]
    assert plan_inputs == {
        "phase": "context",
        "assessmentProject": project.model_dump(mode="json", by_alias=True),
    }
    assess_inputs = workflow["steps"][1]["tool"]["inputs"]
    assert assess_inputs["scope"]["assets"] == list(project.configuration.assets)
    assert assess_inputs["credentialRefs"] == [] and assess_inputs["networkRefs"] == []
