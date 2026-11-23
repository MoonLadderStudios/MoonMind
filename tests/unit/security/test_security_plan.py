"""security-plan/v1: derived content, proposals, authority and retention (#5430)."""

import copy
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from moonmind.security.assessment_preset_inputs import READINESS_ACTIONS
from moonmind.security.assessment_projects import resolve_assessment_project
from moonmind.security.framework_packs import load_declared_pack
from moonmind.security.security_plan import (
    SecurityPlan,
    SecurityPlanError,
    derive_security_plan,
    gap_list,
    plan_identity,
    read_methodology_plan,
    render_security_plan,
    validate_security_plan,
)

NOW = datetime(2026, 11, 23, tzinfo=UTC)
ORIGIN = "https://app.example.test"
PERIOD = {"start": "2026-10-01", "end": "2026-10-31"}


def requirement(rid, **extra):
    return {
        "id": rid,
        "title": f"Synthetic {rid}",
        "summary": f"Original synthetic prose for {rid}.",
        "category": "security",
        "applicability": "All synthetic systems",
        "evidencePrompt": "Retain evidence.",
        "objectiveBasis": "Synthetic basis",
        **extra,
    }


def pack_payload(pack_id, *, distribution="redistributable", revision="1"):
    base = {
        "id": pack_id,
        "edition": "2026",
        "revision": revision,
        "title": f"Synthetic {pack_id}",
        "source": {
            "url": "https://example.org/standard",
            "section": "1",
            "reviewedOn": "2026-10-01",
        },
        "reviewedBy": "operator",
        "reviewedOn": "2026-10-01",
        "publicationStatus": "published",
        "publishedOn": "2026-01-01",
        "effectiveFrom": "2026-01-01",
        "provenance": "synthetic fixture",
        "license": "synthetic license",
        "distribution": distribution,
    }
    if pack_id == "synthetic-app":
        base["externalClaims"] = [
            {
                "id": "lab",
                "evidenceType": "laboratory",
                "gap": "Only an accredited laboratory certifies this.",
                "verifiableBy": "Accredited laboratory",
                "nextStep": "Obtain the laboratory report.",
            }
        ]
        base["requirements"] = [
            requirement(
                "APP-1",
                objectives=[
                    {
                        "id": "no-secrets",
                        "description": "No committed secrets",
                        "source": "gitleaks",
                        "tool": "gitleaks",
                        "ruleIds": ["*"],
                    }
                ],
            ),
            requirement(
                "APP-2",
                organisationalEvidence=[
                    {
                        "id": "policy",
                        "category": "policy",
                        "purpose": "design",
                        "description": "Approved access policy",
                    }
                ],
            ),
            requirement("APP-3", externalClaim="lab", evidenceTypes=["laboratory"]),
            requirement(
                "APP-4", notAssessedReason="MoonMind cannot observe physical controls."
            ),
        ]
    else:
        base["requirements"] = [
            requirement("OPS-1", requiredEvidenceMethods=["deterministic_check"]),
            requirement("OPS-2", requiredEvidenceMethods=["enquiry"]),
        ]
    return base


def load(payload):
    return load_declared_pack(
        json.dumps(payload).encode(),
        pack_id=payload["id"],
        edition=payload["edition"],
        revision=payload["revision"],
    )


def packs(ops_revision="1"):
    app = load(pack_payload("synthetic-app"))
    ops = load(
        pack_payload("synthetic-ops", distribution="restricted", revision=ops_revision)
    )
    return {(app.id, app.edition): app, (ops.id, ops.edition): ops}


def snapshot(**overrides):
    environment = {
        "id": "prod",
        "name": "Production",
        "assets": ["shop-app", "app.example.test"],
        "exclusions": ["shop-legacy"],
        "originCandidates": [ORIGIN],
        "artifactRefs": ["art_openapi"],
        "cloudAccountCandidates": [
            {"account": "123456789012", "regions": ["us-east-1"]}
        ],
        "repositories": [{"repository": "org/shop"}],
        **overrides,
    }
    return resolve_assessment_project(
        {
            "projects": [
                {
                    "id": "shop",
                    "name": "Shop",
                    "purpose": "Synthetic shop",
                    "boundaries": "Synthetic application only",
                    "environments": [environment],
                }
            ]
        },
        project_id="shop",
        environment_id="prod",
        version=3,
    )


