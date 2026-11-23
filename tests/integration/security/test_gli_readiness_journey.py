"""Synthetic GLI acceptance through real catalog, Activity and artifact owners.

GLI-19/GSF is installed declared-pack data on the shared control evaluator
(#5195, #5435). Saved requests still pass ``framework.packId``.
"""

import copy
import hashlib
import json
from unittest.mock import patch

import pytest
from temporalio.exceptions import ApplicationError

from api_service.services.presets.catalog import PresetCatalogService
from moonmind.security import framework_registry
from moonmind.security.framework_registry import installed_pack
from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_recurring_assessments import inputs, invoke, put
from tests.unit.api.test_gli_security_preset import seed
from tests.unit.api.test_presets_service import template_db
from tests.unit.security.test_gli_readiness import evaluate, fixture, request_payload

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment
ACCESS = access_for_test("workflow:assessment")
PACK_ID = "gli-19-readiness"


async def report(service, ref):
    return json.loads((await service.read(artifact_id=ref, access=ACCESS))[1])


def readiness(control):
    return control["frameworkPack"]["readiness"]


def statuses(control):
    return {
        row["requirement"]["id"]: row["requirement"]["readiness"]["status"]
        for row in control["requirements"]
    }


async def complete_synthetic_evidence(service):
    data = fixture()
    active = {
        key for key, row in evaluate().rows.items() if row["status"] == "incomplete"
    }
    pack = installed_pack(PACK_ID)[1]
    grouped = {}
    for requirement in pack.requirements:
        if requirement.id in active:
            grouped.setdefault(
                (requirement.module, requirement.evidence_types[0]), []
            ).append(requirement.id)
    refs = []
    for (module_id, category), requirement_ids in grouped.items():
        row = copy.deepcopy(data["technicalEvidence"])
        row.update(
            requirementIds=requirement_ids,
            evidenceType=category,
            kind=(
                "external_audit"
                if category in {"laboratory", "regulator"}
                else "control_evidence"
            ),
            jurisdiction=data["framework"]["systemInputs"]["jurisdictionBasis"][
                "jurisdiction"
            ],
        )
        module = next(m for m in pack.modules if m.id == module_id)
        row["provenance"]["standard"] = f"{module.id} v{module.edition}"
        refs.append(await put(service, row))
    return refs


async def test_catalog_and_direct_entrypoints_export_identical_scoped_results(
    environment, tmp_path
):
    service = environment.service
    data = fixture()
    refs = await complete_synthetic_evidence(service)
    async with template_db(tmp_path) as maker, maker() as session:
        await seed(session, tmp_path)
        expanded = await PresetCatalogService(session).expand_template(
            slug="security-gli-readiness",
            inputs={
                "assets": ["casino"],
                "system_inputs": data["framework"]["systemInputs"],
                "period": data["framework"]["period"],
                "evidence_refs": refs,
            },
        )
    payload = expanded["steps"][0]["tool"]["inputs"]
    direct = request_payload()
    direct["framework"]["evidenceRefs"] = refs
    first = await invoke(service, payload)
    second = await invoke(service, direct, key="direct")
    assert first["publicationOutcome"] == "published"
    assert first["frameworkPack"]["id"] == PACK_ID
    assert first["frameworkPack"]["readiness"] == "prepared"
    prepared = await report(service, first["controlAssessmentRef"])
    assert second["controlAssessmentRef"] == first["controlAssessmentRef"]
    context = readiness(prepared)
    assert context["evidenceRequests"] == []
    assert (
        context["certification"] == context["jurisdictionApproval"] == "not_performed"
    )
    assert all(
        row["status"] == "Unverified" for row in context["externalClaims"].values()
    )
    # Recorded readiness never becomes a satisfied requirement conclusion.
    assert "satisfied" not in {row["conclusion"] for row in prepared["requirements"]}
    _, exported = await service.read(
        artifact_id=first["reportBundle"]["primary_report_ref"]["artifact_id"],
        access=ACCESS,
    )
    for value in (
        "4.0",
        "2026-11-03",
        "jurisdictionBasis",
        "casino",
        "Unverified",
        refs[0],
        "unresolvedRequirements",
    ):
        assert value.encode() in exported
    proof = await service.verified_dependencies(
        root_artifact_id=first["controlAssessmentRef"], access=ACCESS
    )
    sealed = {d.artifact_id for d in proof.dependencies}
    assert set(refs) <= sealed
    assert first["frameworkPack"]["artifactRef"] in sealed
    _, pack_bytes = await service.read(
        artifact_id=first["frameworkPack"]["artifactRef"], access=ACCESS
    )
    assert hashlib.sha256(pack_bytes).hexdigest() == first["frameworkPack"]["sha256"]
    retried = await invoke(service, payload)
    assert retried["controlAssessmentRef"] == first["controlAssessmentRef"]


