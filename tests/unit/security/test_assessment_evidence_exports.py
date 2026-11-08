"""Selected disclosure, presentation provenance and native-work preservation."""

import hashlib
import io
import json
import zipfile
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from moonmind.security.assessments import AssessmentRequest
from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_control_assessment_journey import (
    inputs as control_inputs,
)
from tests.integration.security.test_oscal_export_journey import export_request
from tests.unit.security.test_control_assessments import request_payload

SECRET = "synthetic-selected-export-value"
EMAIL = "synthetic-person@example.test"
environment = recurring.environment


def test_selected_export_contract_is_explicit_bounded_and_optional():
    payload = request_payload(evidenceExport={"artifactRefs": ["art_selected"]})
    request = AssessmentRequest.model_validate(payload)
    assert request.evidence_export.artifact_refs == ["art_selected"]
    assert AssessmentRequest.model_validate(request_payload()).evidence_export is None
    for selection in (
        {"artifactRefs": []},
        {"artifactRefs": ["art_same", "art_same"]},
        {"artifactRefs": [f"art_{index}" for index in range(33)]},
        {"artifactRefs": ["../../unsafe"]},
        {"artifactRefs": ["art_safe"], "maxTotalBytes": 32 * 1024 * 1024},
        {"artifactRefs": ["art_safe"], "includeDependencies": True},
    ):
        with pytest.raises(ValidationError):
            AssessmentRequest.model_validate(request_payload(evidenceExport=selection))


@pytest.mark.asyncio
async def test_actual_control_and_oscal_producers_redact_presentations_not_native(
    environment,
):
    service = environment.service
    request = await control_inputs(service)
    row = request["controlAssessment"]["requirements"][0]
    row["title"] = f"Reproduce src/check.py:42 token={SECRET} contact {EMAIL}"
    first = await recurring.invoke(service, request)
    assert first["publicationOutcome"] == "published"
    native = await recurring.read(service, first["controlAssessmentRef"])
    assert native["requirements"][0]["requirement"]["title"] == row["title"]
    _, raw = await service.read(
        artifact_id=recurring.report_ref(first),
        access=access_for_test("workflow:assessment"),
    )
    assert SECRET.encode() not in raw and EMAIL.encode() not in raw
    assert b"src/check.py:42" in raw
    assert b"evidence-redaction/v1" in raw
    request, context = await export_request(service)
    context["procedures"][0]["objective"] = (
        f"Inspect src/check.py:42 token={SECRET} contact {EMAIL}"
    )
    request["oscalExport"]["contextRef"] = await recurring.put(service, context)
    second = await recurring.invoke(service, request)
    assert second["oscalExport"]["status"] == "exported", second["oscalExport"]
    plan_ref = second["oscalExport"]["artifacts"]["assessment-plan"]
    _, raw = await service.read(
        artifact_id=plan_ref, access=access_for_test("workflow:assessment")
    )
    assert SECRET.encode() not in raw and EMAIL.encode() not in raw
    assert b"src/check.py:42" in raw and b"evidence-redaction/v1" in raw
    assert (
        await recurring.read(service, request["oscalExport"]["contextRef"])
    ) == context


async def unpack(service, result):
    export = result["evidenceExport"]
    _, body = await service.read(
        artifact_id=export["packageRef"], access=access_for_test("workflow:assessment")
    )
    archive = zipfile.ZipFile(io.BytesIO(body))
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest == await recurring.read(service, export["manifestRef"])
    return archive, manifest