def source(kind="operator_input", ref="Operator planning note"):
    return [{"kind": kind, "ref": ref}]


def hand_authored(project=None):
    """A complete plan written by hand: derived content included verbatim."""
    project = project or snapshot()
    return {
        "schemaVersion": "security-plan/v1",
        "subject": {
            "type": "application",
            "project": {
                "projectId": "shop",
                "environmentId": "prod",
                "sourceVersion": 3,
                "digest": project.digest,
            },
            "candidateRef": "shop@candidate-1",
            "systemBoundary": "The shop web application and its repository.",
            "scope": {
                "assets": ["shop-app", ORIGIN],
                "exclusions": ["shop-legacy"],
            },
            "sources": source("environment_field", "environments.prod.assets"),
        },
        "standards": [
            {
                "packId": "synthetic-app",
                "edition": "2026",
                "revision": "1",
                "artifactRef": "art_app_pack",
                "sha256": "a" * 64,
                "period": PERIOD,
                "title": "Synthetic synthetic-app",
                "distribution": "redistributable",
            },
            {
                "packId": "synthetic-ops",
                "edition": "2026",
                "revision": "1",
                "artifactRef": "art_ops_pack",
                "sha256": "b" * 64,
                "period": PERIOD,
                "requirementChoices": {
                    "OPS-2": {
                        "applicability": "not_applicable",
                        "applicabilityReason": "No operations staff in scope.",
                    }
                },
                "distribution": "restricted",
            },
        ],
        "requirements": [
            {
                "packId": "synthetic-app",
                "edition": "2026",
                "id": "APP-1",
                "title": "Synthetic APP-1",
                "allowedMethods": ["deterministic_check"],
                "evidenceRequestIds": [],
                "applicability": "applicable",
                "reason": "The repository and site are in scope.",
                "sources": source("file", "README.md"),
                "methods": ["deterministic_check"],
                "jobIds": ["code-scan", "web-baseline"],
            },
            {
                "packId": "synthetic-app",
                "edition": "2026",
                "id": "APP-2",
                "title": "Synthetic APP-2",
                "allowedMethods": ["document_review"],
                "evidenceRequestIds": ["policy"],
                "applicability": "applicable",
                "reason": "An access policy governs the shop.",
                "sources": source("document", "art_policy_index"),
                "methods": ["document_review"],
            },
            {
                "packId": "synthetic-app",
                "edition": "2026",
                "id": "APP-3",
                "title": "Synthetic APP-3",
                "allowedMethods": ["external_only"],
                "externalParty": "Accredited laboratory",
                "evidenceRequestIds": [],
                "applicability": "applicable",
                "reason": "Certification is required for release.",
                "sources": source(),
                "methods": ["external_only"],
            },
            {
                "packId": "synthetic-app",
                "edition": "2026",
                "id": "APP-4",
                "title": "Synthetic APP-4",
                "allowedMethods": [],
                "evidenceRequestIds": [],
                "applicability": "undetermined",
                "reason": "Physical hosting is not yet described.",
                "sources": source(),
                "gaps": [
                    {
                        "kind": "no_evidence_path",
                        "detail": "MoonMind cannot observe physical controls.",
                    }
                ],
            },
            {
                "packId": "synthetic-ops",
                "edition": "2026",
                "id": "OPS-1",
                "allowedMethods": ["deterministic_check"],
                "evidenceRequestIds": [],
                "applicability": "applicable",
                "reason": "Source scanning applies.",
                "sources": source(),
                "methods": ["deterministic_check"],
                "jobIds": ["code-scan"],
            },
            {
                "packId": "synthetic-ops",
                "edition": "2026",
                "id": "OPS-2",
                "allowedMethods": ["document_review"],
                "evidenceRequestIds": [],
                "applicability": "not_applicable",
                "reason": "No operations staff in scope.",
                "sources": source(),
            },
        ],
        "jobs": [
            {
                "id": "code-scan",
                "kind": "repository-scan",
                "input": {"scanner": "gitleaks", "outputDirectory": "scan"},
                "assets": ["shop-app"],
                "sources": source(
                    "environment_field", "environments.prod.repositories"
                ),
                "authority": {
                    "scoped": False,
                    "profileRefs": [],
                    "actionClasses": [],
                    "targets": [{"kind": "workspace_path", "ref": "."}],
                    "credentialPurposes": [],
                    "networkRefs": [],
                    "maxJobSeconds": 315,
                },
                "readiness": {"status": "unverified"},
            },
            {
                "id": "web-baseline",
                "kind": "web-assessment",
                "input": {
                    "mode": "baseline",
                    "origin": ORIGIN,
                    "routes": [{"path": "/", "methods": ["GET"]}],
                    "egressPolicyRef": "shop-egress",
                },
                "assets": [ORIGIN],
                "sources": source(
                    "environment_field", "environments.prod.originCandidates"
                ),
                "authority": {
                    "scoped": True,
                    "profileRefs": ["web-assessment"],
                    "actionClasses": ["web-passive"],
                    "targets": [
                        {
                            "kind": "web_origin",
                            "ref": ORIGIN,
                            "routes": [{"path": "/", "methods": ["GET"]}],
                        }
                    ],
                    "credentialPurposes": [],
                    "networkRefs": ["shop-egress"],
                    "maxJobSeconds": 1020,
                    "maxRequestsPerSecond": 10,
                },
                "readiness": {"status": "unverified"},
            },
        ],
        "openPoints": [
            {"kind": "operator_question", "text": "Which release candidate ships?"}
        ],
        "regulatoryFacts": [
            {
                "kind": "certification",
                "subject": "Laboratory certification of the shop",
                "sources": source(),
            }
        ],
    }


