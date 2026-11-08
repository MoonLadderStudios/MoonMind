"""Issue #5192: real Skill Activity, restricted artifact and retention journey."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

from moonmind.security.assessments import Finding
from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_recurring_assessments import (
    age_everything,
    inputs,
    invoke,
    put,
    read,
    report_ref,
    required,
    status,
)
from tests.unit.security.test_oscal_export import (
    MODELS,
    native_fixture,
    validate_document,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
artifact_environment = recurring.environment


@pytest_asyncio.fixture
async def environment(artifact_environment):
    # The operator reads raw exports as this finished execution's recorded
    # owner, not through a blanket instance-operator restricted-byte bypass.
    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalExecutionCanonicalRecord,
        TemporalWorkflowType,
    )

    artifact_environment.session.add(
        TemporalExecutionCanonicalRecord(
            namespace="default",
            workflow_id="assessment",
            run_id="run",
            workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            owner_id=access_for_test("operator").principal,
            state=MoonMindWorkflowState.COMPLETED,
            entry="run",
        )
    )
    await artifact_environment.session.commit()
    yield artifact_environment


async def export_request(service):
    request, inventory, feed = await inputs(service)
    inventory["scope"] = {"assets": ["casino"]}
    inventory["components"][0]["asset"] = "casino"
    request["scope"] = inventory["scope"]
    request["inventoryRef"] = await put(service, inventory)
    data = json.loads(
        (
            Path(__file__).resolve().parents[2] / "fixtures/security/ukgc_rts.json"
        ).read_text()
    )
    request["framework"] = data["framework"]
    request["framework"]["evidenceRefs"] = [
        await put(service, e) for e in data["evidence"]
    ]
    _, _, context, ssp = native_fixture()
    finding = Finding(
        source=feed["source"],
        tool="inventory-feed",
        rule_id=feed["rules"][0]["id"],
        asset="casino",
        native_id="pkg:pypi/example@1.0",
        evidence_refs=[request["inventoryRef"], request["feedRef"]],
    )
    context["findingTargets"] = {
        finding.finding_id: next(iter(context["findingTargets"].values()))
    }
    context["remediations"][0]["findingId"] = finding.finding_id
    context["remediations"][0]["evidenceRefs"] = [
        await put(service, {"action": "revoke", "status": "planned"})
    ]
    context["system"]["sspRef"] = await put(service, ssp)
    request["oscalExport"] = {
        "models": MODELS,
        "contextRef": await put(service, context),
    }
    return request, context


@pytest.mark.parametrize("multiline_objective", [False, True])
async def test_restricted_export_download_retry_and_retention(
    environment, multiline_objective
):
    service = environment.service
    request, context = await export_request(service)
    if multiline_objective:
        context["procedures"][0]["objective"] = (
            "Assess the recorded access review.\nConfirm the recorded revocations."
        )
        request["oscalExport"]["contextRef"] = await put(service, context)
    result = await invoke(service, request)
    assert result["oscalExport"]["status"] == "exported", result.get("oscalExport")
    exports = result["oscalExport"]["artifacts"]
    assert set(exports) == set(MODELS)
    for model, ref in exports.items():
        artifact, raw = await service.read(
            artifact_id=ref, access=access_for_test("operator")
        )
        assert artifact.metadata_json["sensitivity"] == "restricted"
        validate_document(model, json.loads(raw))
        with pytest.raises(TemporalArtifactAuthorizationError):
            await service.read(
                artifact_id=ref, access=access_for_test("workflow:unrelated")
            )
    plan = (await read(service, exports["assessment-plan"]))["assessment-plan"]
    objective = context["procedures"][0]["objective"]
    assert (
        plan["local-definitions"]["objectives-and-methods"][0]["parts"][0]["prose"]
        == objective
    )
    assert plan["local-definitions"]["activities"][0]["description"] == objective
    results = (await read(service, exports["assessment-results"]))["assessment-results"]
    assert results["import-ap"]["href"] == (
        "urn:moonmind:artifact:" + exports["assessment-plan"]
    )
    assert await read(service, result["comparisonRef"])
    _, native = await service.read(
        artifact_id=report_ref(result), access=access_for_test("operator")
    )
    assert b"finding" in native.lower()
    artifacts = await service.list_for_execution(
        namespace="default",
        workflow_id="assessment",
        run_id="run",
        link_type="report.export",
        access=access_for_test("operator"),
    )
    assert {a.artifact_id for a in artifacts} == set(exports.values())
    replay = await invoke(service, request)
    assert replay["scanRef"] == result["scanRef"]
    assert replay["oscalExport"]["artifacts"] == exports
    root = exports["poam"]
    dependencies = await required(service, root)
    expected = {
        request["inventoryRef"],
        request["feedRef"],
        request["oscalExport"]["contextRef"],
        context["system"]["sspRef"],
        *request["framework"]["evidenceRefs"],
        *context["remediations"][0]["evidenceRefs"],
    }
    assert expected <= dependencies
    print(
        "OSCAL_EXPORT_RECEIPT="
        + json.dumps(
            {
                "models": {
                    model: {
                        "artifactRef": ref,
                        "sha256": "sha256:"
                        + hashlib.sha256(
                            (
                                await service.read(
                                    artifact_id=ref, access=access_for_test("operator")
                                )
                            )[1]
                        ).hexdigest(),
                    }
                    for model, ref in exports.items()
                },
                "schemaValidation": {model: "passed" for model in MODELS},
                "dependencies": sorted(dependencies),
                "system": context["system"]["identifier"],
                "topology": "Skill Activity; SQLite repository; LocalTemporalArtifactStore",
                "sensitivity": "restricted",
            },
            sort_keys=True,
        )
    )
    await service.pin(
        artifact_id=root, principal="operator", reason="retained OSCAL export"
    )
    await age_everything(environment)
    await service.sweep_lifecycle(principal="service:sweeper")
    for ref in expected | {root}:
        assert await status(environment, ref) == "complete"
        assert await read(service, ref, principal="operator")
    await service.unpin(artifact_id=root, principal="operator")
    await age_everything(environment)
    await service.sweep_lifecycle(principal="service:sweeper")
    assert await status(environment, root) == "deleted"


@pytest.mark.parametrize(
    "fault",
    [
        "missing-context",
        "denied-evidence",
        "unknown-native-state",
        "wrong-subject",
        "authority",
        "missing-ssp",
        "malformed-ssp",
    ],
)
async def test_export_diagnostics_preserve_native_reports(environment, fault):
    service = environment.service
    request, context = await export_request(service)
    if fault == "missing-context":
        request["oscalExport"].pop("contextRef")
    elif fault == "denied-evidence":
        context["remediations"][0]["evidenceRefs"] = [
            await put(
                service, {"action": "private"}, workflow_id="unrelated", run_id="other"
            )
        ]
    elif fault == "unknown-native-state":
        context["remediations"][0]["status"] = "invented-status"
    elif fault == "wrong-subject":
        context["subjects"]["outside"] = context["subjects"]["casino"]
    elif fault == "authority":
        context["networkRefs"] = ["egress:outside"]
    elif fault == "missing-ssp":
        context["system"]["sspRef"] = "art_missing_ssp"
    else:
        context["system"]["sspRef"] = await put(
            service, {"system-security-plan": {"system-characteristics": []}}
        )
    if fault != "missing-context":
        request["oscalExport"]["contextRef"] = await put(service, context)
    result = await invoke(service, request)
    assert result["publicationOutcome"] == "published"
    assert result["oscalExport"]["status"] in {"unsupported", "partial"}
    assert result["oscalExport"]["diagnostics"]
    assert await read(service, result["comparisonRef"])
    _, native = await service.read(
        artifact_id=report_ref(result), access=access_for_test("operator")
    )
    assert b"Finding" in native or b"finding" in native


async def test_failed_export_publication_retry_reuses_native_work(environment):
    service = environment.service
    request, _ = await export_request(service)
    original = service.link_artifact

    async def fail_export(**kwargs):
        if kwargs["execution_ref"]["link_type"] == "report.export":
            raise RuntimeError("export publication interrupted")
        return await original(**kwargs)

    with patch.object(service, "link_artifact", side_effect=fail_export):
        first = await invoke(service, request)
    assert first["publicationOutcome"] == "published"
    assert first["oscalExport"]["status"] == "failed"
    assert await read(service, first["comparisonRef"])
    retry = await invoke(service, request)
    assert retry["scanRef"] == first["scanRef"]
    assert retry["comparisonRef"] == first["comparisonRef"]
    assert retry["oscalExport"]["status"] == "exported"
    assert len(
        await service.list_for_execution(
            namespace="default",
            workflow_id="assessment",
            run_id="run",
            link_type="security.oscal",
            access=access_for_test("operator"),
        )
    ) == len(MODELS)


async def test_omitted_export_keeps_native_journey(environment):
    request, _ = await export_request(environment.service)
    request.pop("oscalExport")
    result = await invoke(environment.service, request)
    assert "oscalExport" not in result
    assert result["publicationOutcome"] == "published"


async def test_updated_exchange_context_reuses_confirmed_native_work(environment):
    service = environment.service
    request, context = await export_request(service)
    first = await invoke(service, request)
    context["remediations"][0]["status"] = "investigating"
    request["oscalExport"]["contextRef"] = await put(service, context)
    revised = await invoke(service, request)
    assert revised["scanRef"] == first["scanRef"]
    assert revised["comparisonRef"] == first["comparisonRef"]
    assert revised["frameworkRef"] == first["frameworkRef"]
    assert revised["oscalExport"]["status"] == "exported"
    assert (
        revised["oscalExport"]["artifacts"]["poam"]
        != first["oscalExport"]["artifacts"]["poam"]
    )
