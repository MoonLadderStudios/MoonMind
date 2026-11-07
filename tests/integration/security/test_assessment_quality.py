"""Bounded quality cases through existing security owners (issue #5189)."""

from __future__ import annotations

import json

import pytest

from api_service.services.presets.catalog import PresetCatalogService
from moonmind.security.passive_repo_scan import run_passive_scan
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
    return [
        preset_ref,
        outputs["scanRef"],
        outputs["comparisonRef"],
        recurring.report_ref(outputs),
    ]


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

    def facts(payload, native_ref):
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

    observed = facts(native, ref)
    # Independent detector execution, not a replayed/cached observation.
    repeated_native = run_passive_scan(source).to_payload()
    repeat_ref = await recurring.put(environment.service, repeated_native)
    reports = await report(environment.service, ref, ["sample.py"], case_id, tmp_path)
    await run.finish(
        environment.service,
        case_id,
        observed,
        [ref, repeat_ref, *reports],
        repeat=facts(repeated_native, repeat_ref),
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
    observed = cloud_facts(summary)
    # Follow the production workflow order and read artifacts after job removal.
    await admitted.finish(seen["backend"], seen["request"], tmp_path)
    harness.assert_no_seed()
    refs = [row["artifactRef"] for row in names.values() if row.get("artifactRef")]
    refs += await report(
        environment.service,
        names["summary"]["artifactRef"],
        [f"aws:{cloud.ACCOUNT}"],
        case_id,
        tmp_path,
    )
    repeat = None
    if outcome == "clean":
        again, _, _, accepted = await harness.run(
            cloud.assessment(), case_id + "-repeat", scenario=scenario
        )
        job_ids.append(accepted.job_id)
        repeat_names = cloud.artifact_names(harness, accepted)
        again["qualitySummaryRef"] = repeat_names["summary"]["artifactRef"]
        repeat = cloud_facts(again)
        await admitted.finish(seen["backend"], seen["request"], tmp_path)
        refs += [
            row["artifactRef"]
            for row in repeat_names.values()
            if row.get("artifactRef")
        ]
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
    observed = web_facts(summary, names["summary"]["artifactRef"])
    await admitted.finish(harness.backend, harness.request, tmp_path)
    harness.assert_no_secret()
    refs = [row["artifactRef"] for row in names.values() if row.get("artifactRef")]
    refs += await report(
        environment.service,
        names["summary"]["artifactRef"],
        [web.origin(app)],
        case_id,
        tmp_path,
    )
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
