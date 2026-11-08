"""Bounded quality cases through existing security owners (issue #5189)."""

from __future__ import annotations

import json
import re
from copy import deepcopy

import pytest

from api_service.services.presets.catalog import PresetCatalogService
from moonmind.security.assessments import FINDING_STATES
from moonmind.security.passive_repo_scan import run_passive_scan
from moonmind.workflows.skills import security_assessment
from tests.integration.security import test_admitted_assessment_journeys as admitted
from tests.integration.security import test_cloud_assessment_jobs as cloud
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security import test_web_assessment_jobs as web
from tests.integration.security.quality import MANIFEST, QualityRun, observation
from tests.unit.api.test_presets_service import template_db

environment = recurring.environment
provider = cloud.provider
target = web.target
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def case(case_id):
    return next(
        row for row in json.loads(MANIFEST.read_text())["cases"] if row["id"] == case_id
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "dropped-finding",
        "dropped-coverage",
        "false-clean",
        "false-fixed",
        "false-report",
    ],
)
async def test_quality_rejects_corrupted_assessment(
    environment, tmp_path, monkeypatch, corruption
):
    from tests.integration.security import quality

    # Rejected probes must not replace the real corpus run's retained result.
    monkeypatch.setattr(quality, "OUTPUT", tmp_path / "rejected-assessments")
    if corruption in {"dropped-finding", "dropped-coverage"}:
        original = security_assessment._passive_observation

        def corrupt(*args, **kwargs):
            result = original(*args, **kwargs).model_copy(deep=True)
            if corruption == "dropped-finding":
                result.findings = []
            else:
                result.coverage[0].assets = []
            return result

        monkeypatch.setattr(security_assessment, "_passive_observation", corrupt)
    elif corruption in {"false-clean", "false-fixed"}:
        original = security_assessment.compare_observations

        def corrupt(*args, **kwargs):
            result = deepcopy(original(*args, **kwargs))
            result["counts"]["new"] = 0
            if corruption == "false-clean":
                result["findings"] = []
            else:
                for row in result["findings"]:
                    row.update(
                        state="remediated_verified", verification="retest_passed"
                    )
                result["counts"]["remediated_verified"] = len(result["findings"])
            return result

        monkeypatch.setattr(security_assessment, "compare_observations", corrupt)
    else:
        original = security_assessment._summary

        def corrupt(comparison):
            comparison = deepcopy(comparison)
            comparison["counts"]["new"] = 0
            return original(comparison)

        monkeypatch.setattr(security_assessment, "_summary", corrupt)

    with pytest.raises(AssertionError):
        await test_source_quality(environment, tmp_path, "source-vulnerable")


