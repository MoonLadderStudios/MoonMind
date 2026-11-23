"""OWASP ASVS 5.0.0 Level 1 as general best practices, on the quality fixtures.

The passive secret scan runs on the existing quality cases; Opengrep evidence is
the real scanner's report for vulnerable and repaired sources; Trivy summaries
are retained as the scanner jobs write them. security.assess, the installed pack,
the shared evaluator, the Top 10:2025 lens, report retention and rendering are
the real owners (MoonLadderStudios/MoonMind#5434).
"""

import json
from datetime import UTC, datetime

import pytest

from moonmind.security.framework_registry import installed_pack, readiness_catalog
from moonmind.security.passive_repo_scan import run_passive_scan
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.quality import MANIFEST
from tests.integration.security.quality import OUTPUT as QUALITY_OUTPUT
from tests.integration.security.test_web_api_methodology_journey import (
    put_plan,
    retain_plan,
    select_methodology,
)
from tests.unit.security.test_owasp_asvs_pack import (
    opengrep_summary,
    trivy_dependency_summary,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment
ASSETS = ["app.js", "app.py", "requirements.txt", "sample.py"]
BUDGET = {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60}


def quality_case(case_id):
    return next(
        row for row in json.loads(MANIFEST.read_text())["cases"] if row["id"] == case_id
    )


def general_best_practices(assets=ASSETS):
    """The catalog's general best-practices choice, as a selection."""
    entry = next(
        e for e in readiness_catalog() if e["choice"] == "general-best-practices"
    )
    day = datetime.now(UTC).date().isoformat()
    choices = {
        row.id: {
            "applicability": "applicable",
            "applicabilityReason": "Controlled fixture application in scope.",
        }
        for row in installed_pack(entry["id"])[1].requirements
    }
    # The installed eval rules run on the source files they can examine.
    choices["V1.3.2"]["systems"] = [a for a in assets if a.startswith("app.")]
    return {
        "packId": entry["id"],
        "edition": entry["edition"],
        "revision": entry["revision"],
        "period": {"start": day, "end": day},
        "requirementChoices": choices,
    }


async def retained_reports(service, tmp_path, *, vulnerable):
    """Retained evidence of the composed general best-practices checks."""
    source = tmp_path / ("vulnerable" if vulnerable else "clean")
    source.mkdir()
    case = quality_case("source-vulnerable" if vulnerable else "source-clean")
    (source / "sample.py").write_text(case["source"])
    passive = await recurring.put(service, run_passive_scan(source).to_payload())
    opengrep = opengrep_summary(
        "vulnerable" if vulnerable else "repaired",
        native_ref=await recurring.put(service, {"results": []}),
    )
    trivy = trivy_dependency_summary(
        native_ref=await recurring.put(service, {"Results": []}), vulnerable=vulnerable
    )
    return [
        passive,
        await recurring.put(service, opengrep.model_dump(mode="json", by_alias=True)),
        await recurring.put(service, trivy.model_dump(mode="json", by_alias=True)),
    ]


async def assess(service, tmp_path, *, vulnerable, key):
    request = {
        "scope": {"assets": ASSETS, "exclusions": [], "repositoryExceptions": []},
        "tools": ["retained-report"],
        "reportRefs": await retained_reports(service, tmp_path, vulnerable=vulnerable),
        "credentialRefs": [],
        "networkRefs": [],
        "budget": BUDGET,
        "frameworkPacks": [general_best_practices()],
    }
    outputs = await recurring.invoke(service, request, key=key)
    assert outputs["controlAssessmentStatus"] == "completed", outputs
    report = await recurring.read(service, outputs["controlAssessmentRef"])
    _, text = await service.read(
        artifact_id=recurring.report_ref(outputs),
        access=recurring.access_for_test("workflow:assessment"),
    )
    evidence = QUALITY_OUTPUT / "owasp-asvs-l1"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / f"{key}-report.md").write_bytes(text)
    (evidence / f"{key}-control.json").write_text(json.dumps(report, indent=2))
    rows = {row["requirement"]["id"]: row for row in report["requirements"]}
    return rows, report, text.decode()