@pytest.mark.asyncio
async def test_selected_sources_manifest_digests_native_preservation_and_retention(
    environment,
):
    service = environment.service
    request, inventory, _ = await recurring.inputs(service)
    inventory["configRef"] = f"src/check.py:42 token={SECRET} contact {EMAIL}"
    request["inventoryRef"] = await recurring.put(service, inventory)
    first = await recurring.invoke(service, request)
    request["evidenceExport"] = {
        "artifactRefs": [request["inventoryRef"], recurring.report_ref(first)]
    }
    result = await recurring.invoke(service, request)
    assert result["evidenceExport"]["status"] == "exported", result["evidenceExport"]
    assert result["scanRef"] == first["scanRef"]
    archive, manifest = await unpack(service, result)
    assert len(archive.namelist()) == 3
    assert {entry["sourceArtifactId"] for entry in manifest["entries"]} == set(
        request["evidenceExport"]["artifactRefs"]
    )
    for entry in manifest["entries"]:
        _, source = await service.read(
            artifact_id=entry["sourceArtifactId"],
            access=access_for_test("workflow:assessment"),
        )
        assert entry["sourceSha256"] == hashlib.sha256(source).hexdigest()
        assert entry["observedSha256"] == entry["sourceSha256"]
        assert entry["representation"] == "raw"
        emitted = archive.read(entry["memberName"])
        assert entry["memberSha256"] == hashlib.sha256(emitted).hexdigest()
        assert SECRET.encode() not in emitted and EMAIL.encode() not in emitted
        assert (
            not entry["memberName"].startswith("/") and ".." not in entry["memberName"]
        )
    exported_inventory = json.loads(archive.read(manifest["entries"][0]["memberName"]))
    assert exported_inventory["components"] == inventory["components"]
    assert (await recurring.read(service, request["inventoryRef"])) == inventory
    # The selected report's retention closure includes this feed, but its body
    # is not an export selection and must not become an archive member.
    assert request["feedRef"] not in {
        entry["sourceArtifactId"] for entry in manifest["entries"]
    }
    replay = await recurring.invoke(service, request)
    assert (
        replay["evidenceExport"]["packageRef"] == result["evidenceExport"]["packageRef"]
    )
    root = result["evidenceExport"]["packageRef"]
    await service.pin(
        artifact_id=root, principal="operator", reason="Retain selected export"
    )
    await recurring.age_everything(environment)
    await service.sweep_lifecycle(principal="service:sweeper")
    for ref in (
        root,
        result["evidenceExport"]["manifestRef"],
        *request["evidenceExport"]["artifactRefs"],
    ):
        assert await recurring.status(environment, ref) == "complete"


@pytest.mark.asyncio
async def test_unrelated_readable_engagement_artifact_is_not_disclosure_selection(
    environment,
):
    service = environment.service
    request, inventory, _ = await recurring.inputs(service)
    # Different immutable bytes, yet same workflow grants. Readability is not association.
    inventory["configRef"] = "unrelated-source"
    unrelated = await recurring.put(service, inventory)
    request["evidenceExport"] = {"artifactRefs": [unrelated]}
    result = await recurring.invoke(service, request)
    assert result["publicationOutcome"] == "published"
    assert result["evidenceExport"]["status"] == "unavailable"
    assert result["evidenceExport"]["diagnostics"][0]["code"] == "unassociated_evidence"
    assert "packageRef" not in result["evidenceExport"]


@pytest.mark.asyncio
async def test_optional_export_sanitizer_failure_keeps_completed_work(environment):
    from moonmind.security import evidence_exports

    service = environment.service
    request, _, _ = await recurring.inputs(service)
    request["evidenceExport"] = {"artifactRefs": [request["inventoryRef"]]}
    with patch.object(
        evidence_exports,
        "prepare_evidence_payload",
        side_effect=RuntimeError(f"token={SECRET} {EMAIL}"),
    ):
        result = await recurring.invoke(service, request)
    assert (
        result["scanOutcome"] == "completed"
        and result["publicationOutcome"] == "published"
    )
    assert result["evidenceExport"]["status"] == "unavailable"
    assert SECRET not in json.dumps(
        result["evidenceExport"]
    ) and EMAIL not in json.dumps(result["evidenceExport"])
    assert await recurring.read(service, result["comparisonRef"])


@pytest.mark.parametrize(
    "fault",
    ["revoked", "expired", "corrupt", "missing"],
    ids=["revoked", "expired", "corrupt", "missing"],
)
@pytest.mark.asyncio
async def test_export_rechecks_selected_optional_evidence_on_retry(
    environment, monkeypatch, fault
):
    from moonmind.workflows.temporal.artifacts import (
        TemporalArtifactAuthorizationError,
        TemporalArtifactNotFoundError,
    )
    from tests.unit.security.test_control_assessments import supporting

    service = environment.service
    request = await control_inputs(service)
    _, inventory, _ = await recurring.inputs(service)
    evidence_ref = await recurring.put(service, inventory)
    row = request["controlAssessment"]["requirements"][0]
    row["supportingEvidence"] = [
        supporting(artifactRef=evidence_ref, period=row["period"])
    ]
    request["evidenceExport"] = {"artifactRefs": [evidence_ref]}
    first = await recurring.invoke(service, request)
    assert first["evidenceExport"]["status"] == "exported"
    original_read = service.read

    async def unavailable(**kwargs):
        if kwargs["artifact_id"] == evidence_ref:
            if fault == "revoked":
                raise TemporalArtifactAuthorizationError(f"token={SECRET}")
            if fault == "missing":
                raise TemporalArtifactNotFoundError(evidence_ref)
            artifact, body = await original_read(**kwargs)
            return artifact, body.replace(b"example", b"changed")
        return await original_read(**kwargs)

    if fault == "expired":
        # Remove all live retention roots too; ordinary reads must not make
        # an expired record live merely by starting another consumer claim.
        await recurring.age_everything(environment)
        # New native work uses fresh required inputs, with the same optional ref.
        replacement = await control_inputs(service)
        replacement["controlAssessment"] = request["controlAssessment"]
        replacement["evidenceExport"] = request["evidenceExport"]
        request = replacement
    else:
        monkeypatch.setattr(service, "read", unavailable)
    result = await recurring.invoke(service, request)
    assert result["scanOutcome"] == "completed"
    assert result["publicationOutcome"] == "published"
    assert result["evidenceExport"]["status"] == "unavailable"
    assert "packageRef" not in result["evidenceExport"]
    assert SECRET not in json.dumps(result["evidenceExport"])
    if fault != "expired":
        assert result["scanRef"] == first["scanRef"]


