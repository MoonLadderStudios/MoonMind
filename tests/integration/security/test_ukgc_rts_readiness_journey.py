"""Synthetic preset -> native assessment -> retained report journey (#5197)."""

import json
from unittest.mock import patch

import pytest

from api_service.services.presets.catalog import PresetCatalogService
from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_recurring_assessments import inputs, invoke, put
from tests.unit.api.test_presets_service import template_db
from tests.unit.api.test_ukgc_security_preset import seed
from tests.unit.security.test_ukgc_rts import fixture, request_payload

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment


async def test_synthetic_audit_through_catalog_and_native_artifact_journey(
    environment, tmp_path
):
    service = environment.service
    data = fixture()
    record_refs = [await put(service, row) for row in data["evidence"]]
    # Independence and prior-review statements are evidence, never inferred
    # from the auditor's display name or a certification field.
    independence = await put(service, {"statement": "Synthetic independence statement"})
    data["audit"]["auditor"]["independenceRef"] = independence
    audit_ref = await put(service, data["audit"])
    async with template_db(tmp_path) as maker, maker() as session:
        await seed(session, tmp_path)
        expanded = await PresetCatalogService(session).expand_template(
            slug="security-ukgc-rts",
            inputs={
                "assets": ["casino"],
                "system_inputs": data["framework"]["systemInputs"],
                "period": data["framework"]["period"],
                "evidence_refs": record_refs,
                "audit_ref": audit_ref,
            },
        )
    payload = expanded["steps"][0]["tool"]["inputs"]
    direct = request_payload()
    direct["framework"].update(evidenceRefs=record_refs, auditRef=audit_ref)
    first = await invoke(service, payload)
    second = await invoke(service, direct, key="direct")
    assert first["frameworkReadiness"] == "prepared"
    assert first["publicationOutcome"] == "published"
    access = access_for_test("workflow:assessment")
    _, body = await service.read(artifact_id=first["frameworkRef"], access=access)
    report = json.loads(body)
    _, direct_body = await service.read(
        artifact_id=second["frameworkRef"], access=access
    )
    assert json.loads(direct_body)["requirements"] == report["requirements"]
    assert (
        json.loads(direct_body)["evidenceRequests"] == report["evidenceRequests"] == []
    )
    assert len(report["requirements"]) == 44
    assert report["externalAudit"]["status"] == "Unverified"
    bundle = first["reportBundle"]
    _, text = await service.read(
        artifact_id=bundle["primary_report_ref"]["artifact_id"], access=access
    )
    assert b"Synthetic auditor" in text and b"Add test sample" in text
    assert b"customer_information" in text and b"Unverified" in text
    # Saved outputs are reused, but their inputs still require authorization.
    retried = await invoke(service, payload)
    assert retried["frameworkRef"] == first["frameworkRef"]
    proof = await service.verified_dependencies(
        root_artifact_id=first["frameworkRef"], access=access
    )
    assert set(record_refs + [audit_ref, independence]) <= {
        d.artifact_id for d in proof.dependencies
    }


async def test_empty_audit_exports_actionable_missing_external_evidence(environment):
    result = await invoke(environment.service, request_payload())
    assert result["frameworkReadiness"] == "incomplete"
    assert result["publicationOutcome"] == "published"
    _, raw = await environment.service.read(
        artifact_id=result["frameworkRef"],
        access=access_for_test("workflow:assessment"),
    )
    report = json.loads(raw)
    assert len(report["evidenceRequests"]) > 44
    assert any(r["id"] == "auditor.independence" for r in report["evidenceRequests"])
    assert {r["status"] for r in report["requirements"]} == {"incomplete"}


async def test_clean_scan_and_uploaded_certificate_leave_audit_gaps(environment):
    service = environment.service
    payload, _, _ = await inputs(service, rules=False)
    payload["framework"] = fixture()["framework"]
    payload["framework"]["systemInputs"]["criticalSystems"] = [
        dict(row, systems=["app"])
        for row in payload["framework"]["systemInputs"]["criticalSystems"]
    ]
    payload["framework"]["systemInputs"]["thirdParties"] = []
    certificate = await put(
        service, b"Synthetic ISO certificate without control or system coverage"
    )
    payload["framework"]["evidenceRefs"] = [certificate]
    result = await invoke(service, payload)
    assert result["scanOutcome"] == "completed"
    assert sum(result["counts"].values()) == 0
    assert result["frameworkReadiness"] == "incomplete"
    assert any(r.get("artifactRef") == certificate for r in result["evidenceRequests"])
    assert any(r.get("requirementId") == "8.29" for r in result["evidenceRequests"])


async def test_publication_retry_preserves_prepared_artifact(environment):
    service = environment.service
    payload = request_payload()
    with patch(
        "moonmind.workflows.skills.security_assessment._summary",
        side_effect=RuntimeError("synthetic publication outage"),
    ):
        first = await invoke(service, payload)
    assert first["publicationOutcome"] == "failed"
    assert first["frameworkRef"]
    retried = await invoke(service, payload)
    assert retried["publicationOutcome"] == "published"
    assert retried["frameworkRef"] == first["frameworkRef"]


async def test_framework_cannot_expand_admitted_scope(environment):
    from temporalio.exceptions import ApplicationError

    payload = request_payload()
    payload["scope"]["assets"] = ["different-system"]
    with pytest.raises(ApplicationError, match="Invalid framework"):
        await invoke(environment.service, payload)
