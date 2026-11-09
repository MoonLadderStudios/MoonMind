"""Objective coverage and unchanged retest oracles for issue #5188."""

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]
PACK_PATH = ROOT / ".agents/skills/security-review/references/web-api.json"


def request_payload():
    return {
        "scope": {"assets": ["app"]},
        "tools": [],
        "methodology": {"planRef": "art_plan"},
        "credentialRefs": [],
        "networkRefs": [],
        "budget": {"baseSeconds": 30, "maxSeconds": 60, "progressStallSeconds": 30},
    }


def test_methodology_is_an_explicit_assessment_input_without_a_scanner():
    from moonmind.security.assessments import AssessmentRequest

    assert (
        AssessmentRequest.model_validate(request_payload()).methodology.plan_ref
        == "art_plan"
    )


def plan_payload(pack_ref="art_pack"):
    pack = json.loads(PACK_PATH.read_text())
    return {
        "schemaVersion": "web-api-test-plan/v1",
        "pack": {
            "artifactRef": pack_ref,
            "packId": pack["id"],
            "edition": pack["edition"],
            "revision": pack["revision"],
            "sha256": hashlib.sha256(PACK_PATH.read_bytes()).hexdigest(),
            "period": {
                "start": datetime.now(UTC).date().isoformat(),
                "end": datetime.now(UTC).date().isoformat(),
            },
        },
        "scope": {"assets": ["app"]},
        "candidateRef": "fixture:vulnerable",
        "systemBoundary": "Controlled web and API app; no external targets.",
        "threatModel": {
            "entryPoints": ["GET /invoices/2"],
            "trustBoundaries": ["caller to owner"],
            "invariants": ["Only the owner may read an invoice"],
        },
        "authorityRef": "art_authority",
        "interfaces": [
            {"id": "api", "asset": "app", "protocol": "https", "locator": "/invoices/2"}
        ],
        "identities": [
            {"id": "alice", "available": True, "credentialRef": "fixture-alice"}
        ],
        "sourceRefs": ["art_source"],
        "apiContextRefs": ["art_openapi"],
        "allowedMethods": [
            "security-review",
            "security-investigate",
            "application-access",
            "zap",
        ],
        "objectives": [
            {
                "id": r["id"],
                "applicability": "applicable",
                "procedures": [
                    {
                        "id": p["id"],
                        "assets": ["app"],
                        "interfaceIds": ["api"],
                        "identityIds": ["alice"],
                        "caseSpecDigest": (
                            "1" * 64 if p["method"] == "application-access" else None
                        ),
                        "caseRefs": (
                            ["read"] if p["method"] == "application-access" else []
                        ),
                    }
                    for p in r["procedures"]
                ],
            }
            for r in pack["requirements"]
        ],
    }


def prepared(payload=None):
    from moonmind.security.assessments import WebAPITestPlan
    from moonmind.security.framework_packs import load_declared_pack
    from moonmind.security.web_api_methodology import validate_plan

    plan = WebAPITestPlan.model_validate(payload or plan_payload())
    pack = load_declared_pack(
        PACK_PATH.read_bytes(),
        pack_id=plan.pack.pack_id,
        edition=plan.pack.edition,
        revision=plan.pack.revision,
    )
    validate_plan(plan, pack, now=datetime.now(UTC))
    return plan, pack


def result_payload(plan, *, outcome="clear"):
    return {
        "schemaVersion": "web-api-procedure-results/v1",
        "planRef": "art_plan",
        "runs": [
            {
                "objectiveId": o.id,
                "procedureId": p.id,
                "status": "completed",
                "evidenceRefs": ["art_reproduction"],
                **(
                    {
                        "nativeReportRef": "art_native",
                        "configurationRef": "selected-cases",
                    }
                    if p.case_refs
                    else {}
                ),
                "outcome": outcome,
                "evidenceKind": (
                    "reproduction"
                    if p.case_refs
                    else (
                        "tool_match"
                        if o.id in {"input-output", "configuration"}
                        else "review_judgment"
                    )
                ),
            }
            for o in plan.objectives
            for p in o.procedures
        ],
        "limitations": ["Controlled fixture only."],
        "reviewStatus": "pending",
        "reviewerNotes": ["Review the object ownership case."],
        "followUpTasks": ["Check equivalent admitted invoice routes."],
    }