@pytest.mark.asyncio
async def test_authorized_preview_is_distinct_observed_representation(environment):
    from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
    from tests.unit.security.test_control_assessments import supporting

    service = environment.service
    request = await control_inputs(service)
    _, inventory, _ = await recurring.inputs(service)
    inventory["configRef"] = f"src/check.py:42 token={SECRET} contact {EMAIL}"
    artifact, _ = await service.create(
        principal="source-owner",
        content_type="application/json",
        redaction_level="restricted",
        link={
            "namespace": "default",
            "workflow_id": "assessment",
            "run_id": "run",
            "link_type": "input.security",
        },
    )
    await service.write_complete(
        artifact_id=artifact.artifact_id,
        principal="source-owner",
        payload=json.dumps(inventory).encode(),
    )
    source, _, _, policy = await service.get_metadata(
        artifact_id=artifact.artifact_id, access=access_for_test("workflow:assessment")
    )
    assert not policy.raw_access_allowed
    preview_id = policy.default_read_ref.artifact_id
    # The lifecycle owner authorizes only the source-bound safe preview,
    # without a new restricted raw grant.
    with pytest.raises(TemporalArtifactAuthorizationError):
        await service.read(
            artifact_id=source.artifact_id,
            access=access_for_test("workflow:assessment"),
        )
    row = request["controlAssessment"]["requirements"][0]
    row["supportingEvidence"] = [
        supporting(artifactRef=source.artifact_id, period=row["period"])
    ]
    request["evidenceExport"] = {"artifactRefs": [source.artifact_id]}
    result = await recurring.invoke(service, request)
    assert result["publicationOutcome"] == "published"
    assert result["evidenceExport"]["status"] == "exported", result["evidenceExport"]
    archive, manifest = await unpack(service, result)
    entry = manifest["entries"][0]
    assert entry["representation"] == "preview"
    assert entry["sourceSha256"] == source.sha256
    assert entry["observedArtifactId"] == preview_id
    _, preview = await service.read(
        artifact_id=preview_id, access=access_for_test("workflow:assessment")
    )
    assert (
        entry["observedSha256"] == hashlib.sha256(preview).hexdigest() != source.sha256
    )
    emitted = archive.read(entry["memberName"])
    assert json.loads(emitted)["components"] == inventory["components"]
    assert SECRET.encode() not in emitted and EMAIL.encode() not in emitted
    proof = await recurring.required(service, result["evidenceExport"]["packageRef"])
    assert {source.artifact_id, preview_id} <= proof

    # The source is not observed raw, but its recorded immutable digest still
    # has to pass the retention owner's integrity check on every export retry.
    service._store.write_bytes(
        source.storage_key, b"corrupt synthetic source", content_type="application/json"
    )
    retry = await recurring.invoke(service, request)
    assert retry["scanRef"] == result["scanRef"]
    assert retry["evidenceExport"]["status"] == "failed"
    assert "packageRef" not in retry["evidenceExport"]