def validate(plan, **kwargs):
    return validate_security_plan(
        plan,
        packs=kwargs.pop("packs", None) or packs(),
        snapshot=kwargs.pop("snapshot", None) or snapshot(),
        now=NOW,
        **kwargs,
    )


def rejected(plan, **kwargs):
    with pytest.raises(SecurityPlanError) as raised:
        validate(plan, **kwargs)
    return raised.value.diagnostics


def test_hand_authored_plan_for_two_packs_validates_and_renders_its_gaps():
    plan = validate(hand_authored())
    # The hand-written derived content is exactly what trusted code derives.
    assert derive_security_plan(plan, packs=packs()) == plan
    text = render_security_plan(plan, plan_ref="art_plan")
    assert "## Security plan" in text and "A plan grants nothing" in text
    assert "synthetic-ops OPS-2 — not_applicable" in text
    assert "### Gap list" in text and "APP-4: no_evidence_path" in text
    assert [gap["requirementId"] for gap in gap_list(plan)] == ["APP-4"]
    # Restricted pack text stays in its artifact.
    assert "Synthetic OPS" not in text
    dumped = json.dumps(plan.model_dump(mode="json", by_alias=True))
    assert "credentialRef" not in dumped and "Synthetic OPS-1" not in dumped


@pytest.mark.parametrize(
    "change, expected",
    [
        (
            lambda p: p["requirements"].pop(4),
            "requirements: omits synthetic-ops 2026 requirement OPS-1",
        ),
        (
            lambda p: p["requirements"].append(copy.deepcopy(p["requirements"][0])),
            "requirements[6]: duplicates requirements[0]",
        ),
        (
            lambda p: p["requirements"][1].update(id="APP-2-relabelled"),
            "requirements[1].id: not a requirement of the selected standard",
        ),
        (
            lambda p: p["requirements"][1].update(
                allowedMethods=["deterministic_check"]
            ),
            "requirements[1].allowedMethods: differs from the selected pack",
        ),
        (
            lambda p: p["requirements"][0].pop("title"),
            "requirements[0].title: differs from the selected pack",
        ),
        (
            lambda p: p["requirements"][2].update(methods=["deterministic_check"]),
            "requirements[2].methods[0]: not allowed by the selected pack",
        ),
        (
            lambda p: p["requirements"][3].update(gaps=[]),
            "requirements[3].gaps: omits a required no_evidence_path gap",
        ),
        (
            lambda p: p["requirements"][1].update(methods=[]),
            "requirements[1]: needs a planned evidence path or an explicit gap",
        ),
        (
            lambda p: p["standards"][1].update(title="Licensed title"),
            "standards[1].title: differs from the selected pack",
        ),
        (
            lambda p: p["requirements"][5].update(applicability="applicable"),
            "requirements[5].applicability: conflicts with the requirement choice",
        ),
    ],
)
def test_omitted_duplicated_relabelled_or_altered_content_is_field_addressed(
    change, expected
):
    plan = hand_authored()
    change(plan)
    assert expected in rejected(plan)


