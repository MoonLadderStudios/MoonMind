"""Collected inventory survives source cleanup through the real retained-report owner.

The artifact store, authorization, sealing and security.assess Activity are real;
network execution and installed-image qualification belong to the Docker lane.
"""

import hashlib
import io
import json
import shutil
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from temporalio.exceptions import ApplicationError

from api_service.db.models import TemporalArtifactLink
from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring
from tests.unit.security.test_service_inventory import resolution
from tests.unit.security.test_service_inventory_integration import (
    collector,
    execution,
    plan,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment


async def collect(
    service, tmp_path, *, reachability="open", missing=False, restricted=False
):
    tmp_path.mkdir()
    payload = None if missing else execution(reachability=reachability)
    if payload:
        now = datetime.now(UTC)
        payload["startedAt"] = (now - timedelta(seconds=2)).isoformat()
        payload["finishedAt"] = (now - timedelta(seconds=1)).isoformat()
    evidence = resolution(plan())
    evidence["nativeNetwork"]["dialOutcomes"] = {reachability: 1} if not missing else {}
    evidence["nativeNetwork"]["attempts"] = 0 if missing else 1
    backend, request, _ = collector(tmp_path, payload, egress=evidence)
    refs = {}

    async def publish(request, name, data):
        if not restricted:
            refs[name] = await recurring.put(service, data)
        else:
            operator = access_for_test("operator")
            artifact, _ = await service.create(
                principal=operator.principal,
                content_type="application/json",
                redaction_level="restricted",
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal=operator.principal,
                payload=data,
            )
            await service.delegate_read_access(
                artifact_ids=[artifact.artifact_id],
                workflow_id="assessment",
                access=operator,
            )
            refs[name] = artifact.artifact_id
        return refs[name]

    backend._publish = publish
    manifest = await backend._collect_declared_outputs(request, [])
    summary_ref = next(
        row.artifact_ref for row in manifest if row.name == "service-inventory-summary"
    )
    summary = await recurring.read(service, summary_ref)
    shutil.rmtree(tmp_path / "service-inventory")
    return summary_ref, summary


def assessment(summary_ref, baseline=None):
    request = {
        "scope": {"assets": ["tcp://smtp.example.com:587"]},
        "tools": ["retained-report"],
        "reportRefs": [summary_ref],
        "credentialRefs": [],
        "networkRefs": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
    }
    if baseline:
        request["baselineRef"] = baseline
    return request


async def test_collected_inventory_survives_cleanup_seals_native_refs_and_compares(
    environment, tmp_path
):
    service = environment.service
    first_ref, first_summary = await collect(service, tmp_path / "open")
    first = await recurring.invoke(service, assessment(first_ref), key="inventory-open")
    comparison = await recurring.read(service, first["comparisonRef"])
    assert comparison["findings"][0]["state"] == "new"
    assert (
        comparison["findings"][0]["finding"]["ruleId"]
        == "informational-open-tcp-service"
    )
    required = await recurring.required(service, first["scanRef"])
    assert {first_summary["evidenceRef"], first_summary["attestationRef"]} <= required
    assert first["scannerSummaries"][0]["coverage"] == "complete"
    assert first["scannerSummaries"][0]["nativeRefs"] == {
        "execution": first_summary["evidenceRef"],
        "attestation": first_summary["attestationRef"],
    }
    second_ref, _ = await collect(service, tmp_path / "refused", reachability="refused")
    second = await recurring.invoke(
        service, assessment(second_ref, first["comparisonRef"]), key="inventory-refused"
    )
    compared = await recurring.read(service, second["comparisonRef"])
    assert compared["findings"][0]["state"] == "absent_comparable"
    _, report = await service.read(
        artifact_id=recurring.report_ref(second),
        access=recurring.access_for_test("workflow:assessment"),
    )
    assert b"not confirmed vulnerabilities" in report
    assert b"Per-endpoint outcomes are fixed-runner observations" in report


async def test_missing_inventory_is_explicit_and_preserves_previous_observation(
    environment, tmp_path
):
    service = environment.service
    first_ref, _ = await collect(service, tmp_path / "open")
    first = await recurring.invoke(service, assessment(first_ref), key="inventory-open")
    missing_ref, _ = await collect(service, tmp_path / "missing", missing=True)
    second = await recurring.invoke(
        service,
        assessment(missing_ref, first["comparisonRef"]),
        key="inventory-missing",
    )
    compared = await recurring.read(service, second["comparisonRef"])
    assert compared["findings"][0]["state"] == "not_reassessed"
    summary = second["scannerSummaries"][0]
    assert summary["missingEndpoints"] == ["smtp.example.com:587"]
    assert summary["reducedCoverage"] is True
    assert summary["verification"] == "unverified"
    assert "execution" not in summary["nativeRefs"]
    _, report = await service.read(
        artifact_id=recurring.report_ref(second),
        access=recurring.access_for_test("workflow:assessment"),
    )
    assert b"Untested endpoints: smtp.example.com:587" in report


@pytest.mark.parametrize("include_native", [False, True])
async def test_selected_inventory_summary_export_preserves_sources_and_current_grants(
    environment, tmp_path, include_native
):
    service = environment.service
    summary_ref, summary = await collect(
        service, tmp_path / "inventory", restricted=True
    )
    refs = [summary_ref, summary["evidenceRef"], summary["attestationRef"]]
    original = {
        ref: (await service.read(artifact_id=ref, access=access_for_test("operator")))[
            1
        ]
        for ref in refs
    }
    request = assessment(summary_ref)
    first = await recurring.invoke(service, request)
    request["evidenceExport"] = {
        "artifactRefs": refs if include_native else [summary_ref]
    }
    exported = await recurring.invoke(service, request)
    assert exported["scanRef"] == first["scanRef"]
    assert exported["scannerSummaries"][0]["coverage"] == "complete"
    result = exported["evidenceExport"]
    assert result["status"] == ("partial" if include_native else "exported"), result
    _, package = await service.read(
        artifact_id=result["packageRef"], access=access_for_test("workflow:assessment")
    )
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        included = [row for row in manifest["entries"] if row["status"] == "included"]
        assert len(included) == 1 and included[0]["sourceArtifactId"] == summary_ref
        entry = included[0]
        body = archive.read(entry["memberName"])
        assert entry["sourceSchema"] == "service-inventory/v1"
        assert entry["representation"] == "raw"
        assert (
            entry["sourceSha256"] == hashlib.sha256(original[summary_ref]).hexdigest()
        )
        assert entry["memberSha256"] == hashlib.sha256(body).hexdigest()
        assert json.loads(body)["endpoints"] == summary["endpoints"]
        assert manifest["transformationVersion"] == result["transformationVersion"]
        assert len(archive.namelist()) == 2
        if include_native:
            assert {
                row["sourceArtifactId"]: row["reason"]
                for row in manifest["entries"]
                if row["status"] == "withheld"
            } == {ref: "unsupported_schema" for ref in refs[1:]}
    repeated = await recurring.invoke(service, request)
    assert repeated["evidenceExport"]["packageRef"] == result["packageRef"]
    for ref, body in original.items():
        assert (
            await service.read(artifact_id=ref, access=access_for_test("operator"))
        )[1] == body

    # A saved scan/package and metadata access cannot replace a current raw grant.
    await environment.session.execute(
        delete(TemporalArtifactLink).where(
            TemporalArtifactLink.artifact_id == summary_ref,
            TemporalArtifactLink.workflow_id == "assessment",
            TemporalArtifactLink.link_type == "input.artifact.raw",
        )
    )
    await environment.session.commit()
    with pytest.raises(TemporalArtifactAuthorizationError):
        await service.read(
            artifact_id=summary_ref, access=access_for_test("workflow:assessment")
        )
    with pytest.raises(ApplicationError):
        await recurring.invoke(service, request)