@pytest.mark.parametrize(
    "kind",
    ["malformed", "opaque", "unknown-schema", "member-bound", "total-bound"],
    ids=["malformed", "opaque", "unknown-schema", "member-bound", "total-bound"],
)
@pytest.mark.asyncio
async def test_unsupported_or_oversized_optional_evidence_is_withheld(
    environment, kind
):
    from tests.unit.security.test_control_assessments import supporting

    service = environment.service
    request = await control_inputs(service)
    _, inventory, _ = await recurring.inputs(service)
    if kind == "malformed":
        payload = b'{"schemaVersion":"security-inventory/v1", "broken":'
    elif kind == "opaque":
        payload = b"\xff\x00\x80opaque-synthetic-evidence"
    elif kind == "unknown-schema":
        payload = {"schemaVersion": "unrecognized/v1", "email": EMAIL}
    else:
        payload = inventory
    ref = await recurring.put(service, payload)
    row = request["controlAssessment"]["requirements"][0]
    row["supportingEvidence"] = [supporting(artifactRef=ref, period=row["period"])]
    request["evidenceExport"] = {"artifactRefs": [ref]}
    if kind == "member-bound":
        request["evidenceExport"]["maxMemberBytes"] = 1
    elif kind == "total-bound":
        request["evidenceExport"]["maxTotalBytes"] = 1024
    result = await recurring.invoke(service, request)
    assert (
        result["scanOutcome"] == "completed"
        and result["publicationOutcome"] == "published"
    )
    assert result["evidenceExport"]["status"] == "unavailable"
    assert "packageRef" not in result["evidenceExport"]
    assert EMAIL not in json.dumps(result["evidenceExport"])


@pytest.mark.asyncio
async def test_export_publication_retry_reuses_sealed_native_and_package_work(
    environment, monkeypatch
):
    service = environment.service
    request, _, _ = await recurring.inputs(service)
    request["evidenceExport"] = {"artifactRefs": [request["inventoryRef"]]}
    original = service.link_artifact

    async def interrupt(**kwargs):
        if kwargs["execution_ref"]["link_type"] == "report.export":
            raise ConnectionError("synthetic publication interruption")
        return await original(**kwargs)

    monkeypatch.setattr(service, "link_artifact", interrupt)
    first = await recurring.invoke(service, request)
    assert first["evidenceExport"]["status"] == "failed"
    assert first["publicationOutcome"] == "published"
    monkeypatch.setattr(service, "link_artifact", original)
    second = await recurring.invoke(service, request)
    assert second["scanRef"] == first["scanRef"]
    assert second["evidenceExport"]["status"] == "exported"
    packages = await service.list_for_execution(
        namespace="default",
        workflow_id="assessment",
        run_id="run",
        link_type="security.evidence-export",
        access=access_for_test("workflow:assessment"),
    )
    assert len(packages) == 1


@pytest.mark.asyncio
async def test_selected_sanitized_native_scanner_json_keeps_structure_and_identity(
    environment,
):
    from moonmind.security.repository_scanners import sanitize_native
    from tests.unit.security.test_scanner_assessment import summary

    service = environment.service
    native_input = {
        "results": [
            {
                "check_id": "native-rule",
                "path": "app.py",
                "start": {"line": 42, "col": 1},
                "end": {"line": 42, "col": 8},
                "extra": {"message": f"token={SECRET}"},
                # Existing scanner sanitation keeps version metadata but does not
                # classify email as credentials. The export is a separate copy.
                "version": EMAIL,
            }
        ],
        "errors": [],
        "paths": {"scanned": ["app.py"]},
    }
    native = sanitize_native("opengrep", json.dumps(native_input).encode())
    assert SECRET.encode() not in native and EMAIL.encode() in native
    native_ref = await recurring.put(service, native)
    retained = summary()
    retained.native_refs = {"scan": native_ref}
    for finding in retained.results["scan"].findings:
        finding.evidence_refs = [native_ref]
    summary_ref = await recurring.put(
        service, retained.model_dump(mode="json", by_alias=True)
    )
    request = request_payload(
        scope={"assets": ["app.py"]},
        reportRefs=[summary_ref],
        controlAssessment=None,
        evidenceExport={"artifactRefs": [native_ref]},
    )
    first = await recurring.invoke(service, request)
    assert first["evidenceExport"]["status"] == "exported", first["evidenceExport"]
    archive, manifest = await unpack(service, first)
    emitted = json.loads(archive.read(manifest["entries"][0]["memberName"]))
    assert emitted["results"][0]["path"] == "app.py"
    assert emitted["results"][0]["check_id"] == "native-rule"
    assert emitted["results"][0]["start"]["line"] == 42
    assert EMAIL not in json.dumps(emitted)
    _, still_native = await service.read(
        artifact_id=native_ref, access=access_for_test("workflow:assessment")
    )
    assert still_native == native
    before = await recurring.read(service, first["comparisonRef"])
    request.pop("evidenceExport")
    second = await recurring.invoke(service, request)
    after = await recurring.read(service, second["comparisonRef"])
    assert [row["findingId"] for row in before["findings"]] == [
        row["findingId"] for row in after["findings"]
    ]
    assert first["scanRef"] == second["scanRef"]


