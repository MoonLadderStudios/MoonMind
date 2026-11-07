"""Requirement evidence through the real admitted tool and artifact owners."""

from datetime import UTC, datetime, timedelta

import pytest

from moonmind.workflows.skills import security_assessment
from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
from tests.integration.security import test_recurring_assessments as recurring
from tests.unit.security.test_control_assessments import requirement, supporting

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment


async def inputs(service, *, vulnerable=False):
    request, inventory, _feed = await recurring.inputs(service)
    if not vulnerable:
        inventory["components"][0]["version"] = "2.0"
        request["inventoryRef"] = await recurring.put(service, inventory)
    day = datetime.now(UTC).date().isoformat()
    row = requirement(
        period={"start": day, "end": day},
        objectives=[
            {
                "id": "check",
                "description": "No selected advisory matches the scoped inventory",
                "source": "fixture-advisories",
                "tool": "inventory-feed",
                "ruleIds": ["CVE-EXAMPLE"],
            }
        ],
    )
    request["controlAssessment"] = {"requirements": [row]}
    return request


async def result_row(service, outputs):
    assert outputs["controlAssessmentStatus"] == "completed", outputs
    report = await recurring.read(service, outputs["controlAssessmentRef"])
    return report["requirements"][0]


async def test_admitted_control_report_retains_native_evidence_and_structured_access(
    environment,
):
    service = environment.service
    request = await inputs(service)
    result = await recurring.invoke(service, request)
    row = await result_row(service, result)
    assert row["conclusion"] == "satisfied"
    assert result["controlAssessmentCounts"]["satisfied"] == 1
    assert (
        result["scanOutcome"] == "completed"
        and result["publicationOutcome"] == "published"
    )
    assert (
        result["reportBundle"]["structured_ref"]["artifact_id"]
        == result["controlAssessmentRef"]
    )
    proof = await recurring.required(service, result["controlAssessmentRef"])
    assert {
        result["scanRef"],
        result["comparisonRef"],
        request["inventoryRef"],
        request["feedRef"],
    } <= proof
    _, summary = await service.read(
        artifact_id=recurring.report_ref(result),
        access=recurring.access_for_test("workflow:assessment"),
    )
    assert (
        b"Requirement assessments" in summary
        and result["comparisonRef"].encode() in summary
    )
    scan = await recurring.read(service, result["scanRef"])
    assert scan["observation"]["coverageWindows"][0]["observedAt"]


async def test_accepted_risk_is_not_satisfaction_through_actual_producer(environment):
    service = environment.service
    request = await inputs(service, vulnerable=True)
    first = await recurring.invoke(service, request)
    comparison = await recurring.read(service, first["comparisonRef"])
    finding_id = comparison["findings"][0]["findingId"]
    decision = recurring.decision(finding_id, [request["inventoryRef"]])
    decision.update(
        disposition="accepted_risk",
        retestOutcome=None,
        author="Fixture owner",
        reason="Temporary risk acceptance",
    )
    request["decisionsRef"] = await recurring.put(service, [decision])
    second = await recurring.invoke(service, request)
    row = await result_row(service, second)
    assert row["conclusion"] == "not_satisfied"
    assert row["exceptions"][0]["decision"]["disposition"] == "accepted_risk"
    assert row["exceptions"][0]["decision"]["author"] == "Fixture owner"
    assert first["scanRef"] == second["scanRef"]


@pytest.mark.parametrize("denied", [False, True])
async def test_missing_or_foreign_required_evidence_does_not_discard_native_results(
    environment, denied
):
    service = environment.service
    request = await inputs(service)
    reference = (
        await recurring.put(
            service, {"secret": "foreign-control-marker"}, workflow_id="foreign"
        )
        if denied
        else "art_missing_control_evidence"
    )
    requirement = request["controlAssessment"]["requirements"][0]
    requirement["supportingEvidence"] = [
        supporting(artifactRef=reference, period=requirement["period"])
    ]
    outputs = await recurring.invoke(service, request)
    row = await result_row(service, outputs)
    assert outputs["scanOutcome"] == "completed"
    assert outputs["publicationOutcome"] == "published"
    assert row["conclusion"] == "inconclusive" and row["coverage"] == "blocked"
    assert row["unavailableEvidenceRefs"] == [reference]
    assert reference not in await recurring.required(
        service, outputs["controlAssessmentRef"]
    )
    _, summary = await service.read(
        artifact_id=recurring.report_ref(outputs),
        access=recurring.access_for_test("workflow:assessment"),
    )
    assert b"foreign-control-marker" not in summary


async def test_publication_retry_reuses_scan_comparison_and_control_artifacts(
    environment, monkeypatch
):
    service = environment.service
    request = await inputs(service)
    original = service.link_artifact

    async def fail_publication(**kwargs):
        if kwargs["execution_ref"]["link_type"] == "report.structured":
            raise ConnectionError("fixture publication failure")
        return await original(**kwargs)

    monkeypatch.setattr(service, "link_artifact", fail_publication)
    first = await recurring.invoke(service, request)
    assert first["publicationOutcome"] == "failed"
    assert (await result_row(service, first))["conclusion"] == "satisfied"
    monkeypatch.setattr(service, "link_artifact", original)

    def no_rescan(*args):
        raise AssertionError("Native work repeated during report retry")

    monkeypatch.setattr(security_assessment, "observe_inventory", no_rescan)
    second = await recurring.invoke(service, request)
    assert second["publicationOutcome"] == "published"
    for key in ("scanRef", "comparisonRef", "controlAssessmentRef"):
        assert second[key] == first[key]