def report(payload=None, results=None):
    from moonmind.security.web_api_methodology import (
        ProcedureResults,
        evaluate_coverage,
    )

    plan, pack = prepared(payload)
    results = result_payload(plan) if results is None else results
    return evaluate_coverage(
        plan, pack, ProcedureResults.model_validate(results), plan_ref="art_plan"
    )


def test_plan_precedes_runs_and_absent_results_never_become_clean_coverage():
    from moonmind.security.web_api_methodology import evaluate_coverage

    plan, pack = prepared()
    result = evaluate_coverage(plan, pack, None, plan_ref="art_plan")
    assert {r["status"] for r in result["objectives"]} == {"untested"}
    assert not result["complete"]
    assert all(row["methods"] for row in result["objectives"])
    assert result["methodologyEditions"] == {
        "WSTG": "4.2",
        "ASVS": "5.0.0",
        "API Security": "2023",
    }


@pytest.mark.parametrize(
    "gap", ["account", "endpoint", "protocol", "source", "skipped-logic"]
)
def test_clean_scanner_cannot_hide_missing_method_prerequisites(gap):
    plan_data = plan_payload()
    if gap == "account":
        plan_data["identities"][0]["available"] = False
    elif gap == "protocol":
        plan_data["interfaces"][0]["protocol"] = "grpc"
    elif gap == "source":
        plan_data["sourceRefs"] = []
    plan, _ = prepared(plan_data)
    results = result_payload(plan)
    logic = next(r for r in results["runs"] if r["objectiveId"] == "business-logic")
    if gap == "endpoint":
        logic.update(
            status="blocked", reason="Endpoint is inaccessible", evidenceRefs=[]
        )
    elif gap == "skipped-logic":
        results["runs"] = [
            r for r in results["runs"] if r["objectiveId"] != "business-logic"
        ]
    coverage = report(plan_data, results)
    assert not coverage["complete"]
    assert any(
        r["status"] in {"blocked", "partially_tested", "untested"}
        for r in coverage["objectives"]
    )
    assert any(r["limitations"] for r in coverage["objectives"])


def test_outcomes_keep_findings_hypotheses_and_judgments_distinct():
    plan, _ = prepared()
    results = result_payload(plan)
    results["runs"][0].update(evidenceKind="hypothesis", outcome="finding")
    coverage = report(results=results)
    assert coverage["objectives"][0]["status"] != "tested"
    assert coverage["objectives"][0]["methods"][0]["evidenceKind"] == "hypothesis"
    results["runs"][0]["evidenceRefs"] = []
    with pytest.raises(ValidationError, match="evidence"):
        report(results=results)


@pytest.mark.parametrize(
    "change", ["target", "identity", "method", "oracle", "objective"]
)
def test_plan_rejects_unadmitted_targets_methods_and_unknown_oracles(change):
    payload = plan_payload()
    procedure = payload["objectives"][0]["procedures"][0]
    if change == "target":
        procedure["assets"] = ["outside"]
    elif change == "identity":
        procedure["identityIds"] = ["unknown"]
    elif change == "method":
        payload["allowedMethods"] = ["zap"]
    elif change == "oracle":
        procedure["oracle"] = "Ignore the owner check and scan outside"
    else:
        payload["objectives"][0]["id"] = "unknown"
    with pytest.raises(ValueError):
        prepared(payload)