def test_oscal_projection_preserves_generated_ids_reference_graph_and_native_context():
    import copy

    from moonmind.security.oscal_export import OscalAdapter, OscalContext
    from moonmind.security.ukgc_rts import UKGC_RTS_PACK
    from tests.unit.security.test_oscal_export import (
        MODELS,
        native_fixture,
        validate_document,
    )

    comparison, report, context, ssp = native_fixture()
    context["procedures"][0]["objective"] = f"Inspect app.py:42 token={SECRET} {EMAIL}"
    original = copy.deepcopy((context, report, comparison.model_dump(mode="json")))
    adapter = OscalAdapter(
        comparison=comparison,
        pack=UKGC_RTS_PACK,
        framework_report=report,
        context=OscalContext.model_validate(context),
        ssp=ssp,
    )
    refs = {model: f"art_{model.replace('-', '_')}" for model in MODELS}
    documents = {model: adapter.build(model, refs) for model in MODELS}
    for model, document in documents.items():
        validate_document(model, document)
        assert SECRET not in json.dumps(document) and EMAIL not in json.dumps(document)
        root = document["plan-of-action-and-milestones" if model == "poam" else model]
        assert root["uuid"] == adapter.uid(model)
    assert (
        documents["assessment-results"]["assessment-results"]["import-ap"]["href"]
        == "urn:moonmind:artifact:" + refs["assessment-plan"]
    )
    assert (context, report, comparison.model_dump(mode="json")) == original


@pytest.mark.parametrize(
    "name",
    [
        "../outside.json",
        "/absolute.json",
        "evidence/../../../out",
        "evidence\\out.json",
    ],
    ids=["parent", "absolute", "nested-parent", "backslash"],
)
def test_archive_member_names_reject_unsafe_paths(name):
    from moonmind.security.evidence_exports import (
        WithheldEvidence,
        build_evidence_archive,
    )

    with pytest.raises(WithheldEvidence):
        build_evidence_archive({"entries": []}, {name: b"safe"}, limit=4096)


def test_falsely_named_passive_native_schema_is_withheld():
    from moonmind.security.evidence_exports import (
        WithheldEvidence,
        prepare_evidence_member,
    )

    with pytest.raises(WithheldEvidence):
        prepare_evidence_member(
            b'{"toolRef":"moonmind.security.passive_repo_scan.v1","opaque":{"name":"unrecognized"}}',
            content_type="application/json",
            hint="",
        )


@pytest.mark.parametrize(
    "boundary", ["admission", "framework"], ids=["admission", "framework"]
)
@pytest.mark.asyncio
async def test_actual_activity_invalid_input_failure_chain_excludes_source_values(
    environment, boundary
):
    from temporalio.api.failure.v1 import Failure
    from temporalio.converter import DefaultFailureConverter, DefaultPayloadConverter
    from temporalio.exceptions import ApplicationError

    from tests.integration.security.test_framework_pack_journey import select_pack
    from tests.unit.security.test_framework_packs import pack_payload

    request, _, _ = await recurring.inputs(environment.service)
    if boundary == "admission":
        request["evidenceExport"] = {"artifactRefs": [f"token={SECRET} {EMAIL}"]}
    else:
        payload = pack_payload()
        payload[f"token={SECRET} {EMAIL}"] = "unknown-field"
        request = await select_pack(environment.service, request, payload)
    with pytest.raises(ApplicationError) as raised:
        await recurring.invoke(environment.service, request)
    wire = Failure()
    DefaultFailureConverter().to_failure(raised.value, DefaultPayloadConverter(), wire)
    assert SECRET not in str(wire) and EMAIL not in str(wire)
    assert "INVALID_INPUT" in str(wire)
    assert (
        "string_pattern_mismatch" in str(wire)
        if boundary == "admission"
        else "extra_forbidden" in str(wire)
    )


@pytest.mark.asyncio
async def test_seeded_invalid_oscal_context_has_safe_diagnostics(environment):
    request, context = await export_request(environment.service)
    context["procedures"][0]["controlId"] = f"token={SECRET} {EMAIL}"
    request["oscalExport"]["contextRef"] = await recurring.put(
        environment.service, context
    )
    result = await recurring.invoke(environment.service, request)
    assert result["publicationOutcome"] == "published"
    assert result["oscalExport"]["diagnostics"]
    assert SECRET not in json.dumps(result["oscalExport"]) and EMAIL not in json.dumps(
        result["oscalExport"]
    )