async def report(service, summary_ref, assets, key, tmp_path):
    # A saved fixture preset uses the real catalog expansion and tool dispatch.
    payload = {
        "scope": {"assets": assets},
        "tools": ["retained-report"],
        "reportRefs": [summary_ref],
        "credentialRefs": [],
        "networkRefs": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
    }
    async with template_db(tmp_path) as sessions, sessions() as session:
        catalog = PresetCatalogService(session)
        await catalog.create_template(
            slug="security-quality",
            title="Controlled security assessment",
            description="Assess only the admitted fixture evidence.",
            tags=["security"],
            inputs_schema=[],
            required_capabilities=["sandbox"],
            annotations={"workflowPublish": {"mode": "none"}},
            steps=[
                {
                    "type": "tool",
                    "title": "Assess retained fixture",
                    "instructions": "Report the fixture observations and limits.",
                    "tool": {
                        "id": "security.assess",
                        "inputs": payload,
                        "requiredCapabilities": ["sandbox"],
                    },
                }
            ],
        )
        expanded = await catalog.expand_template(slug="security-quality", inputs={})
    preset_ref = await recurring.put(service, expanded)
    outputs = await recurring.invoke(
        service, expanded["steps"][0]["tool"]["inputs"], key=key
    )
    assert outputs["publicationOutcome"] == "published", outputs["diagnostics"]
    refs = [
        preset_ref,
        outputs["scanRef"],
        outputs["comparisonRef"],
        recurring.report_ref(outputs),
    ]
    native = await recurring.read(service, summary_ref)
    work = await recurring.read(service, outputs["scanRef"])
    comparison = await recurring.read(service, outputs["comparisonRef"])
    assessed = comparison["observation"]
    assert assessed == work["observation"]
    assert comparison["baselineStatus"] == "initial"
    assert comparison["decisions"] == []
    assert all(row["state"] == "new" for row in comparison["findings"])
    findings = [row["finding"] for row in comparison["findings"]]
    assert findings == assessed["findings"]

    if native.get("toolRef"):
        actual = source_facts(native, summary_ref)
        labels = [f"{row['ruleId']}@{row['asset']}" for row in findings]
        expected_assets = native["coveredFiles"]
        complete = native["verdict"] != "incomplete"
        assert all(
            row["source"] == row["tool"] == native["toolRef"]
            and row["evidenceRefs"] == [summary_ref]
            for row in findings
        )
    else:
        # Native identities connect assessment findings to the oracle's route or
        # resource labels. Equality also catches changed rules, assets or citations.
        assert sorted(map(canonical, findings)) == sorted(
            map(canonical, native["result"]["findings"])
        )
        expected_assets = native["result"]["examinedAssets"]
        complete = native["status"] == "complete" and bool(expected_assets)
        if native["schemaVersion"] == "cloud-posture/v1":
            native["qualitySummaryRef"] = summary_ref
            actual = cloud_facts(native)
            by_id = {row["findingUid"]: row for row in native["findings"]}
            labels = [
                f"{row['ruleId']}@{uid}"
                for row in findings
                for uid in by_id[row["nativeId"]]["resourceUids"]
            ]
        else:
            actual = web_facts(native, summary_ref)
            by_id = {row["findingId"]: row for row in native["findings"]}
            labels = [
                f"{row['finding']['ruleId']}@{by_id[row['findingId']]['route']}"
                for row in comparison["findings"]
            ]

    assert sorted(set(labels)) == actual["findings"]
    covered_assets = {asset for row in assessed["coverage"] for asset in row["assets"]}
    assert covered_assets == set(expected_assets)
    assert all(row["status"] == "complete" for row in assessed["coverage"]) == complete
    coverage_status = "complete" if complete else "stale_or_partial"
    assert comparison["coverageStatus"] == outputs["coverageStatus"] == coverage_status
    assert work["scanOutcome"] == ("completed" if complete else "incomplete")
    counts = {state: len(findings) if state == "new" else 0 for state in FINDING_STATES}
    counts["review_required"] = 0
    assert comparison["counts"] == outputs["counts"] == counts
    _, body = await service.read(
        artifact_id=refs[-1], access=recurring.access_for_test("workflow:assessment")
    )
    text = body.decode()
    assert f"Coverage: {coverage_status}. Baseline: initial." in text.splitlines()
    reported_counts = re.findall(r"^- ([a-z ]+): (\d+)$", text, re.MULTILINE)
    assert sorted(reported_counts) == sorted(
        (name.replace("_", " "), str(count)) for name, count in counts.items()
    )
    actual.update(
        findings=sorted(set(labels)),
        satisfied=complete and not findings,
        evidenceByFinding={
            label: [
                *actual["executionEvidence"],
                outputs["scanRef"],
                outputs["comparisonRef"],
                refs[-1],
            ]
            for label in labels
        },
        executionEvidence=[
            *actual["executionEvidence"],
            summary_ref,
            outputs["scanRef"],
            outputs["comparisonRef"],
            refs[-1],
        ],
    )
    if actual["outcome"] in {"clean", "vulnerable"}:
        actual["outcome"] = "vulnerable" if findings else "clean"
    if native.get("toolRef"):
        actual["covered"] = sorted(covered_assets)
    return actual, refs


def canonical(finding):
    return json.dumps(finding, sort_keys=True)