async def test_changed_period_and_revoked_evidence_do_not_reuse_a_green_control_result(
    environment, monkeypatch
):
    service = environment.service
    request = await inputs(service)
    evidence = await recurring.put(service, {"statement": "Fixture management claim"})
    requirement = request["controlAssessment"]["requirements"][0]
    requirement["supportingEvidence"] = [
        supporting(artifactRef=evidence, period=requirement["period"])
    ]
    first = await recurring.invoke(service, request)
    assert (await result_row(service, first))["conclusion"] == "satisfied"
    read = service.read

    async def denied(**kwargs):
        if kwargs["artifact_id"] == evidence:
            raise TemporalArtifactAuthorizationError("fixture revoked")
        return await read(**kwargs)

    monkeypatch.setattr(service, "read", denied)
    second = await recurring.invoke(service, request)
    assert (await result_row(service, second))["conclusion"] == "inconclusive"
    assert second["controlAssessmentRef"] != first["controlAssessmentRef"]
    assert second["scanRef"] == first["scanRef"]
    monkeypatch.setattr(service, "read", read)
    assert (await result_row(service, first))["conclusion"] == "satisfied"
    requirement["period"]["start"] = (
        datetime.now(UTC).date() - timedelta(days=30)
    ).isoformat()
    third = await recurring.invoke(service, request)
    assert (await result_row(service, third))["conclusion"] == "inconclusive"
    assert third["scanRef"] == first["scanRef"]


async def test_unavailable_agent_interpretation_keeps_native_and_control_evidence(
    environment,
):
    service = environment.service
    request = await inputs(service)
    request["reviewRef"] = "art_missing_interpretation"
    outputs = await recurring.invoke(service, request)
    assert outputs["reviewStatus"] == "unavailable"
    assert outputs["scanOutcome"] == "completed"
    row = await result_row(service, outputs)
    assert row["conclusion"] == "inconclusive"
    assert row["unavailableEvidenceRefs"] == [request["reviewRef"]]


async def test_aggregation_and_baseline_reuse_preserve_original_source_windows(
    environment,
):
    service = environment.service
    request = await inputs(service)
    now = datetime.now(UTC)

    def observation(tool, observed):
        return {
            "scope": {"assets": ["app"]},
            "observedAt": observed.isoformat(),
            "expiresAt": (now + timedelta(days=1)).isoformat(),
            "coverage": [
                {
                    "source": "fixture",
                    "tool": tool,
                    "assets": ["app"],
                    "ruleIds": ["defect"],
                    "status": "complete",
                    "evidenceMethod": "deterministic_check",
                    "configRef": "fixture:1",
                }
            ],
            "findings": [],
            "provenance": {"source": "fixture"},
        }

    old = await recurring.put(
        service, observation("old-check", now - timedelta(days=1))
    )
    new = await recurring.put(service, observation("new-check", now))
    request.update(
        tools=["retained-report"],
        reportRefs=[old, new],
        inventoryRef=None,
        feedRef=None,
    )
    request["controlAssessment"]["requirements"][0]["objectives"] = [
        {
            "id": "check",
            "description": "Selected old check",
            "source": "fixture",
            "tool": "old-check",
            "ruleIds": ["defect"],
        }
    ]
    first = await recurring.invoke(service, request)
    assert (await result_row(service, first))["conclusion"] == "inconclusive"
    source = await recurring.read(service, first["scanRef"])
    assert (
        source["observation"]["coverageWindows"][0]["observedAt"]
        != source["observation"]["observedAt"]
    )
    request["baselineRef"] = first["comparisonRef"]
    second = await recurring.invoke(service, request, run_id="next-run")
    assert (await result_row(service, second))["conclusion"] == "inconclusive"
    replayed = await recurring.read(service, second["scanRef"])
    assert replayed["originalOutcomes"] == ["retained_completed_work"]
    assert (
        replayed["observation"]["coverageWindows"]
        == source["observation"]["coverageWindows"]
    )


async def test_new_feed_does_not_refresh_old_inventory_population_date(environment):
    service = environment.service
    request = await inputs(service)
    inventory = await recurring.read(service, request["inventoryRef"])
    inventory["observedAt"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    request["inventoryRef"] = await recurring.put(service, inventory)
    outputs = await recurring.invoke(service, request)
    row = await result_row(service, outputs)
    assert row["conclusion"] == "inconclusive"
    scan = await recurring.read(service, outputs["scanRef"])
    assert datetime.fromisoformat(
        scan["observation"]["coverageWindows"][0]["observedAt"]
    ) == datetime.fromisoformat(inventory["observedAt"])


async def test_future_feed_cannot_supply_current_requirement_evidence(environment):
    service = environment.service
    request = await inputs(service)
    now = datetime.now(UTC)
    for key in ("inventoryRef", "feedRef"):
        payload = await recurring.read(service, request[key])
        payload["expiresAt"] = (now + timedelta(days=3)).isoformat()
        if key == "feedRef":
            payload["observedAt"] = (now + timedelta(days=1)).isoformat()
        request[key] = await recurring.put(service, payload)
    outputs = await recurring.invoke(service, request)
    assert outputs["scanOutcome"] == "incomplete"
    row = await result_row(service, outputs)
    assert row["conclusion"] == "inconclusive"
    assert row["evidenceSufficiency"] == "stale"
