"""Declared pack intelligence through the admitted Skill and artifact owners."""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_control_assessment_journey import inputs
from tests.unit.security.test_framework_packs import pack_payload

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment


async def select_pack(service, request, pack=None):
    pack = pack or pack_payload()
    raw = json.dumps(pack).encode()
    ref = await recurring.put(service, raw)
    day = datetime.now(UTC).date().isoformat()
    request.pop("controlAssessment", None)
    request["frameworkPack"] = {
        "artifactRef": ref,
        "packId": pack["id"],
        "edition": pack["edition"],
        "revision": pack["revision"],
        "sha256": hashlib.sha256(raw).hexdigest(),
        "period": {"start": day, "end": day},
        "requirementChoices": {
            "control-1": {
                "applicability": "applicable",
                "applicabilityReason": "The synthetic requirement applies to the selected app.",
            }
        },
    }
    return request


async def test_declared_pack_produces_retained_requirement_report(environment):
    service = environment.service
    request = await select_pack(service, await inputs(service))
    result = await recurring.invoke(service, request)
    report = await recurring.read(service, result["controlAssessmentRef"])
    assert report["requirements"][0]["conclusion"] == "satisfied"
    assert report["frameworkPack"]["revision"] == "1"
    assert (
        result["frameworkPack"]["artifactRef"]
        == request["frameworkPack"]["artifactRef"]
    )
    assert "requirements" not in result["frameworkPack"]
    assert "mappings" not in result["frameworkPack"]
    assert report["frameworkPack"]["sha256"] == request["frameworkPack"]["sha256"]
    assert request["frameworkPack"]["artifactRef"] in await recurring.required(
        service, result["controlAssessmentRef"]
    )
    _, summary = await service.read(
        artifact_id=recurring.report_ref(result),
        access=recurring.access_for_test("workflow:assessment"),
    )
    assert b"Framework pack: synthetic / 2026 / 1" in summary


async def test_pack_change_is_intelligence_not_a_new_scan_or_remediation(
    environment, monkeypatch
):
    service = environment.service
    request = await select_pack(service, await inputs(service))
    first = await recurring.invoke(service, request)
    pack = pack_payload()
    pack["revision"] = "2"
    pack["requirements"][0]["summary"] = "Updated original assessment summary"
    request = await select_pack(service, request, pack)
    request["frameworkPack"]["previousAssessmentRef"] = first["controlAssessmentRef"]

    def no_rescan(*args):
        raise AssertionError("Framework intelligence change repeated native work")

    monkeypatch.setattr(recurring.security_assessment, "observe_inventory", no_rescan)
    second = await recurring.invoke(service, request)
    assert second["scanRef"] == first["scanRef"]
    assert second["controlAssessmentRef"] != first["controlAssessmentRef"]
    report = await recurring.read(service, second["controlAssessmentRef"])
    assert report["frameworkPack"]["revision"] == "2"
    assert (
        report["frameworkPack"]["previousAssessmentRef"]
        == first["controlAssessmentRef"]
    )
    assert report["frameworkPack"]["previousAssessmentStatus"] == "available"
    assert any("revision" in row for row in report["intelligenceChanges"])
    assert any("requirementsDigest" in row for row in report["intelligenceChanges"])
    assert all("not remediation" in row for row in report["intelligenceChanges"])
    historical = await recurring.read(service, first["controlAssessmentRef"])
    assert historical["frameworkPack"]["revision"] == "1"
    assert historical["intelligenceChanges"] == []


async def test_restricted_pack_text_is_omitted_from_portable_reports(environment):
    service = environment.service
    pack = pack_payload()
    pack["distribution"] = "restricted"
    row = pack["requirements"][0]
    for key in ("title", "summary", "evidencePrompt", "objectiveBasis"):
        row[key] = "licensed-secret-marker"
    row["objectives"][0]["description"] = "licensed-secret-marker"
    request = await select_pack(service, await inputs(service), pack)
    result = await recurring.invoke(service, request)
    report = await recurring.read(service, result["controlAssessmentRef"])
    assert "licensed-secret-marker" not in json.dumps(report)
    assert report["frameworkPack"]["textOmitted"]
    assert any("omitted" in row for row in report["diagnostics"])
    _, summary = await service.read(
        artifact_id=recurring.report_ref(result),
        access=recurring.access_for_test("workflow:assessment"),
    )
    assert b"licensed-secret-marker" not in summary