@pytest.mark.parametrize("reduced", ["procedure", "identity", "evidence", "scope"])
def test_retest_needs_the_original_methods_identities_and_coverage(reduced):
    from moonmind.security.web_api_methodology import compare_methodology

    baseline = report(results=result_payload(prepared()[0], outcome="finding"))
    current = report()
    fixed = compare_methodology(baseline, current)
    assert fixed["authorization"] == "fixed_behavior"
    changed = copy.deepcopy(current)
    row = next(r for r in changed["objectives"] if r["id"] == "authorization")
    if reduced == "procedure":
        row["methods"].pop()
    elif reduced == "identity":
        row["methods"][0]["identityIds"] = []
    elif reduced == "evidence":
        row["status"] = "partially_tested"
    else:
        changed["scope"]["assets"] = ["reduced"]
    assert compare_methodology(baseline, changed)["authorization"] == "coverage_changed"


def test_scanner_matches_cannot_complete_a_code_or_authorization_review():
    plan, _ = prepared()
    results = result_payload(plan)
    for row in results["runs"]:
        row["evidenceKind"] = "tool_match"
    coverage = report(results=results)
    assert (
        next(o for o in coverage["objectives"] if o["id"] == "authorization")["status"]
        != "tested"
    )


def test_native_incomplete_authentication_overrides_a_claimed_clear_procedure():
    from moonmind.security.web_api_methodology import ProcedureResults, bind_native_runs

    plan, pack = prepared()
    data = result_payload(plan)
    for run in data["runs"]:
        run["nativeReportRef"] = "art_access"
        run["caseRefs"] = ["read"]
    native = [
        {
            "scanner": "application-access",
            "summaryRef": "art_access",
            "target": "app",
            "specDigest": "1" * 64,
            "cases": [
                {
                    "caseRef": "read",
                    "identityRef": "alice",
                    "authenticationState": "unverified",
                    "coverage": "partial",
                    "outcome": "inconclusive",
                    "request": {"path": "/invoices/2"},
                }
            ],
        }
    ]
    results = bind_native_runs(
        plan, pack, ProcedureResults.model_validate(data), native
    )
    coverage = report(results=results.model_dump(mode="json", by_alias=True))
    assert (
        next(o for o in coverage["objectives"] if o["id"] == "authorization")["status"]
        != "tested"
    )


def test_blocked_hypotheses_stay_blocked_without_claiming_partial_execution():
    plan, _ = prepared()
    data = result_payload(plan)
    data["runs"][0].update(
        status="blocked",
        evidenceKind="hypothesis",
        evidenceRefs=[],
        reason="Required account is unavailable",
    )
    coverage = report(results=data)
    assert coverage["objectives"][0]["status"] == "blocked"


def test_explicit_non_applicability_is_visible_and_requires_a_reason():
    payload = plan_payload()
    payload["objectives"][0].update(
        applicability="not_applicable",
        reason="This admitted interface has no sessions.",
        procedures=[],
    )
    coverage = report(payload)
    assert coverage["objectives"][0]["status"] == "not_applicable"
    assert coverage["objectives"][0]["reason"]
    payload["objectives"][0].pop("reason")
    with pytest.raises(ValidationError, match="reason"):
        prepared(payload)


def test_reviewed_results_need_attribution_but_pending_review_does_not_gate_evidence():
    plan, _ = prepared()
    data = result_payload(plan)
    assert report(results=data)["complete"]
    data["reviewStatus"] = "reviewed"
    with pytest.raises(ValidationError, match="reviewer"):
        report(results=data)
    data["reviewer"] = "controlled-reviewer"
    assert report(results=data)["reviewer"] == "controlled-reviewer"


def test_metadata_revision_alone_does_not_invalidate_unchanged_retest_methods():
    from moonmind.security.web_api_methodology import compare_methodology

    before = report(results=result_payload(prepared()[0], outcome="finding"))
    after = report()
    after["pack"].update(revision="2", sha256="different-description-bytes")
    assert compare_methodology(before, after)["authorization"] == "fixed_behavior"