def source_facts(payload, native_ref):
    return observation(
        {
            "finding_present": "vulnerable",
            "clean_with_coverage": "clean",
            "incomplete": "partial",
        }[payload["verdict"]],
        [f"{row['category']}@sample.py" for row in payload["findings"]],
        payload["coveredFiles"],
        native_ref=native_ref,
        satisfied=payload["verdict"] == "clean_with_coverage",
    )


@pytest.mark.parametrize(
    "case_id", ["source-vulnerable", "source-clean", "source-held-out"]
)
async def test_source_quality(environment, tmp_path, case_id):
    run = QualityRun()
    source = tmp_path / "snapshot"
    source.mkdir()
    (source / "sample.py").write_text(case(case_id)["source"])
    native = run_passive_scan(source).to_payload()
    run.useful()
    ref = await recurring.put(environment.service, native)

    # Independent detector execution, not a replayed/cached observation.
    repeated_native = run_passive_scan(source).to_payload()
    repeat_ref = await recurring.put(environment.service, repeated_native)
    observed, reports = await report(
        environment.service, ref, ["sample.py"], case_id, tmp_path
    )
    repeat_path = tmp_path / "repeat"
    repeat_path.mkdir()
    repeated, repeat_reports = await report(
        environment.service, repeat_ref, ["sample.py"], case_id + "-repeat", repeat_path
    )
    await run.finish(
        environment.service,
        case_id,
        observed,
        [ref, repeat_ref, *reports, *repeat_reports],
        repeat=repeated,
        provenance={
            "tool": native["toolRef"],
            "rules": "outbound-scan plus prefixed credential assignments",
            "configuration": native["config"],
            "inputDigest": native["inputDigest"],
            "model": None,
            "topology": "real in-process passive detector -> artifact owner -> security.assess -> report",
        },
    )


def cloud_facts(summary):
    limits = summary["limitations"]
    outcome = (
        "canceled"
        if "execution_canceled" in limits
        else (
            "malformed-output"
            if "report_malformed" in limits
            else (
                "partial"
                if summary["status"] != "complete"
                else "clean" if summary["outcome"] == "clean" else "vulnerable"
            )
        )
    )
    return observation(
        outcome,
        [
            f"{row['checkId']}@{uid}"
            for row in summary["findings"]
            if row["status"] == "FAIL"
            for uid in row["resourceUids"]
        ],
        set(summary["coverage"]["regionsWithResults"])
        - set(summary["coverage"]["regionsWithErrors"]),
        native_ref=summary.get("nativeRef") or summary["qualitySummaryRef"],
        satisfied=summary["status"] == "complete" and summary["outcome"] == "clean",
    )


@pytest.mark.parametrize(
    "outcome", ["vulnerable", "clean", "partial", "malformed-output", "canceled"]
)
async def test_cloud_quality(environment, tmp_path, monkeypatch, provider, outcome):
    run = QualityRun()
    case_id = f"cloud-{outcome}"
    scenario = case(case_id)["scenario"]
    session = admitted.Session(environment, monkeypatch, scope=cloud.scope())
    seen = admitted.spy_publication(monkeypatch)
    harness = admitted.CliCloud(session, environment, tmp_path, monkeypatch, provider)
    summary, _, _, accepted = await harness.run(
        cloud.assessment(),
        case_id,
        scenario=scenario,
        terminal="canceled" if outcome == "canceled" else None,
    )
    run.useful()
    job_ids = [accepted.job_id]
    names = cloud.artifact_names(harness, accepted)
    summary["qualitySummaryRef"] = names["summary"]["artifactRef"]
    # Follow the production workflow order and read artifacts after job removal.
    await admitted.finish(seen["backend"], seen["request"], tmp_path)
    harness.assert_no_seed()
    refs = [row["artifactRef"] for row in names.values() if row.get("artifactRef")]
    observed, reports = await report(
        environment.service,
        names["summary"]["artifactRef"],
        [f"aws:{cloud.ACCOUNT}"],
        case_id,
        tmp_path,
    )
    refs += reports
    repeat = None
    if outcome == "clean":
        again, _, _, accepted = await harness.run(
            cloud.assessment(), case_id + "-repeat", scenario=scenario
        )
        job_ids.append(accepted.job_id)
        repeat_names = cloud.artifact_names(harness, accepted)
        again["qualitySummaryRef"] = repeat_names["summary"]["artifactRef"]
        await admitted.finish(seen["backend"], seen["request"], tmp_path)
        refs += [
            row["artifactRef"]
            for row in repeat_names.values()
            if row.get("artifactRef")
        ]
        repeat_path = tmp_path / "repeat"
        repeat_path.mkdir()
        repeat, repeat_reports = await report(
            environment.service,
            repeat_names["summary"]["artifactRef"],
            [f"aws:{cloud.ACCOUNT}"],
            case_id + "-repeat",
            repeat_path,
        )
        refs += repeat_reports
    await run.finish(
        environment.service,
        case_id,
        observed,
        refs,
        repeat=repeat,
        provenance={
            "tool": "prowler controlled binary transport",
            "execution": summary["execution"],
            "image": summary["image"],
            "configuration": cloud.assessment().model_dump(mode="json", by_alias=True),
            "model": None,
            "jobs": job_ids,
            "topology": "admitted CLI -> signed MCP -> job admission -> controlled provider/scanner -> worker publication -> cleanup -> artifact read -> security.assess -> report",
        },
    )