def test_external_only_and_organisational_requirements_are_never_a_scan():
    for index in (1, 2):
        plan = hand_authored()
        plan["requirements"][index]["jobIds"] = ["code-scan"]
        assert (
            f"requirements[{index}].jobIds: jobs evidence only deterministic checks"
            in rejected(plan)
        )


def test_job_inputs_use_native_contracts_and_refuse_unknown_kinds_and_targets():
    plan = hand_authored()
    plan["jobs"][1]["input"]["mode"] = "active"
    errors = rejected(plan)
    assert any(e.startswith("jobs[1].input.assessment:") for e in errors)

    plan = hand_authored()
    plan["jobs"][0]["kind"] = "port-sweep"
    assert "jobs[0].kind: unknown job kind" in rejected(plan)

    plan = hand_authored()
    other = "https://elsewhere.example.test"
    plan["jobs"][1]["input"]["origin"] = other
    plan["jobs"][1]["authority"]["targets"][0]["ref"] = other
    assert (
        "jobs[1].authority.targets[0]: outside the saved environment's candidates"
        in rejected(plan)
    )

    plan = hand_authored()
    plan["jobs"][1]["authority"]["actionClasses"] = ["web-active"]
    assert "jobs[1].authority: differs from the derived authority" in rejected(plan)

    plan = hand_authored()
    plan["subject"]["scope"]["assets"].append("db.internal.test")
    assert (
        "subject.scope.assets[2]: outside the saved environment's candidates"
        in rejected(plan)
    )
    plan = hand_authored()
    plan["subject"]["scope"]["exclusions"] = []
    assert "subject.scope.exclusions: drops a saved environment exclusion" in rejected(
        plan
    )


def test_plans_carry_no_credentials_or_secret_material():
    plan = hand_authored()
    plan["jobs"][1]["input"]["identity"] = {
        "name": "alice",
        "method": "bearer",
        "credentialRef": "db://alice-token",
        "verificationPath": "/",
        "loggedInMarker": "alice",
    }
    assert (
        "jobs[1].input.identity.credentialRef: bound at launch from a saved authorization; plans name credential purposes only"
        in rejected(plan)
    )
    secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    plan = hand_authored()
    plan["subject"]["systemBoundary"] = f"Shop; deploy token {secret}"
    errors = rejected(plan)
    assert (
        "subject.systemBoundary: contains secret material; cite a reference instead"
        in errors
    )
    assert secret not in "; ".join(errors)