@pytest.mark.parametrize("gap", ["removed-case", "identity", "endpoint"])
def test_native_cases_cannot_hide_reduced_authorization_coverage(gap):
    from moonmind.security.web_api_methodology import ProcedureResults, bind_native_runs

    plan, pack = prepared()
    data = result_payload(plan)
    for run in data["runs"]:
        run.update(nativeReportRef="art_access", caseRefs=["read"])
    case = {
        "caseRef": "read",
        "identityRef": "alice",
        "authenticationState": "verified",
        "coverage": "complete",
        "outcome": "pass",
        "request": {"path": "/invoices/2"},
    }
    if gap == "identity":
        case["identityRef"] = "another-principal"
    elif gap == "endpoint":
        case["request"]["path"] = "/untested"
    native = [
        {
            "scanner": "application-access",
            "summaryRef": "art_access",
            "target": "app",
            "specDigest": "1" * 64,
            "cases": [] if gap == "removed-case" else [case],
        }
    ]
    bound = bind_native_runs(plan, pack, ProcedureResults.model_validate(data), native)
    result = report(results=bound.model_dump(mode="json", by_alias=True))
    assert (
        next(o for o in result["objectives"] if o["id"] == "authorization")["status"]
        == "partially_tested"
    )


@pytest.mark.parametrize("method", ["application-access", "zap"])
@pytest.mark.parametrize("gap", ["endpoint", "asset"])
@pytest.mark.parametrize("outcome", ["clear", "finding"])
def test_native_report_does_not_cover_unexecuted_selections(method, gap, outcome):
    from moonmind.security.web_api_methodology import (
        ProcedureResults,
        bind_native_runs,
        compare_methodology,
    )

    payload = plan_payload()
    objective_id = (
        "authorization" if method == "application-access" else "configuration"
    )
    objective = next(o for o in payload["objectives"] if o["id"] == objective_id)
    missing_asset = "unassessed-app" if gap == "asset" else "app"
    missing_path = "/unexecuted" if gap == "endpoint" else "/invoices/2"
    if gap == "asset":
        payload["scope"]["assets"].append(missing_asset)
        for procedure in objective["procedures"]:
            procedure["assets"].append(missing_asset)
    payload["interfaces"].append(
        {
            "id": "unexecuted",
            "asset": missing_asset,
            "protocol": "https",
            "locator": missing_path,
        }
    )
    for procedure in objective["procedures"]:
        procedure["interfaceIds"].append("unexecuted")
    plan, pack = prepared(payload)
    data = result_payload(plan)
    for run in data["runs"]:
        run.update(nativeReportRef="art_native", caseRefs=["read"])
    native = {
        "scanner": method,
        "summaryRef": "art_native",
        "target": "app",
        "specDigest": "1" * 64,
        "coverage": "complete",
        "methodologyRoutes": ["/invoices/2"],
        "methodologyOutcome": outcome,
        "cases": [
            {
                "caseRef": "read",
                "identityRef": "alice",
                "authenticationState": "verified",
                "coverage": "complete",
                "outcome": "finding" if outcome == "finding" else "pass",
                "request": {"path": "/invoices/2"},
            }
        ],
    }
    bound = bind_native_runs(
        plan, pack, ProcedureResults.model_validate(data), [native]
    )
    result = report(payload, bound.model_dump(mode="json", by_alias=True))
    row = next(o for o in result["objectives"] if o["id"] == objective_id)
    procedure = next(p for p in row["methods"] if p["method"] == method)
    assert row["status"] == "partially_tested"
    assert procedure["status"] == "partial"
    assert any(
        (missing_asset if gap == "asset" else missing_path) in reason
        for reason in procedure["limitations"]
    )
    assert procedure["outcome"] == (
        "finding" if outcome == "finding" else "inconclusive"
    )
    assert procedure["oracle"]
    assert procedure["nativeReportRef"] == "art_native"
    assert not result["complete"]
    # Historical optimistic coverage must not establish a fix on a reduced retest.
    baseline = report(payload, result_payload(plan, outcome="finding"))
    assert compare_methodology(baseline, result)[objective_id] == "coverage_changed"


