"""Synthetic GLI acceptance through real catalog, Activity and artifact owners (#5195)."""

import copy
import json
from unittest.mock import patch

import pytest
from temporalio.exceptions import ApplicationError

from api_service.services.presets.catalog import PresetCatalogService
from moonmind.security.frameworks import load_framework_pack
from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_recurring_assessments import inputs, invoke, put
from tests.unit.api.test_gli_security_preset import seed
from tests.unit.api.test_presets_service import template_db
from tests.unit.security.test_gli_readiness import evaluate, fixture, request_payload

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment
ACCESS = access_for_test("workflow:assessment")


async def report(service, ref):
    return json.loads((await service.read(artifact_id=ref, access=ACCESS))[1])


async def complete_synthetic_evidence(service):
    data = fixture()
    active = {
        row["id"] for row in evaluate()["requirements"] if row["status"] == "incomplete"
    }
    grouped = {}
    for requirement in load_framework_pack("gli-19-readiness").requirements:
        if requirement.id not in active:
            continue
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
        module = next(
            m
            for m in load_framework_pack("gli-19-readiness").modules
            if m.id == module_id
        )
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
    assert first["frameworkReadiness"] == "prepared"
    prepared = await report(service, first["frameworkRef"])
    assert await report(service, second["frameworkRef"]) == prepared
    assert prepared["evidenceRequests"] == []
    assert (
        prepared["certification"] == prepared["jurisdictionApproval"] == "not_performed"
    )
    assert all(
        row["status"] == "Unverified"
        for row in prepared["externalVerification"].values()
    )
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
        root_artifact_id=first["frameworkRef"], access=ACCESS
    )
    assert set(refs) <= {d.artifact_id for d in proof.dependencies}
    retried = await invoke(service, payload)
    assert retried["frameworkRef"] == first["frameworkRef"]


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
    assert result["frameworkReadiness"] == "incomplete"
    prepared = await report(service, result["frameworkRef"])
    missing_types = {
        kind
        for r in prepared["evidenceRequests"]
        for kind in r.get("evidenceTypes", [])
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
        for r in prepared["evidenceRequests"]
        if r.get("artifactRef") == certificate
    )
    assert all(field in prompt for field in ("evidenceType", "outcome", "edition"))
    assert (
        prepared["certification"] == prepared["jurisdictionApproval"] == "not_performed"
    )


async def test_edition_and_adoption_reassessments_preserve_previous_reports(
    environment, monkeypatch
):
    from moonmind.security import gli_readiness

    service = environment.service
    payload = request_payload()
    first = await invoke(service, payload)
    _, original = await service.read(artifact_id=first["frameworkRef"], access=ACCESS)
    _, original_export = await service.read(
        artifact_id=first["reportBundle"]["primary_report_ref"]["artifact_id"],
        access=ACCESS,
    )
    # Simulate a later pack publication, not a runtime compatibility constraint.
    next_pack = gli_readiness.GLI_PACK.model_copy(deep=True)
    next_pack.edition = "synthetic-next-edition"
    monkeypatch.setattr(gli_readiness, "GLI_PACK", next_pack)
    payload["framework"]["previousAssessmentRef"] = first["frameworkRef"]
    second = await invoke(service, payload, key="edition-change")
    assert second["frameworkRef"] != first["frameworkRef"]
    changed = await report(service, second["frameworkRef"])
    assert changed["pack"]["edition"] == "synthetic-next-edition"
    assert changed["reassessment"]["previousRef"] == first["frameworkRef"]
    assert changed["reassessment"]["previousEdition"] == "4.0"
    assert changed["applicability"]["status"] == "incomplete"
    payload["framework"]["systemInputs"]["jurisdictionBasis"].update(
        edition="synthetic-next-edition", recordVersion="synthetic-2"
    )
    payload["framework"]["previousAssessmentRef"] = second["frameworkRef"]
    third = await invoke(service, payload, key="adoption-change")
    assert (
        len({first["frameworkRef"], second["frameworkRef"], third["frameworkRef"]}) == 3
    )
    assert (await report(service, third["frameworkRef"]))["jurisdictionBasis"][
        "recordVersion"
    ] == "synthetic-2"
    assert (await report(service, third["frameworkRef"]))["reassessment"][
        "jurisdictionBasisChanged"
    ] is True
    proof = await service.verified_dependencies(
        root_artifact_id=third["frameworkRef"], access=ACCESS
    )
    assert second["frameworkRef"] in {d.artifact_id for d in proof.dependencies}
    assert (await service.read(artifact_id=first["frameworkRef"], access=ACCESS))[
        1
    ] == original
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
    assert retried["frameworkRef"] == first["frameworkRef"]


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
    prepared = await report(service, result["frameworkRef"])
    assert prepared["evidence"][0]["artifactRef"] == record_ref
    proof = await service.verified_dependencies(
        root_artifact_id=result["frameworkRef"], access=ACCESS
    )
    assert {source_ref, adoption_ref, record_ref} <= {
        d.artifact_id for d in proof.dependencies
    }


@pytest.mark.parametrize(
    "previous", [{"pack": None}, {"pack": {"id": "gli-19-readiness"}}]
)
async def test_invalid_previous_report_is_an_input_error(environment, previous):
    payload = request_payload()
    payload["framework"]["previousAssessmentRef"] = await put(
        environment.service, previous
    )
    with pytest.raises(ApplicationError, match="Invalid framework"):
        await invoke(environment.service, payload)