async def test_passing_vulnerability_scan_leaves_game_operational_and_lab_gaps(
    environment,
):
    service = environment.service
    payload, _, _ = await inputs(service, rules=False)
    payload["framework"] = fixture()["framework"]
    payload["framework"]["systemInputs"]["systems"] = ["app"]
    evidence = fixture()["technicalEvidence"]
    evidence["systems"] = ["app"]
    certificate = await put(service, b"Synthetic certificate without scoped metadata")
    payload["framework"]["evidenceRefs"] = [await put(service, evidence), certificate]
    result = await invoke(service, payload)
    assert result["scanOutcome"] == "completed"
    assert sum(result["counts"].values()) == 0
    assert result["frameworkPack"]["readiness"] == "incomplete"
    context = readiness(await report(service, result["controlAssessmentRef"]))
    missing_types = {
        kind for r in context["evidenceRequests"] for kind in r.get("evidenceTypes", [])
    }
    assert {
        "rng",
        "game_math",
        "organizational",
        "laboratory",
        "regulator",
    } <= missing_types
    prompt = next(
        r["prompt"]
        for r in context["evidenceRequests"]
        if r.get("artifactRef") == certificate
    )
    assert all(field in prompt for field in ("evidenceType", "outcome", "edition"))
    assert (
        context["certification"] == context["jurisdictionApproval"] == "not_performed"
    )


async def test_edition_and_adoption_reassessments_preserve_previous_reports(
    environment, monkeypatch
):
    service = environment.service
    payload = request_payload()
    first = await invoke(service, payload)
    _, original = await service.read(
        artifact_id=first["controlAssessmentRef"], access=ACCESS
    )
    _, original_export = await service.read(
        artifact_id=first["reportBundle"]["primary_report_ref"]["artifact_id"],
        access=ACCESS,
    )
    # Simulate a later installed publication, not a runtime compatibility pin.
    raw, pack = installed_pack(PACK_ID)
    data = json.loads(raw)
    data.update(edition="synthetic-next-edition", revision="2")
    next_raw = json.dumps(data).encode()
    next_pack = pack.model_validate(data)
    real = framework_registry.installed_pack

    def later(pack_id):
        return (next_raw, next_pack) if pack_id == PACK_ID else real(pack_id)

    monkeypatch.setattr(framework_registry, "installed_pack", later)
    payload["framework"]["previousAssessmentRef"] = first["controlAssessmentRef"]
    second = await invoke(service, payload, key="edition-change")
    assert second["controlAssessmentRef"] != first["controlAssessmentRef"]
    changed = await report(service, second["controlAssessmentRef"])
    assert changed["frameworkPack"]["edition"] == "synthetic-next-edition"
    assert (
        changed["frameworkPack"]["previousAssessmentRef"]
        == first["controlAssessmentRef"]
    )
    assert changed["frameworkPack"]["previousAssessmentStatus"] == "available"
    assert any("edition" in row for row in changed["intelligenceChanges"])
    # The recorded adoption names edition 4.0, so the new edition is not adopted.
    assert readiness(changed)["applicability"]["status"] == "incomplete"
    payload["framework"]["systemInputs"]["jurisdictionBasis"].update(
        edition="synthetic-next-edition", recordVersion="synthetic-2"
    )
    payload["framework"]["previousAssessmentRef"] = second["controlAssessmentRef"]
    third = await invoke(service, payload, key="adoption-change")
    refs = {
        first["controlAssessmentRef"],
        second["controlAssessmentRef"],
        third["controlAssessmentRef"],
    }
    assert len(refs) == 3
    adopted = await report(service, third["controlAssessmentRef"])
    assert readiness(adopted)["jurisdictionBasis"]["recordVersion"] == "synthetic-2"
    assert any(
        "profile inputs changed" in row for row in adopted["intelligenceChanges"]
    )
    proof = await service.verified_dependencies(
        root_artifact_id=third["controlAssessmentRef"], access=ACCESS
    )
    assert second["controlAssessmentRef"] in {d.artifact_id for d in proof.dependencies}
    assert (
        await service.read(artifact_id=first["controlAssessmentRef"], access=ACCESS)
    )[1] == original
    assert (
        await service.read(
            artifact_id=first["reportBundle"]["primary_report_ref"]["artifact_id"],
            access=ACCESS,
        )
    )[1] == original_export


async def test_publication_retry_preserves_saved_framework_result(environment):
    service = environment.service
    with patch(
        "moonmind.workflows.skills.security_assessment._summary",
        side_effect=RuntimeError("Synthetic export outage"),
    ):
        first = await invoke(service, request_payload())
    assert first["publicationOutcome"] == "failed"
    retried = await invoke(service, request_payload())
    assert retried["publicationOutcome"] == "published"
    assert retried["controlAssessmentRef"] == first["controlAssessmentRef"]