@pytest.mark.parametrize("vulnerable", [False, True])
async def test_general_best_practices_concludes_per_requirement_with_a_top10_lens(
    environment, tmp_path, vulnerable
):
    rows, report, text = await assess(
        environment.service,
        tmp_path,
        vulnerable=vulnerable,
        key="vulnerable" if vulnerable else "clean",
    )
    assert len(rows) == 70
    # A clean scan alone satisfies no requirement that needs review.
    assert not [key for key, row in rows.items() if row["conclusion"] == "satisfied"]
    eval_row = rows["V1.3.2"]
    assert eval_row["objectives"][0]["outcome"] == (
        "failed" if vulnerable else "passed"
    )
    assert eval_row["conclusion"] == ("not_satisfied" if vulnerable else "inconclusive")
    assert (rows["V14.3.1"]["conclusion"], rows["V14.3.1"]["limitations"][0][:19]) == (
        "not_assessed",
        "Not assessed here: ",
    )
    assert rows["V15.1.1"]["organisationalEvidence"][0]["state"] == "requested"
    for key, row in rows.items():
        plan = row["requirement"]["evidencePlan"]
        assert plan.get("methods") or plan.get("notAssessedReason"), key

    pack = report["frameworkPack"]
    assert pack["attribution"].startswith("Requirement text: OWASP Application")
    (lens,) = pack["reportingLenses"]
    categories = {row["id"]: row for row in lens["categories"]}
    assert not any(row["status"] == "passed" for row in categories.values())
    supply = categories["A03:2025"]
    findings = {
        (f["source"], f["ruleId"]) for c in categories.values() for f in c["findings"]
    }
    if vulnerable:
        assert [f["ruleId"] for f in supply["findings"]] == ["CVE-2026-0001"]
        assert ("moonmind.security.passive_repo_scan.v1", "credential") in {
            (f["source"], f["ruleId"]) for f in categories["A07:2025"]["findings"]
        }
        assert {
            f["ruleId"].rsplit(".", 1)[1] for f in categories["A05:2025"]["findings"]
        } == {
            "moonmind-python-dynamic-eval",
            "moonmind-python-shell-command",
            "moonmind-javascript-dynamic-eval",
        }
    else:
        assert not findings
    assert supply["status"] == "assessed"
    assert {m["basis"] for m in supply["mappings"]} == {"upstream", "evaluated"}
    for key in ("A09:2025", "A10:2025"):
        assert categories[key]["status"] == "unmapped"

    assert "Framework pack: owasp-asvs-l1 / 5.0.0 / 1." in text
    assert (
        "Attribution: Requirement text: OWASP Application Security Verification Standard 5.0.0"
        in text
    )
    assert "#### Reporting lens: owasp-top-10 2025" in text
    assert (
        "A10:2025 Mishandling of Exceptional Conditions: no mapping in this pack; not assessed, not a pass."
        in text
    )
    assert ("CVE-2026-0001 on requirements.txt" in text) is vulnerable
    assert "- V14.3.1: **not_assessed** (not_assessed); not assessed here:" in text


async def test_methodology_coverage_states_survive_beside_asvs_conclusions(environment):
    """The six web/API procedures keep their own coverage states and report."""
    service = environment.service
    _, plan = await retain_plan(service)
    plan_ref = await put_plan(service, plan)
    runs = [
        {
            "objectiveId": "authorization",
            "procedureId": "ownership-review",
            "status": "partial",
            "reason": "Selected review coverage only.",
            "outcome": "clear",
            "evidenceKind": "review_judgment",
            "evidenceRefs": plan["sourceRefs"],
        },
        {
            "objectiveId": "data-exposure",
            "procedureId": "response-review",
            "status": "completed",
            "outcome": "clear",
            "evidenceKind": "review_judgment",
            "evidenceRefs": plan["sourceRefs"],
        },
        {
            "objectiveId": "business-logic",
            "procedureId": "invariant-review",
            "status": "blocked",
            "reason": "Reviewer unavailable in this controlled run.",
            "outcome": "inconclusive",
            "evidenceKind": "review_judgment",
        },
    ]
    results_ref = await recurring.put(
        service,
        {
            "planRef": plan_ref,
            "runs": runs,
            "reviewStatus": "pending",
            "limitations": ["Controlled fixture; assessor qualification Unverified."],
        },
    )
    selection = general_best_practices(assets=["app"])
    selection["requirementChoices"]["V1.3.2"].pop("systems")
    request = {
        "tools": [],
        "credentialRefs": [],
        "networkRefs": [],
        "budget": BUDGET,
        "frameworkPacks": [selection],
    }
    request = await select_methodology(service, request, plan_ref)
    request["methodology"]["resultsRef"] = results_ref
    outputs = await recurring.invoke(service, request, key="asvs-methodology")
    methodology = await recurring.read(service, outputs["methodologyRef"])
    states = {row["id"]: row["status"] for row in methodology["objectives"]}
    assert states["authorization"] == "partially_tested"
    assert states["data-exposure"] == "tested"
    assert states["business-logic"] == "blocked"
    assert states["input-output"] == "untested"
    assert outputs["methodologyStatus"] == "incomplete"
    report = await recurring.read(service, outputs["controlAssessmentRef"])
    rows = {row["requirement"]["id"]: row for row in report["requirements"]}
    assert rows["V15.3.1"]["requirement"]["evidencePlan"]["procedures"] == [
        "response-review"
    ]
    # A tested review procedure is evidence for a reviewer, not a conclusion.
    assert rows["V15.3.1"]["conclusion"] == "inconclusive"
    assert not [key for key, row in rows.items() if row["conclusion"] == "satisfied"]
    _, text = await service.read(
        artifact_id=recurring.report_ref(outputs),
        access=recurring.access_for_test("workflow:assessment"),
    )
    summary = text.decode()
    for objective, state in states.items():
        assert f"- {objective} — {state};" in summary
    assert "procedures: ownership-review, ownership-cases" in summary