def web_facts(summary, summary_ref):
    auth_failed = (summary["coverage"]["authentication"] or {}).get(
        "status"
    ) == "failed"
    outcome = (
        "auth-failed"
        if auth_failed
        else (
            "partial"
            if summary["status"] != "complete"
            else "vulnerable" if summary["findings"] else "clean"
        )
    )
    return observation(
        outcome,
        [f"{row['ruleId']}@{row['route']}" for row in summary["findings"]],
        summary["coverage"]["testedRoutes"],
        native_ref=summary.get("nativeRef") or summary_ref,
        satisfied=summary["status"] == "complete" and not summary["findings"],
    )


@pytest.mark.parametrize("outcome", ["vulnerable", "clean", "auth-failed"])
async def test_web_quality(environment, tmp_path, monkeypatch, target, outcome):
    run = QualityRun()
    app, gateway = target
    case_id = f"web-{outcome}"
    session = admitted.Session(environment, monkeypatch, scope=web.scope(app))
    harness = admitted.CliWeb(session, environment, tmp_path, monkeypatch, app, gateway)
    path = "/fixed/search" if outcome == "clean" else "/search"
    options = {
        "routes": [{"path": path, "methods": ["GET"]}],
        "startPaths": [path],
        "exclusions": [],
    }
    if outcome == "auth-failed":
        options = {
            "identity": web.identity("alice"),
            "routes": [
                {"path": "/login", "methods": ["GET", "POST"]},
                {"path": "/account", "methods": ["GET"]},
            ],
            "startPaths": ["/account"],
            "exclusions": [],
        }
        harness.passwords["alice"] = "wrong-password"
    request = web.assessment(app, **options)
    summary, _, _, accepted, _ = await harness.run(request, case_id)
    run.useful()
    names = web.artifact_names(harness, accepted)
    await admitted.finish(harness.backend, harness.request, tmp_path)
    harness.assert_no_secret()
    refs = [row["artifactRef"] for row in names.values() if row.get("artifactRef")]
    observed, reports = await report(
        environment.service,
        names["summary"]["artifactRef"],
        [web.origin(app)],
        case_id,
        tmp_path,
    )
    refs += reports
    await run.finish(
        environment.service,
        case_id,
        observed,
        refs,
        provenance={
            "tool": "zap controlled binary transport",
            "execution": summary["execution"],
            "configuration": request.model_dump(mode="json", by_alias=True),
            "model": None,
            "jobId": accepted.job_id,
            "topology": "admitted CLI -> signed MCP -> job admission -> local app/proxy -> rendered ZAP plan -> worker publication -> cleanup -> artifact read -> security.assess -> report",
        },
    )