@pytest.mark.parametrize("change", ["digest", "edition", "draft", "missing", "foreign"])
async def test_invalid_or_inaccessible_pack_never_collects_native_work(
    environment, monkeypatch, change
):
    from moonmind.security.assessments import AssessmentRequest
    from moonmind.workflows.skills.security_assessment import AssessmentExecution
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactAuthorizationError,
        TemporalArtifactNotFoundError,
    )

    service = environment.service
    pack = pack_payload()
    if change == "draft":
        pack["publicationStatus"] = "draft"
    request = await select_pack(service, await inputs(service), pack)
    if change == "digest":
        request["frameworkPack"]["sha256"] = "0" * 64
    elif change == "edition":
        request["frameworkPack"]["edition"] = "2025"
    elif change == "missing":
        request["frameworkPack"]["artifactRef"] = "art_missing_pack"
    elif change == "foreign":
        request["frameworkPack"]["artifactRef"] = await recurring.put(
            service, pack, workflow_id="foreign"
        )

    def no_scan(*args):
        raise AssertionError("Unadmitted pack started a native scan")

    monkeypatch.setattr(recurring.security_assessment, "observe_inventory", no_scan)
    execution = AssessmentExecution(
        service,
        principal="operator",
        context={
            "namespace": "default",
            "workflow_id": "assessment",
            "run_id": "run",
        },
        access=recurring.access_for_test("workflow:assessment"),
    )
    with pytest.raises(
        (ToolFailure, TemporalArtifactAuthorizationError, TemporalArtifactNotFoundError)
    ):
        await execution.execute(AssessmentRequest.model_validate(request))


async def test_lost_history_is_reported_without_rewriting_current_evidence(environment):
    service = environment.service
    request = await select_pack(service, await inputs(service))
    request["frameworkPack"][
        "previousAssessmentRef"
    ] = "art_missing_previous_pack_report"
    result = await recurring.invoke(service, request)
    report = await recurring.read(service, result["controlAssessmentRef"])
    assert report["requirements"][0]["conclusion"] == "satisfied"
    assert report["intelligenceChanges"] == []
    assert report["frameworkPack"]["previousAssessmentStatus"] == "unavailable"
    assert any("comparison is Unverified" in row for row in report["diagnostics"])


@pytest.mark.parametrize("fault", ["missing_bytes", "expired"])
async def test_old_evidence_loss_does_not_block_current_pack_assessment(
    environment, fault
):
    from datetime import timedelta

    from moonmind.workflows.temporal.artifacts import LocalTemporalArtifactStore

    service = environment.service
    old_request = await select_pack(service, await inputs(service))
    first = await recurring.invoke(service, old_request)
    old_input, _ = await service.read(
        artifact_id=old_request["inventoryRef"],
        access=recurring.access_for_test("workflow:assessment"),
    )
    if fault == "missing_bytes":
        LocalTemporalArtifactStore(environment.storage_root).delete(
            old_input.storage_key
        )
    else:
        from api_service.db.models import TemporalArtifact

        row = await environment.session.get(
            TemporalArtifact, old_request["inventoryRef"]
        )
        row.expires_at = datetime.now(UTC) - timedelta(days=1)
        await environment.session.commit()
    request = await select_pack(service, await inputs(service))
    request["frameworkPack"]["previousAssessmentRef"] = first["controlAssessmentRef"]
    result = await recurring.invoke(service, request)
    assert result["controlAssessmentStatus"] == "completed", result
    assert result["publicationOutcome"] == "published"
    report = await recurring.read(service, result["controlAssessmentRef"])
    assert report["requirements"][0]["conclusion"] == "satisfied"
    assert report["frameworkPack"]["previousAssessmentStatus"] == "available"
    proof = await recurring.required(service, result["controlAssessmentRef"])
    assert first["controlAssessmentRef"] in proof
    assert old_request["inventoryRef"] not in proof