def test_derived_job_authority_matches_existing_admission():
    from moonmind.schemas.container_job_models import ContainerJobSpec
    from moonmind.security.assessment_preset_inputs import assessment_job_spec
    from moonmind.security.security_plan import job_authority, native_job
    from moonmind.security.tls_assessments import build_tls_assessment_job
    from moonmind.security.workload_profiles import (
        AdmittedAssessmentScope,
        resolved_job_within_scope,
        selection_within_scope,
    )

    dns = {
        "questions": [{"name": "app.example.test", "recordType": "A"}],
        "timeoutSeconds": 60,
    }
    native = {
        "targets": [
            {"hosts": ["app.example.test"], "ports": [443], "purpose": "live-target"}
        ],
        "limits": {
            "maxConnections": 10,
            "maxBytes": 4096,
            "maxConnectionsPerSecond": 1,
            "maxConcurrentConnections": 1,
            "maxDurationSeconds": 60,
        },
    }
    cases = {
        "web-assessment": {
            "mode": "api",
            "origin": ORIGIN,
            "routes": [{"path": "/api", "methods": ["GET", "POST"]}],
            "startPaths": ["/api"],
            "apiDefinition": {"format": "openapi", "inputArtifactRef": "art_openapi"},
            "egressPolicyRef": "shop-egress",
        },
        "cloud-assessment": {
            "provider": "aws",
            "account": "123456789012",
            "regions": ["us-east-1"],
            "services": ["iam"],
            "egressPolicyRef": "aws-egress",
        },
        "tls-assessment": {"origin": ORIGIN, "egressPolicyRef": "shop-egress"},
        "dns-assessment": {
            "profileRef": "dns-resolver",
            "mode": "query",
            "egressPolicyRef": "resolver",
            "dnsAssessment": dns,
        },
        "service-inventory": {
            "profileRef": "inventory",
            "mode": "observe",
            "egressPolicyRef": "native",
            "nativeNetwork": native,
            "serviceInventory": {},
        },
    }
    for kind, value in cases.items():
        model, facts, scope = native_job(kind, value)
        authority = job_authority(kind, model, facts, scope)
        targets = []
        for target in authority.targets:
            if target.kind == "web_origin":
                routes = [r.model_dump(by_alias=True) for r in target.routes]
                targets.append(
                    {"webOrigin": target.ref, "webRoutes": routes or [{"path": "/"}]}
                )
            elif target.kind == "artifact":
                targets.append({"artifactRef": target.ref})
            elif target.kind == "cloud_account":
                targets.append(
                    {
                        "cloudAccount": target.ref,
                        "cloudProvider": "aws",
                        "cloudRegions": target.regions,
                    }
                )
            elif target.kind == "dns_names":
                targets.append({"dnsAssessment": dns})
            else:
                targets.append({"nativeNetwork": native})

        def admitted(**drop):
            values = {
                "assessmentRef": "plan-proof",
                "profileRefs": authority.profile_refs,
                "actionClasses": authority.action_classes,
                "targets": targets,
                "networkRefs": authority.network_refs,
                "maxJobs": 1,
                "maxJobSeconds": authority.max_job_seconds,
                "maxRequestsPerSecond": authority.max_requests_per_second,
                "evidenceIntent": "security plan admission proof",
            }
            values.update(drop)
            return AdmittedAssessmentScope.model_validate(values)

        stamped = {**value, "assessmentRef": "plan-proof"}
        if kind in {"dns-assessment", "service-inventory"}:
            selection = {
                k: v for k, v in stamped.items() if k not in {"egressPolicyRef"}
            }
            spec = ContainerJobSpec.model_validate(
                {
                    "imageSourceRef": "moonmind",
                    "workloadProfile": selection,
                    "inputAttachments": [],
                    "resources": {"cpuMillis": 500, "memoryMiB": 256, "pids": 32},
                    "timeoutSeconds": authority.max_job_seconds,
                }
            )
            assert selection_within_scope(spec, admitted())
            assert not selection_within_scope(spec, admitted(profileRefs=["other"]))
            if kind == "dns-assessment":
                assert not selection_within_scope(spec, admitted(actionClasses=["x"]))
            continue
        if kind == "cloud-assessment":
            stamped["credentialRef"] = "db://aws-inventory"
        job = (
            build_tls_assessment_job(
                model.model_copy(update={"assessment_ref": "plan-proof"})
            )
            if kind == "tls-assessment"
            else assessment_job_spec(kind, stamped)
        )
        spec = ContainerJobSpec.model_validate(job)
        resolved_job_within_scope(spec, admitted(), ())
        for field in ("actionClasses", "networkRefs"):
            with pytest.raises(PermissionError):
                resolved_job_within_scope(spec, admitted(**{field: ["other"]}), ())
        with pytest.raises(PermissionError):
            resolved_job_within_scope(
                spec, admitted(maxJobSeconds=authority.max_job_seconds - 1), ()
            )
    assert job_authority(
        "cloud-assessment", *native_job("cloud-assessment", cases["cloud-assessment"])
    ).credential_purposes == ["cloud_inventory"]


