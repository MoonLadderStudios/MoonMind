"""Synthetic New Jersey preset -> assessment -> retained export journey (#5196)."""

import copy
import json
from unittest.mock import patch

import pytest

from api_service.services.presets.catalog import PresetCatalogService
from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_recurring_assessments import inputs, invoke, put
from tests.unit.api.test_nj_dge_security_preset import seed
from tests.unit.api.test_presets_service import template_db
from tests.unit.security.test_nj_dge import fixture, request_payload

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment


async def materialize(service):
    data = fixture()
    refs = {}

    async def replace(value):
        if isinstance(value, dict):
            return {k: await replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [await replace(v) for v in value]
        if isinstance(value, str) and value.startswith("art_synthetic_"):
            if value not in refs:
                refs[value] = await put(service, {"synthetic": value})
            return refs[value]
        return value

    data = await replace(data)
    records = [await put(service, row) for row in data["evidence"]]
    data["framework"].update(
        evidenceRefs=records, auditRef=await put(service, data["audit"])
    )
    return data, list(refs.values()) + records + [data["framework"]["auditRef"]]


async def read(service, ref):
    return (
        await service.read(
            artifact_id=ref, access=access_for_test("workflow:assessment")
        )
    )[1]


@pytest.mark.parametrize(
    "profile, expected",
    [
        ("casino_licensee", {"nj.internet.annual", "nj.network.annual"}),
        ("rgs_content_provider", {"nj.rgs.annual"}),
        ("unrelated_supplier", set()),
    ],
)
async def test_entity_applicability_through_native_export(
    environment, profile, expected
):
    data = fixture()
    data["framework"]["systemInputs"] = data["profiles"][profile]
    result = await invoke(environment.service, request_payload(data))
    report = json.loads(await read(environment.service, result["frameworkRef"]))
    assert set(report["reportingConditions"]["applicableAnnualRules"]) == expected
    assert report["reportingConditions"]["annualReportToDivision"] == bool(expected)
    assert result["publicationOutcome"] == "published"


async def test_synthetic_export_preset_and_direct_share_scoped_requirements(
    environment, tmp_path
):
    service = environment.service
    data, expected_refs = await materialize(service)
    async with template_db(tmp_path) as maker, maker() as session:
        await seed(session, tmp_path)
        expanded = await PresetCatalogService(session).expand_template(
            slug="security-nj-dge",
            inputs={
                "assets": ["rgs"],
                "system_inputs": data["framework"]["systemInputs"],
                "period": data["framework"]["period"],
                "evidence_refs": data["framework"]["evidenceRefs"],
                "audit_ref": data["framework"]["auditRef"],
            },
        )
    payload = expanded["steps"][0]["tool"]["inputs"]
    first = await invoke(service, payload)
    second = await invoke(service, request_payload(data), key="direct")
    assert first["frameworkReadiness"] == "prepared"
    assert first["publicationOutcome"] == "published"
    report = json.loads(await read(service, first["frameworkRef"]))
    direct = json.loads(await read(service, second["frameworkRef"]))
    assert direct["requirements"] == report["requirements"]
    assert direct["evidenceRequests"] == report["evidenceRequests"] == []
    assert report["externalAssurance"]["status"] == "Unverified"
    assert report["reportingConditions"]["submissionPerformed"] is False
    bundle = first["reportBundle"]
    assert bundle["structured_ref"]["artifact_id"] == first["frameworkRef"]
    assert bundle["sensitivity"] == "restricted"
    text = await read(service, bundle["primary_report_ref"]["artifact_id"])
    for needle in (
        b"Synthetic assessor",
        b"licenseeResponse",
        b"13:69O-3.1",
        b"Unverified",
        b"2024-07-01",
        b"applicabilityBasis",
    ):
        assert needle in text
    proof = await service.verified_dependencies(
        root_artifact_id=first["frameworkRef"],
        access=access_for_test("workflow:assessment"),
    )
    assert set(expected_refs) <= {d.artifact_id for d in proof.dependencies}
    retry = await invoke(service, payload)
    assert retry["frameworkRef"] == first["frameworkRef"]


async def test_incomplete_annual_and_partner_evidence_survives_clean_scan(environment):
    service = environment.service
    payload, _, _ = await inputs(service, rules=False)
    data = fixture()
    data["framework"]["systemInputs"]["systems"] = ["app"]
    payload["framework"] = data["framework"]
    first = await invoke(service, dict(request_payload(data)), key="no-scan")
    scanned = await invoke(service, payload, key="clean-scan")
    assert scanned["scanOutcome"] == "completed"
    assert sum(scanned["counts"].values()) == 0
    assert scanned["evidenceRequests"] == first["evidenceRequests"]
    assert scanned["frameworkReadiness"] == "incomplete"
    ids = {r["id"] for r in scanned["evidenceRequests"]}
    assert "annual.nj.rgs.annual.divisionApprovalRef" in ids
    assert "annual.nj.rgs.annual.independence" in ids
    assert any(
        r["requirementId"] == "nj.partner.player-accounts"
        for r in scanned["evidenceRequests"]
    )


async def test_missing_approval_incident_operator_evidence_exported_with_sources(
    environment,
):
    service = environment.service
    data = fixture()
    # A clean technical assessment and authored approval claims do not substitute
    # for required evidence artifacts. Even the artifactRef is supplied by storage.
    scan = dict(
        data["evidence"][0],
        kind="technical_assessment",
        requirementIds=[r["requirementIds"][0] for r in data["evidence"]],
        artifactRef="art_forged",
    )
    for key in (
        "divisionApprovalRef",
        "operatorEvidenceRef",
        "incidentDisposition",
        "notificationRef",
    ):
        scan.pop(key, None)
    certificate = await put(service, b"unstructured synthetic GLI certificate")
    data["framework"]["evidenceRefs"] = [await put(service, scan), certificate]
    result = await invoke(service, request_payload(data))
    report = json.loads(await read(service, result["frameworkRef"]))
    rows = {r["id"]: r for r in report["requirements"]}
    for id in ("nj.rgs.games-releases", "nj.rgs.incidents", "nj.rgs.integration"):
        assert rows[id]["status"] == "incomplete"
        assert rows[id]["source"] and rows[id]["reason"]
        assert any(r["requirementId"] == id for r in report["evidenceRequests"])
    assert any(r.get("artifactRef") == certificate for r in report["evidenceRequests"])
    assert report["externalAssurance"]["status"] == "Unverified"
    assert all(r["status"] != "DGE-approved" for r in rows.values())


async def test_source_and_scope_snapshot_retained_after_updates_and_publication_retry(
    environment,
):
    from moonmind.security import nj_dge

    service = environment.service
    data, _ = await materialize(service)
    payload = request_payload(data)
    with patch(
        "moonmind.workflows.skills.security_assessment._summary",
        side_effect=RuntimeError("synthetic export outage"),
    ):
        first = await invoke(service, payload)
    assert first["publicationOutcome"] == "failed"
    original = await read(service, first["frameworkRef"])
    retry = await invoke(service, payload)
    assert retry["publicationOutcome"] == "published"
    assert retry["frameworkRef"] == first["frameworkRef"]
    changed_pack = nj_dge.NJ_DGE_PACK.model_copy(deep=True)
    changed_pack.rule_sources[0].edition = "Synthetic newer source snapshot"
    changed = copy.deepcopy(payload)
    newer_evidence = dict(
        data["evidence"][2], description="Synthetic newer incident evidence snapshot"
    )
    changed["framework"]["evidenceRefs"][2] = await put(service, newer_evidence)
    changed["framework"]["requirementChoices"] = {
        "nj.rgs.incidents": {"period": {"start": "2026-03-01", "end": "2026-06-30"}}
    }
    with patch.object(nj_dge, "NJ_DGE_PACK", changed_pack):
        source_only = await invoke(service, payload, key="source-only")
        new = await invoke(service, changed, key="new-snapshot")
    assert source_only["frameworkRef"] != first["frameworkRef"]
    assert source_only["frameworkRef"] != new["frameworkRef"]
    source_report = json.loads(await read(service, source_only["frameworkRef"]))
    assert (
        source_report["ruleSources"][0]["edition"] == "Synthetic newer source snapshot"
    )
    assert new["frameworkRef"] != first["frameworkRef"]
    assert (await read(service, first["frameworkRef"])) == original
    prior = json.loads(original)
    newer = json.loads(await read(service, new["frameworkRef"]))
    assert prior["ruleSources"][0]["edition"] != newer["ruleSources"][0]["edition"]
    assert prior["requirements"] != newer["requirements"]
    assert prior["evidence"] != newer["evidence"]


async def test_export_rejects_systems_outside_admitted_scope(environment):
    from temporalio.exceptions import ApplicationError

    payload = request_payload()
    payload["scope"]["assets"] = ["different-system"]
    with pytest.raises(ApplicationError, match="Invalid framework"):
        await invoke(environment.service, payload)


async def test_export_cannot_read_foreign_assessor_approval_evidence(environment):
    from temporalio.exceptions import ApplicationError

    service = environment.service
    data, _ = await materialize(service)
    private = await put(
        service, {"private": "other-workflow approval"}, workflow_id="other"
    )
    data["audit"]["assessments"][0]["divisionApprovalRef"] = private
    data["framework"]["auditRef"] = await put(service, data["audit"])
    with pytest.raises(ApplicationError):
        await invoke(service, request_payload(data))
