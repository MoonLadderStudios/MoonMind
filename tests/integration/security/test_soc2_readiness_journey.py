"""Synthetic service journeys through catalog, Activity, retention and export.

SOC 2 runs as an installed pack on the shared control evaluator; the saved
``framework.packId`` shape these journeys submit normalizes once at the
request boundary.
"""

import copy
from pathlib import Path

import pytest

from api_service.services.presets.catalog import PresetCatalogService
from api_service.services.single_user_conversion import run_guarded_upgrade
from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring
from tests.unit.api.test_presets_service import template_db
from tests.unit.security.test_assessment_evidence_exports import unpack
from tests.unit.security.test_soc2_readiness import (
    complete_requirement,
    review_sampling,
    selection,
)

environment = recurring.environment
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
ROOT = Path(__file__).resolve().parents[3]


async def retained(service, outputs):
    """The retained control report and its SOC 2 readiness context."""
    report = await recurring.read(service, outputs["controlAssessmentRef"])
    return report, report["frameworkPack"]["readiness"]


def criterion(report, identifier="CC6.1"):
    """One criterion's readiness status with the behavior's recorded basis."""
    row = next(
        r["requirement"]
        for r in report["requirements"]
        if r["requirement"]["id"] == identifier
    )
    details = report["frameworkPack"]["readiness"].get("requirementDetails", {})
    return {"status": row["readiness"]["status"], **details.get(identifier, {})}


async def seed(session, tmp_path):
    await run_guarded_upgrade(session, operator_authorized=True)
    source = ROOT / "api_service/data/presets/security-soc2-readiness.yaml"
    seeds = tmp_path / "soc2-seeds"
    seeds.mkdir(exist_ok=True)
    (seeds / source.name).write_text(source.read_text())
    await PresetCatalogService(session).import_seed_templates(seed_dir=seeds)


async def test_selectable_catalog_matches_direct_api_and_exports_matrix(
    environment, tmp_path
):
    values = {
        "assets": ["api"],
        "system_inputs": selection().system_inputs,
        "period": selection().period.model_dump(mode="json"),
    }
    async with template_db(tmp_path) as maker, maker() as session:
        await seed(session, tmp_path)
        catalog = PresetCatalogService(session)
        expanded = await catalog.expand_template(
            slug="security-soc2-readiness", inputs=values
        )
    payload = expanded["steps"][0]["tool"]["inputs"]
    first = await recurring.invoke(environment.service, payload)
    direct = copy.deepcopy(payload)
    direct["framework"] = selection().model_dump(mode="json", by_alias=True)
    second = await recurring.invoke(environment.service, direct, key="direct")
    assert first["controlAssessmentRef"] == second["controlAssessmentRef"]
    report, _ = await retained(environment.service, first)
    assert len(report["requirements"]) == 70
    assert first["frameworkPack"]["readiness"] == "incomplete"
    retried = await recurring.invoke(environment.service, payload)
    assert retried["controlAssessmentRef"] == first["controlAssessmentRef"]
    primary = recurring.report_ref(first)
    _, readable = await environment.service.read(
        artifact_id=primary, access=access_for_test("workflow:assessment")
    )
    assert b"SOC 2 readiness only" in readable
    assert b"CC6.1: **inconclusive** (partial); readiness incomplete" in readable
    assert b"Unverified" in readable
    payload["evidenceExport"] = {"artifactRefs": [primary]}
    result = await recurring.invoke(environment.service, payload, key="export")
    archive, _manifest = await unpack(environment.service, result)
    assert any(
        b"SOC 2 readiness only" in archive.read(name) for name in archive.namelist()
    )


async def test_period_reassessment_reuses_evidence_without_erasing_gaps(environment):
    request = complete_requirement()
    refs = []
    for item in request.system_inputs["requirementEvidence"]["CC6.1"]:
        ref = await recurring.put(environment.service, {"synthetic": item["purpose"]})
        item["revisions"][0]["artifactRef"] = ref
        refs.append(ref)
    review_sampling(request)
    payload = {
        "scope": {"assets": ["api"]},
        "tools": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "credentialRefs": [],
        "networkRefs": [],
        "framework": request.model_dump(mode="json", by_alias=True),
    }
    first = await recurring.invoke(environment.service, payload)
    original, _ = await retained(environment.service, first)
    assert criterion(original)["status"] == "evidence_recorded"
    assert criterion(original, "CC8.1")["status"] == "incomplete"
    proof = await environment.service.verified_dependencies(
        root_artifact_id=first["controlAssessmentRef"],
        access=access_for_test("workflow:assessment"),
    )
    assert set(refs) <= {d.artifact_id for d in proof.dependencies}
    changed_selection = copy.deepcopy(payload)
    changed_selection["framework"]["systemInputs"]["requirementSamples"]["CC6.1"][
        "selectedRecordIds"
    ] = ["never-supplied-record"]
    changed_selection["framework"]["previousAssessmentRef"] = first[
        "controlAssessmentRef"
    ]
    tampered = await recurring.invoke(
        environment.service, changed_selection, key="sample-change"
    )
    changed_report, _ = await retained(environment.service, tampered)
    assert criterion(changed_report)["status"] == "incomplete"
    primary = recurring.report_ref(tampered)
    changed_selection["evidenceExport"] = {"artifactRefs": [primary]}
    exported = await recurring.invoke(
        environment.service, changed_selection, key="sample-export"
    )
    archive, _manifest = await unpack(environment.service, exported)
    contents = b"\n".join(archive.read(name) for name in archive.namelist())
    assert b"exact sampling context" in contents
    assert b"never-supplied-record" in contents
    assert (
        await recurring.read(environment.service, first["controlAssessmentRef"])
        == original
    )
    payload["framework"]["period"]["start"] = "2025-01-01"
    payload["framework"]["previousAssessmentRef"] = first["controlAssessmentRef"]
    later = await recurring.invoke(environment.service, payload, key="period-change")
    changed, context = await retained(environment.service, later)
    assert criterion(changed)["status"] == "incomplete"
    assert context["reassessment"]["periodChanged"]
    assert (
        await recurring.read(environment.service, first["controlAssessmentRef"])
        == original
    )


