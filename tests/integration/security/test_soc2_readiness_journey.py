"""Synthetic service journeys through catalog, Activity, retention and export."""

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
    criterion,
    selection,
)

environment = recurring.environment
pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
ROOT = Path(__file__).resolve().parents[3]


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
    report = await recurring.read(environment.service, first["frameworkRef"])
    assert report == await recurring.read(environment.service, second["frameworkRef"])
    assert len(report["requirements"]) == 70
    assert first["frameworkReadiness"] == "incomplete"
    retried = await recurring.invoke(environment.service, payload)
    assert retried["frameworkRef"] == first["frameworkRef"]
    primary = recurring.report_ref(first)
    _, readable = await environment.service.read(
        artifact_id=primary, access=access_for_test("workflow:assessment")
    )
    assert b"SOC 2 readiness" in readable
    assert b"CC6.1: unassessed" in readable
    assert b"Unverified" in readable
    payload["evidenceExport"] = {"artifactRefs": [primary]}
    result = await recurring.invoke(environment.service, payload, key="export")
    archive, _manifest = await unpack(environment.service, result)
    assert any(b"SOC 2 readiness" in archive.read(name) for name in archive.namelist())


async def test_period_reassessment_reuses_evidence_without_erasing_gaps(environment):
    request = complete_requirement()
    refs = []
    for item in request.system_inputs["requirementEvidence"]["CC6.1"]:
        ref = await recurring.put(environment.service, {"synthetic": item["purpose"]})
        item["revisions"][0]["artifactRef"] = ref
        refs.append(ref)
    payload = {
        "scope": {"assets": ["api"]},
        "tools": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "credentialRefs": [],
        "networkRefs": [],
        "framework": request.model_dump(mode="json", by_alias=True),
    }
    first = await recurring.invoke(environment.service, payload)
    original = await recurring.read(environment.service, first["frameworkRef"])
    assert criterion(original)["status"] == "evidence_recorded"
    assert criterion(original, "CC8.1")["status"] == "unassessed"
    proof = await environment.service.verified_dependencies(
        root_artifact_id=first["frameworkRef"],
        access=access_for_test("workflow:assessment"),
    )
    assert set(refs) <= {d.artifact_id for d in proof.dependencies}
    payload["framework"]["period"]["start"] = "2025-01-01"
    payload["framework"]["previousAssessmentRef"] = first["frameworkRef"]
    later = await recurring.invoke(environment.service, payload, key="period-change")
    changed = await recurring.read(environment.service, later["frameworkRef"])
    assert criterion(changed)["status"] == "unassessed"
    assert changed["reassessment"]["periodChanged"]
    assert await recurring.read(environment.service, first["frameworkRef"]) == original


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
    report = await recurring.read(environment.service, after["frameworkRef"])
    assert criterion(report)["status"] == "unassessed"
    assert after["frameworkReadiness"] == "incomplete"