def test_application_access_authority_matches_its_profile_admission():
    from moonmind.security.security_plan import job_authority, native_job
    from tests.unit.security.test_application_access_target import scope

    target = scope("http://fixture.test").model_dump(mode="json", by_alias=True)
    target.pop("credentialSlots")
    value = {
        "profileRef": "application-access",
        "mode": "observe",
        "actionClass": "application-access",
        "targetScope": target,
    }
    authority = job_authority(
        "application-access", *native_job("application-access", value)
    )
    assert authority.action_classes == ["application-access", "web-active"]
    assert authority.credential_purposes == ["application_auth"]
    assert authority.network_refs == ["fixture"]
    with pytest.raises(SecurityPlanError, match="actionClass"):
        native_job(
            "application-access", {k: v for k, v in value.items() if k != "actionClass"}
        )
    with pytest.raises(SecurityPlanError, match="credentialSlots"):
        native_job(
            "application-access",
            {**value, "targetScope": {**target, "credentialSlots": {"db://x": "x"}}},
        )


def test_readiness_gaps_take_their_next_action_from_readiness_diagnostics():
    readiness = {
        "code-scan": {
            "status": "blocked",
            "checks": [
                {
                    "component": "feeds",
                    "status": "blocked",
                    "code": "feed_component_missing",
                },
                {
                    "component": "gitleaks",
                    "status": "blocked",
                    "code": "scanner_executable_unavailable",
                },
            ],
            "diagnostics": [],
        },
        "web-baseline": {
            "status": "blocked",
            "checks": [],
            "diagnostics": [
                {
                    "code": "credential_unavailable",
                    "action": READINESS_ACTIONS["credential_unavailable"],
                }
            ],
        },
    }
    # The hand-authored plan predates these results: its gaps are now missing.
    errors = rejected(hand_authored(), readiness=readiness)
    assert "jobs[0].readiness: differs from the derived readiness" in errors
    assert "requirements[0].gaps: omits a required feed_unavailable gap" in errors

    plan = derive_security_plan(hand_authored(), packs=packs(), readiness=readiness)
    plan = validate(plan, readiness=readiness)
    gaps = {
        (g["requirementId"], g["kind"], g.get("readinessCode")) for g in gap_list(plan)
    }
    assert {
        ("APP-1", "feed_unavailable", "feed_cache_unavailable"),
        ("APP-1", "tool_unavailable", "scanner_image_unavailable"),
        ("APP-1", "identity_unavailable", "credential_unavailable"),
        ("OPS-1", "feed_unavailable", "feed_cache_unavailable"),
    } <= gaps
    assert all(
        g["nextAction"] == READINESS_ACTIONS[g["readinessCode"]]
        for g in gap_list(plan)
        if g.get("readinessCode")
    )
    text = render_security_plan(plan)
    assert READINESS_ACTIONS["feed_cache_unavailable"].replace(".", "") in text.replace(
        ".", ""
    )

    plan = plan.model_dump(mode="json", by_alias=True)
    plan["requirements"][0]["gaps"][0]["nextAction"] = "Ignore this."
    assert (
        "requirements[0].gaps[0].nextAction: differs from the readiness diagnostic"
        in rejected(plan, readiness=readiness)
    )
    plan = hand_authored()
    plan["requirements"][3]["gaps"].append(
        {"kind": "tool_unavailable", "detail": "Scanner missing"}
    )
    assert "requirements[3].gaps[1].readinessCode: required for this gap" in rejected(
        plan
    )


def test_regulatory_facts_without_a_cited_operator_record_stay_undetermined():
    plan = hand_authored()
    plan["regulatoryFacts"][0]["status"] = "established"
    assert (
        "regulatoryFacts[0].status: stays undetermined without a cited operator record"
        in rejected(plan)
    )
    derived = validate(derive_security_plan(plan, packs=packs()))
    assert derived.regulatory_facts[0].status == "undetermined"

    plan["regulatoryFacts"][0].update(
        recordRef="art_lab_certificate",
        sources=source("document", "art_lab_certificate"),
    )
    assert validate(plan).regulatory_facts[0].status == "established"