async def test_fixed_technical_finding_leaves_organisational_gap(environment):
    payload, inventory, _feed = await recurring.inputs(environment.service)
    payload["framework"] = selection(systems=["app"]).model_dump(
        mode="json", by_alias=True
    )
    before = await recurring.invoke(environment.service, payload)
    baseline = await recurring.read(environment.service, before["comparisonRef"])
    assert baseline["findings"]
    inventory["components"][0]["version"] = "2.0"
    payload["inventoryRef"] = await recurring.put(environment.service, inventory)
    payload["baselineRef"] = before["comparisonRef"]
    retest_ref = await recurring.put(
        environment.service, {"retest": "passed", "synthetic": True}
    )
    payload["decisionsRef"] = await recurring.put(
        environment.service,
        [recurring.decision(baseline["findings"][0]["findingId"], [retest_ref])],
    )
    after = await recurring.invoke(environment.service, payload, key="fixed")
    comparison = await recurring.read(environment.service, after["comparisonRef"])
    assert comparison["findings"][0]["state"] == "remediated_verified"
    report, _ = await retained(environment.service, after)
    assert criterion(report)["status"] == "incomplete"
    assert after["frameworkPack"]["readiness"] == "incomplete"


async def test_supporting_document_export_is_current_only_and_access_checked(
    environment,
):
    from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
    from tests.unit.security.test_soc2_readiness import documentary_request

    service = environment.service
    artifact, _ = await service.create(
        principal="operator",
        content_type="text/plain",
        link={
            "namespace": "default",
            "workflow_id": "assessment",
            "run_id": "run",
            "link_type": "input.security",
        },
    )
    current = artifact.artifact_id
    await service.write_complete(
        artifact_id=current,
        principal="operator",
        payload=b"Synthetic approved policy record",
    )
    old = await recurring.put(service, {"superseded": True})
    documentary = documentary_request(ref=current)
    documentary["revisions"].insert(
        0, {**documentary["revisions"][0], "artifactRef": old}
    )
    selected = selection(requirementEvidence={"CC6.1": [documentary]})
    payload = {
        "scope": {"assets": ["api"]},
        "tools": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "credentialRefs": [],
        "networkRefs": [],
        "framework": selected.model_dump(mode="json", by_alias=True),
        "evidenceExport": {"artifactRefs": [current, old]},
    }
    result = await recurring.invoke(service, payload)
    archive, manifest = await unpack(service, result)
    assert any(
        b"Synthetic approved policy record" in archive.read(name)
        for name in archive.namelist()
    )
    entries = {row["sourceArtifactId"]: row for row in manifest["entries"]}
    assert entries[old]["status"] == "withheld"
    assert entries[old]["reason"] == "unassociated_evidence"
    proof = await service.verified_dependencies(
        root_artifact_id=result["controlAssessmentRef"],
        access=access_for_test("workflow:assessment"),
    )
    assert current in {d.artifact_id for d in proof.dependencies}
    assert old not in {d.artifact_id for d in proof.dependencies}
    report, _ = await retained(service, result)
    row = criterion(report)
    assert row["organisationalEvidence"][0]["supersededRefs"] == [old]
    with pytest.raises(TemporalArtifactAuthorizationError):
        await service.read(
            artifact_id=result["controlAssessmentRef"],
            access=access_for_test("workflow:unrelated"),
        )


async def test_retained_legacy_soc2_report_renders_exports_and_is_a_previous_assessment(
    environment,
):
    """A retained soc2-readiness/v1 report stays usable after #5436."""
    import json

    from moonmind.workflows.skills.security_assessment import _record

    service = environment.service
    legacy = json.loads(
        (ROOT / "tests/fixtures/security/legacy_readiness_reports.json").read_text()
    )["reports"]["soc2-readiness/v1"]
    previous = await recurring.put(service, legacy)
    await _record(service, previous, [], access_for_test("workflow:assessment"))
    payload = {
        "scope": {"assets": ["api"]},
        "tools": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "credentialRefs": [],
        "networkRefs": [],
        "framework": dict(
            selection().model_dump(mode="json", by_alias=True),
            previousAssessmentRef=previous,
        ),
        "evidenceExport": {"artifactRefs": [previous]},
    }
    result = await recurring.invoke(service, payload)
    report, context = await retained(service, result)
    pack = report["frameworkPack"]
    assert pack["previousAssessmentStatus"] == "available"
    # The retained run recorded CC6.1 evidence this run does not supply.
    assert any(
        "CC6.1 evidence_recorded -> incomplete" in row
        for row in report["intelligenceChanges"]
    )
    assert context["reassessment"]["previousRef"] == previous
    _, readable = await service.read(
        artifact_id=recurring.report_ref(result),
        access=access_for_test("workflow:assessment"),
    )
    assert b"Previous soc2-readiness/v1 report" in readable
    archive, manifest = await unpack(service, result)
    entries = {row["sourceArtifactId"]: row for row in manifest["entries"]}
    assert entries[previous]["status"] == "included"
    assert previous in await recurring.required(service, result["controlAssessmentRef"])
