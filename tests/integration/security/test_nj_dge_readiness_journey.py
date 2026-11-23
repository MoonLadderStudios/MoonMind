"""Synthetic New Jersey preset -> assessment -> retained export journey.

New Jersey DGE is installed declared-pack data on the shared control evaluator
(#5196, #5435). Saved requests still pass ``framework.packId``.
"""

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


async def control(service, result):
    return json.loads(await read(service, result["controlAssessmentRef"]))


def rows(report):
    return {
        row["requirement"]["id"]: {
            **row["requirement"]["readiness"],
            "reason": row["requirement"]["applicabilityReason"],
            "conclusion": row["conclusion"],
        }
        for row in report["requirements"]
    }


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
    readiness = (await control(environment.service, result))["frameworkPack"][
        "readiness"
    ]
    assert set(readiness["reportingConditions"]["applicableAnnualRules"]) == expected
    assert readiness["reportingConditions"]["annualReportToDivision"] == bool(expected)
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
    assert first["frameworkPack"]["readiness"] == "prepared"
    assert first["publicationOutcome"] == "published"
    report = await control(service, first)
    direct = await control(service, second)
    assert direct["requirements"] == report["requirements"]
    readiness = report["frameworkPack"]["readiness"]
    assert direct["frameworkPack"]["readiness"]["evidenceRequests"] == []
    assert readiness["evidenceRequests"] == []
    assert all(
        c["status"] == "Unverified" for c in readiness["externalClaims"].values()
    )
    assert readiness["reportingConditions"]["submissionPerformed"] is False
    bundle = first["reportBundle"]
    assert bundle["structured_ref"]["artifact_id"] == first["controlAssessmentRef"]
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
        root_artifact_id=first["controlAssessmentRef"],
        access=access_for_test("workflow:assessment"),
    )
    assert set(expected_refs) <= {d.artifact_id for d in proof.dependencies}
    retry = await invoke(service, payload)
    assert retry["controlAssessmentRef"] == first["controlAssessmentRef"]


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
    requests = (await control(service, scanned))["frameworkPack"]["readiness"][
        "evidenceRequests"
    ]
    assert (
        requests
        == (await control(service, first))["frameworkPack"]["readiness"][
            "evidenceRequests"
        ]
    )
    assert scanned["frameworkPack"]["readiness"] == "incomplete"
    ids = {r["id"] for r in requests}
    assert "annual.nj.rgs.annual.divisionApprovalRef" in ids
    assert "annual.nj.rgs.annual.independence" in ids
    assert any(r.get("requirementId") == "nj.partner.player-accounts" for r in requests)


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
    report = await control(service, result)
    readiness = report["frameworkPack"]["readiness"]
    by_id = rows(report)
    for id in ("nj.rgs.games-releases", "nj.rgs.incidents", "nj.rgs.integration"):
        assert by_id[id]["status"] == "incomplete"
        assert by_id[id]["reason"]
        assert any(
            r.get("requirementId") == id and r["source"]
            for r in readiness["evidenceRequests"]
        )
    assert any(
        r.get("artifactRef") == certificate for r in readiness["evidenceRequests"]
    )
    assert readiness["externalClaims"]["regulator"]["status"] == "Unverified"
    assert all(row["conclusion"] != "satisfied" for row in by_id.values())