def test_identity_changes_with_edition_choice_asset_or_job_input_only():
    base = plan_identity(validate(hand_authored()))
    assert plan_identity(validate(hand_authored())) == base

    plan = hand_authored()
    plan["standards"][1]["revision"] = "2"
    changed_packs = packs(ops_revision="2")
    assert plan_identity(validate(plan, packs=changed_packs)) != base

    plan = hand_authored()
    plan["standards"][0]["requirementChoices"] = {
        "APP-1": {"applicability": "applicable", "applicabilityReason": "In scope."}
    }
    assert plan_identity(validate(plan)) != base

    plan = hand_authored()
    plan["subject"]["scope"]["assets"] = [ORIGIN, "shop-app"]
    assert plan_identity(SecurityPlan.model_validate(plan)) != base

    plan = hand_authored()
    plan["jobs"][0]["input"]["outputDirectory"] = "scan-2"
    assert plan_identity(validate(plan)) != base


def test_plan_rows_map_onto_oscal_assessment_plan_procedures():
    from moonmind.security.oscal_export import Procedure

    plan = validate(hand_authored())
    oscal = {
        "deterministic_check": "TEST",
        "agent_interpretation": "EXAMINE",
        "document_review": "EXAMINE",
    }
    jobs = {job.id: job for job in plan.jobs}
    procedures = [
        Procedure(
            id=f"{row.pack_id}.{row.id}",
            control_id=row.id,
            objective=row.title or row.id,
            methods=sorted({oscal[m] for m in row.methods if m in oscal}),
            steps=[f"{jobs[j].kind} {j}" for j in row.job_ids]
            + [f"evidence request {r}" for r in row.evidence_request_ids],
            assets=[a for j in row.job_ids for a in jobs[j].assets]
            or plan.subject.scope.assets,
            responsible_party=uuid4(),
        )
        for row in plan.requirements
        if row.applicability != "not_applicable"
        and any(m in oscal for m in row.methods)
    ]
    assert {p.control_id: p.methods for p in procedures} == {
        "APP-1": ["TEST"],
        "APP-2": ["EXAMINE"],
        "OPS-1": ["TEST"],
    }


def test_retained_v1_plans_stay_readable_through_the_compatibility_reader():
    from moonmind.security.assessments import WebAPITestPlan
    from tests.unit.security.test_web_api_methodology import plan_payload

    raw = json.dumps(plan_payload()).encode()
    assert isinstance(read_methodology_plan(raw), WebAPITestPlan)
    assert isinstance(
        read_methodology_plan(json.dumps(hand_authored()).encode()), SecurityPlan
    )
    with pytest.raises(SecurityPlanError, match="schemaVersion"):
        read_methodology_plan(
            json.dumps({**hand_authored(), "schemaVersion": "x"}).encode()
        )


def test_methodology_reads_the_generalized_plan_exactly_like_a_v1_plan():
    from moonmind.security.web_api_methodology import (
        ProcedureResults,
        compare_methodology,
        evaluate_coverage,
    )
    from tests.unit.security.test_web_api_methodology import (
        prepared,
        result_payload,
        security_plan_payload,
    )

    v1, pack = prepared()
    payload, project = security_plan_payload()
    general = validate_security_plan(
        payload,
        packs={(pack.id, pack.edition): pack},
        snapshot=project,
        now=datetime.now(UTC),
    )
    assert "credentialRef" not in json.dumps(payload)
    results = ProcedureResults.model_validate(result_payload(v1))
    retained = evaluate_coverage(v1, pack, results, plan_ref="art_plan")
    current = evaluate_coverage(general, pack, results, plan_ref="art_plan")

    # Generalized requirements always state their applicability reason.
    def without_reasons(report):
        return {
            **report,
            "objectives": [
                {k: v for k, v in row.items() if k != "reason"}
                for row in report["objectives"]
            ],
        }

    assert without_reasons(current) == without_reasons(retained)
    assert compare_methodology(retained, current) == compare_methodology(
        retained, retained
    )