def test_native_cases_can_cover_multiple_selected_endpoints():
    from moonmind.security.web_api_methodology import ProcedureResults, bind_native_runs

    payload = plan_payload()
    payload["interfaces"].append(
        {"id": "second", "asset": "app", "protocol": "https", "locator": "/invoices/3"}
    )
    for objective in payload["objectives"]:
        for procedure in objective["procedures"]:
            procedure["interfaceIds"].append("second")
            if procedure["caseRefs"]:
                procedure["caseRefs"].append("read-second")
    plan, pack = prepared(payload)
    data = result_payload(plan)
    for run in data["runs"]:
        run.update(nativeReportRef="art_native", caseRefs=["read", "read-second"])
    native = {
        "scanner": "application-access",
        "summaryRef": "art_native",
        "target": "app",
        "specDigest": "1" * 64,
        "cases": [
            {
                "caseRef": ref,
                "identityRef": "alice",
                "authenticationState": "verified",
                "coverage": "complete",
                "outcome": "pass",
                "request": {"path": path},
            }
            for ref, path in [("read", "/invoices/2"), ("read-second", "/invoices/3")]
        ],
    }
    bound = bind_native_runs(
        plan, pack, ProcedureResults.model_validate(data), [native]
    )
    coverage = report(payload, bound.model_dump(mode="json", by_alias=True))
    authorization = next(
        o for o in coverage["objectives"] if o["id"] == "authorization"
    )
    assert authorization["status"] == "tested"
    assert not authorization["limitations"]


def test_clean_scanner_absence_is_distinct_from_a_reproduced_behavior_fix():
    from moonmind.security.web_api_methodology import compare_methodology

    before = report(results=result_payload(prepared()[0], outcome="finding"))
    after = report()
    assert compare_methodology(before, after)["configuration"] == "absent_comparable"


@pytest.mark.parametrize("current_kind", ["tool_match", "reproduction"])
def test_reproduced_scanner_finding_needs_a_passing_current_reproduction(current_kind):
    from moonmind.security.web_api_methodology import compare_methodology

    plan, _ = prepared()
    reports = []
    for outcome, kind in [("finding", "reproduction"), ("clear", current_kind)]:
        data = result_payload(plan, outcome=outcome)
        run = next(r for r in data["runs"] if r["objectiveId"] == "configuration")
        run.update(
            evidenceKind=kind,
            nativeReportRef="art_" + outcome,
            configurationRef="same-scanner-configuration",
        )
        reports.append(report(results=data))
    assert compare_methodology(*reports)["configuration"] == (
        "fixed_behavior" if current_kind == "reproduction" else "absent_comparable"
    )


@pytest.mark.parametrize("native_identity", [None, "anonymous", "bob", "alice"])
def test_zap_coverage_requires_the_selected_native_identity(native_identity):
    from moonmind.security.web_api_methodology import ProcedureResults, bind_native_runs

    plan, pack = prepared()
    data = result_payload(plan)
    run = next(r for r in data["runs"] if r["objectiveId"] == "configuration")
    run["nativeReportRef"] = "art_scan"
    summary = {
        "scanner": "zap",
        "summaryRef": "art_scan",
        "target": "app",
        "coverage": "complete",
        "methodologyRoutes": ["/invoices/2"],
        "methodologyOutcome": "clear",
        "methodologyIdentity": native_identity,
        "configurationRef": "scanner-config",
    }
    bound = bind_native_runs(
        plan, pack, ProcedureResults.model_validate(data), [summary]
    )
    result = report(results=bound.model_dump(mode="json", by_alias=True))
    row = next(o for o in result["objectives"] if o["id"] == "configuration")
    assert row["status"] == (
        "tested" if native_identity == "alice" else "partially_tested"
    )
    if native_identity != "alice":
        assert any("identit" in limitation for limitation in row["limitations"])