async def test_source_and_scope_snapshot_retained_after_updates_and_publication_retry(
    environment, monkeypatch
):
    from moonmind.security import framework_registry

    service = environment.service
    data, _ = await materialize(service)
    payload = request_payload(data)
    with patch(
        "moonmind.workflows.skills.security_assessment._summary",
        side_effect=RuntimeError("synthetic export outage"),
    ):
        first = await invoke(service, payload)
    assert first["publicationOutcome"] == "failed"
    original = await read(service, first["controlAssessmentRef"])
    retry = await invoke(service, payload)
    assert retry["publicationOutcome"] == "published"
    assert retry["controlAssessmentRef"] == first["controlAssessmentRef"]
    raw, pack = framework_registry.installed_pack("nj-dge-readiness")
    newer = json.loads(raw)
    newer["revision"] = "2"
    newer["modules"][0]["edition"] = "Synthetic newer source snapshot"
    real = framework_registry.installed_pack
    monkeypatch.setattr(
        framework_registry,
        "installed_pack",
        lambda pack_id: (
            (json.dumps(newer).encode(), pack.model_validate(newer))
            if pack_id == "nj-dge-readiness"
            else real(pack_id)
        ),
    )
    changed = copy.deepcopy(payload)
    newer_evidence = dict(
        data["evidence"][2], description="Synthetic newer incident evidence snapshot"
    )
    changed["framework"]["evidenceRefs"][2] = await put(service, newer_evidence)
    changed["framework"]["requirementChoices"] = {
        "nj.rgs.incidents": {"period": {"start": "2026-03-01", "end": "2026-06-30"}}
    }
    source_only = await invoke(service, payload, key="source-only")
    new = await invoke(service, changed, key="new-snapshot")
    assert source_only["controlAssessmentRef"] != first["controlAssessmentRef"]
    assert source_only["controlAssessmentRef"] != new["controlAssessmentRef"]
    source_report = await control(service, source_only)
    modules = source_report["frameworkPack"]["readiness"]["modules"]
    assert modules[0]["edition"] == "Synthetic newer source snapshot"
    assert new["controlAssessmentRef"] != first["controlAssessmentRef"]
    assert (await read(service, first["controlAssessmentRef"])) == original
    prior = json.loads(original)
    newest = await control(service, new)
    assert (
        prior["frameworkPack"]["readiness"]["modules"][0]["edition"]
        != newest["frameworkPack"]["readiness"]["modules"][0]["edition"]
    )
    assert prior["requirements"] != newest["requirements"]
    assert (
        prior["frameworkPack"]["readiness"]["evidence"]
        != newest["frameworkPack"]["readiness"]["evidence"]
    )


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


async def test_comparison_accepts_retained_nj_rule_source_metadata(environment):
    service = environment.service
    data, _ = await materialize(service)
    payload = request_payload(data)
    original = await invoke(service, payload)
    payload["framework"]["previousAssessmentRef"] = original["controlAssessmentRef"]
    current = await invoke(service, payload, key="comparison")
    report = await control(service, current)
    assert (
        report["frameworkPack"]["previousAssessmentRef"]
        == original["controlAssessmentRef"]
    )
    assert report["frameworkPack"]["previousAssessmentStatus"] == "available"
    assert report["intelligenceChanges"] == []


async def test_retained_nj_report_serves_as_previous_assessment(environment):
    from moonmind.workflows.skills.security_assessment import _record
    from tests.unit.security.test_assessment_evidence_exports import unpack
    from tests.unit.security.test_legacy_framework_reports import RETAINED

    service = environment.service
    access = access_for_test("workflow:assessment")
    previous = await put(service, RETAINED["framework-audit-preparation/v1"])
    await _record(service, previous, [], access)
    data, _ = await materialize(service)
    payload = request_payload(data)
    payload["framework"]["previousAssessmentRef"] = previous
    payload["evidenceExport"] = {"artifactRefs": [previous]}
    result = await invoke(service, payload)
    report = await control(service, result)
    assert report["frameworkPack"]["previousAssessmentStatus"] == "available"
    archive, manifest = await unpack(service, result)
    entry = next(r for r in manifest["entries"] if r["sourceArtifactId"] == previous)
    assert entry["status"] == "included"
    exported = json.loads(archive.read(entry["memberName"]))
    assert exported["schemaVersion"] == "framework-audit-preparation/v1"
    changes = report["intelligenceChanges"]
    assert any("framework-audit-preparation/v1" in row for row in changes)
    assert any("edition changed" in row for row in changes)
    # Same synthetic evidence, so the matching requirement IDs did not change.
    assert not any("Requirement readiness changed" in row for row in changes)
    text = await read(
        service, result["reportBundle"]["primary_report_ref"]["artifact_id"]
    )
    assert b"Previous framework-audit-preparation/v1 report" in text