async def test_artifact_authority_and_admitted_scope_are_preserved(environment):
    service = environment.service
    payload = request_payload()
    payload["framework"]["evidenceRefs"] = [
        await put(service, fixture()["technicalEvidence"], workflow_id="other")
    ]
    with pytest.raises(ApplicationError):
        await invoke(service, payload)
    payload = request_payload()
    payload["scope"]["assets"] = ["outside"]
    with pytest.raises(ApplicationError, match="Invalid framework"):
        await invoke(service, payload)


async def test_provenance_and_adoption_artifacts_are_sealed_with_record(environment):
    service = environment.service
    payload = request_payload()
    source_ref = await put(
        service, {"observation": "Synthetic independent technical result"}
    )
    adoption_ref = await put(service, {"adoption": "Synthetic jurisdiction fixture"})
    row = fixture()["technicalEvidence"]
    row["provenance"]["sourceRef"] = source_ref
    # Authored artifactRef is deliberately false; the real artifact owner wins.
    row["artifactRef"] = "authored-fake-ref"
    record_ref = await put(service, row)
    payload["framework"]["evidenceRefs"] = [record_ref]
    payload["framework"]["systemInputs"]["jurisdictionBasis"][
        "sourceRef"
    ] = adoption_ref
    result = await invoke(service, payload)
    control = await report(service, result["controlAssessmentRef"])
    assert readiness(control)["evidence"][0]["artifactRef"] == record_ref
    assert statuses(control)["GLI-GSF-2:B"] == "evidence_recorded"
    proof = await service.verified_dependencies(
        root_artifact_id=result["controlAssessmentRef"], access=ACCESS
    )
    assert {source_ref, adoption_ref, record_ref} <= {
        d.artifact_id for d in proof.dependencies
    }


@pytest.mark.parametrize(
    "previous", [{"pack": None}, {"pack": {"id": "gli-19-readiness"}}]
)
async def test_invalid_previous_report_leaves_comparison_unverified(
    environment, previous
):
    # A broken history record no longer blocks the current evidence: the
    # comparison is reported Unverified, as for every declared pack.
    payload = request_payload()
    payload["framework"]["previousAssessmentRef"] = await put(
        environment.service, previous
    )
    result = await invoke(environment.service, payload)
    control = await report(environment.service, result["controlAssessmentRef"])
    assert control["frameworkPack"]["previousAssessmentStatus"] == "unavailable"
    assert any("comparison is Unverified" in row for row in control["diagnostics"])


async def test_retained_gli_readiness_report_serves_as_previous_assessment(
    environment,
):
    from moonmind.workflows.skills.security_assessment import _record
    from tests.unit.security.test_legacy_framework_reports import RETAINED

    service = environment.service
    retained = RETAINED["gli-readiness/v1"]
    previous = await put(service, retained)
    await _record(service, previous, [], ACCESS)
    _, original = await service.read(artifact_id=previous, access=ACCESS)
    payload = request_payload()
    payload["framework"]["previousAssessmentRef"] = previous
    result = await invoke(service, payload)
    control = await report(service, result["controlAssessmentRef"])
    pack = control["frameworkPack"]
    assert pack["previousAssessmentStatus"] == "available"
    changes = control["intelligenceChanges"]
    assert any("gli-readiness/v1" in row and "Unverified" in row for row in changes)
    # The retained run had passing technical evidence for GLI-GSF-2:B.
    assert any("GLI-GSF-2:B evidence_recorded -> incomplete" in row for row in changes)
    _, exported = await service.read(
        artifact_id=result["reportBundle"]["primary_report_ref"]["artifact_id"],
        access=ACCESS,
    )
    assert b"Previous gli-readiness/v1 report" in exported
    assert previous in await recurring.required(service, result["controlAssessmentRef"])
    assert (await service.read(artifact_id=previous, access=ACCESS))[1] == original


async def test_retained_report_exports_beside_the_reassessment(environment):
    from moonmind.workflows.skills.security_assessment import _record
    from tests.unit.security.test_assessment_evidence_exports import unpack
    from tests.unit.security.test_legacy_framework_reports import RETAINED

    service = environment.service
    previous = await put(service, RETAINED["gli-readiness/v1"])
    await _record(service, previous, [], ACCESS)
    payload = request_payload()
    payload["framework"]["previousAssessmentRef"] = previous
    payload["evidenceExport"] = {"artifactRefs": [previous]}
    result = await invoke(service, payload)
    archive, manifest = await unpack(service, result)
    entry = next(
        row for row in manifest["entries"] if row["sourceArtifactId"] == previous
    )
    assert entry["status"] == "included"
    exported = json.loads(archive.read(entry["memberName"]))
    assert exported["schemaVersion"] == "gli-readiness/v1"
    assert [row["id"] for row in exported["requirements"]] == [
        row["id"] for row in RETAINED["gli-readiness/v1"]["requirements"]
    ]