@pytest.mark.parametrize("method", ["zap", "application-access"])
@pytest.mark.parametrize("gap", [None, "report", "target", "endpoint", "identity"])
@pytest.mark.parametrize("outcome", ["clear", "finding"])
def test_multiple_native_reports_cover_only_their_actual_selections(
    method, gap, outcome
):
    from moonmind.security.web_api_methodology import ProcedureResults, bind_native_runs

    payload = plan_payload()
    payload["scope"]["assets"].append("second-app")
    payload["interfaces"].append(
        {
            "id": "second",
            "asset": "second-app",
            "protocol": "https",
            "locator": "/invoices/3",
        }
    )
    objective_id = "configuration" if method == "zap" else "authorization"
    objective = next(o for o in payload["objectives"] if o["id"] == objective_id)
    for selected in objective["procedures"]:
        selected["assets"].append("second-app")
        selected["interfaceIds"].append("second")
        if selected["caseRefs"]:
            selected["caseRefs"].append("read-second")
    plan, pack = prepared(payload)
    data = result_payload(plan)
    run = next(
        r
        for r in data["runs"]
        if r["objectiveId"] == objective_id
        and (method == "zap" or r["procedureId"] == "ownership-cases")
    )
    run.pop("nativeReportRef", None)
    run.update(
        nativeReportRefs=["art_first", "art_second"],
        caseRefs=["read", "read-second"] if method == "application-access" else [],
    )
    summaries = [
        {
            "scanner": method,
            "summaryRef": ref,
            "target": asset,
            "specDigest": "1" * 64,
            "coverage": "complete",
            "methodologyRoutes": [path],
            "methodologyOutcome": outcome,
            "methodologyIdentity": "alice",
            "configurationRef": "scanner-config",
            "cases": [
                {
                    "caseRef": case,
                    "identityRef": "alice",
                    "authenticationState": "verified",
                    "coverage": "complete",
                    "outcome": "finding" if outcome == "finding" else "pass",
                    "request": {"path": path},
                }
            ],
        }
        for ref, asset, path, case in [
            ("art_first", "app", "/invoices/2", "read"),
            ("art_second", "second-app", "/invoices/3", "read-second"),
        ]
    ]
    if gap == "report":
        summaries.pop()
    elif gap == "target":
        summaries[1]["target"] = "app"
    elif gap == "endpoint":
        summaries[1]["methodologyRoutes"] = ["/unselected"]
        summaries[1]["cases"][0]["request"]["path"] = "/unselected"
    elif gap == "identity":
        summaries[1]["methodologyIdentity"] = "bob"
        summaries[1]["cases"][0]["identityRef"] = "bob"
    bound = bind_native_runs(
        plan, pack, ProcedureResults.model_validate(data), summaries
    )
    result = report(payload, bound.model_dump(mode="json", by_alias=True))
    row = next(o for o in result["objectives"] if o["id"] == objective_id)
    selected = next(p for p in row["methods"] if p["method"] == method)
    assert row["status"] == ("tested" if gap is None else "partially_tested")
    assert selected["nativeReportRefs"] == ["art_first", "art_second"]
    assert selected["outcome"] == (
        "finding" if outcome == "finding" else "inconclusive" if gap else "clear"
    )
    assert bool(row["limitations"]) is (gap is not None)


def test_application_content_cannot_replace_the_pre_execution_case_oracle():
    from moonmind.security.web_api_methodology import ProcedureResults, bind_native_runs

    payload = plan_payload()
    for objective in payload["objectives"]:
        for procedure in objective["procedures"]:
            if procedure["caseRefs"]:
                procedure["caseSpecDigest"] = "1" * 64
    plan, pack = prepared(payload)
    data = result_payload(plan)
    for run in data["runs"]:
        run.update(nativeReportRef="art_access", caseRefs=["read"])
    native = [
        {
            "scanner": "application-access",
            "summaryRef": "art_access",
            "target": "app",
            "specDigest": "2"
            * 64,  # Same case ID, but changed expected-access assertions.
            "cases": [
                {
                    "caseRef": "read",
                    "identityRef": "alice",
                    "authenticationState": "verified",
                    "coverage": "complete",
                    "outcome": "pass",
                    "request": {"path": "/invoices/2"},
                }
            ],
        }
    ]
    results = bind_native_runs(
        plan, pack, ProcedureResults.model_validate(data), native
    )
    coverage = report(payload, results.model_dump(mode="json", by_alias=True))
    assert (
        next(o for o in coverage["objectives"] if o["id"] == "authorization")["status"]
        == "partially_tested"
    )
