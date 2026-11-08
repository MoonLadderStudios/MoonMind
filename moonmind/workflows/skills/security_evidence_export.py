"""Selected assessment evidence packages on the existing report artifact owner."""

from __future__ import annotations

import hashlib

from moonmind.security.evidence_exports import (
    EVIDENCE_TRANSFORMATION_LIMITATIONS,
    EVIDENCE_TRANSFORMATION_VERSION,
    WithheldEvidence,
    build_evidence_archive,
    member_name,
    prepare_evidence_member,
)
from moonmind.workflows.temporal.artifacts import ArtifactDependency


def associated_evidence(*, request, work, outputs, evidence, summary_ref, review):
    """Direct assessment relationships, never all readable execution artifacts.

    In particular, a retention proof and a selected artifact's recursive
    citations are not a disclosure-selection mechanism.
    """
    refs = {ref: "" for ref in evidence}
    for ref in (
        *request.report_refs,
        request.inventory_ref,
        request.feed_ref,
        request.decisions_ref,
        request.review_ref,
    ):
        if ref:
            refs[ref] = ""
    if request.framework:
        refs.update({ref: "" for ref in request.framework.evidence_refs})
    if request.control_assessment:
        refs.update(
            {
                item.artifact_ref: ""
                for row in request.control_assessment.requirements
                for item in row.supporting_evidence
            }
        )
    if review:
        refs.update({ref: "" for ref in review.evidence_refs()})
    for summary in work.get("scannerSummaries", []):
        refs[summary["summaryRef"]] = ""
        for ref in summary["nativeRefs"].values():
            refs[ref] = "native:" + summary["scanner"]
    refs.update({ref: "" for ref in work.get("productEvidenceRefs", [])})
    for key in ("comparisonRef", "controlAssessmentRef", "frameworkRef"):
        if outputs.get(key):
            refs[outputs[key]] = ""
    refs[outputs["scanRef"]] = "scan"
    if summary_ref:
        refs[summary_ref] = "report"
    refs.update(
        {
            ref: "oscal:" + model
            for model, ref in outputs.get("oscalExport", {})
            .get("artifacts", {})
            .items()
        }
    )
    return refs


async def _write_retained(execution, payload, *, kind, content_type="application/json"):
    digest = hashlib.sha256(payload).hexdigest()
    for artifact in await execution.service.list_for_execution(
        **execution.execution, link_type=kind, access=execution.access
    ):
        if (artifact.metadata_json or {}).get("subject") != digest:
            continue
        observed, body = await execution.service.read(
            artifact_id=artifact.artifact_id, access=execution.access
        )
        if (
            observed.sha256 != digest
            or len(body) != len(payload)
            or hashlib.sha256(body).hexdigest() != digest
        ):
            raise WithheldEvidence("evidence_integrity_failed")
        return artifact.artifact_id
    return await execution.write(
        payload, kind=kind, key=digest, content_type=content_type
    )


async def export_selected_evidence(execution, *, selection, associated):
    result = {
        "status": "unavailable",
        "diagnostics": [],
        "transformationVersion": EVIDENCE_TRANSFORMATION_VERSION,
        "limitations": list(EVIDENCE_TRANSFORMATION_LIMITATIONS),
    }
    entries, members, dependencies = [], {}, {}
    source_bytes = emitted_bytes = 0
    for index, ref in enumerate(selection.artifact_refs):
        entry = {"sourceArtifactId": ref, "status": "withheld"}
        entries.append(entry)
        try:
            if ref not in associated:
                raise WithheldEvidence("unassociated_evidence")
            source, _, _, policy = await execution.service.get_metadata(
                artifact_id=ref, access=execution.access
            )
            await execution.service.validate_dependency_availability(
                source, access=execution.access
            )
            observed_ref = policy.default_read_ref
            if observed_ref is None or (
                not policy.raw_access_allowed and observed_ref.artifact_id == ref
            ):
                raise WithheldEvidence("inaccessible_evidence")
            size = observed_ref.size_bytes
            if (
                size is None
                or size > selection.max_member_bytes
                or source_bytes + size > selection.max_total_bytes
            ):
                raise WithheldEvidence("export_bound_exceeded")
            observed, body = await execution.service.read(
                artifact_id=observed_ref.artifact_id, access=execution.access
            )
            observed_digest = hashlib.sha256(body).hexdigest()
            if (
                observed.artifact_id != observed_ref.artifact_id
                or observed.sha256 != observed_digest
                or observed_ref.sha256 != observed_digest
                or observed.size_bytes != len(body)
                or size != len(body)
                or (observed.artifact_id == ref and source.sha256 != observed_digest)
            ):
                raise WithheldEvidence("evidence_integrity_failed")
            source_bytes += len(body)
            # A preview is an independently observed representation, never raw
            # evidence. Validate it against the source's declared supported
            # format; truncated/malformed JSON cannot become a successful export.
            prepared, extension, schema = prepare_evidence_member(
                body, content_type=source.content_type or "", hint=associated[ref]
            )
            emitted_bytes += len(prepared)
            if (
                len(prepared) > selection.max_member_bytes
                or emitted_bytes > selection.max_total_bytes
            ):
                raise WithheldEvidence("export_bound_exceeded")
            name = member_name(index, prepared, extension)
            entry.update(
                status="included",
                sourceSha256=source.sha256,
                sourceDigestOrigin="artifact_metadata",
                observedArtifactId=observed.artifact_id,
                observedSha256=observed_digest,
                observedBytes=len(body),
                representation="raw" if observed.artifact_id == ref else "preview",
                sourceSchema=schema,
                memberName=name,
                memberSha256=hashlib.sha256(prepared).hexdigest(),
                memberBytes=len(prepared),
            )
            members[name] = prepared
            dependencies[ref] = source.sha256
            dependencies[observed.artifact_id] = observed_digest
        except WithheldEvidence as exc:
            entry["reason"] = exc.code
            result["diagnostics"].append({"sourceArtifactId": ref, "code": exc.code})
        except Exception:  # noqa: BLE001 - optional disclosure must fail closed
            # Exception messages, filenames and payload fragments are untrusted.
            entry["reason"] = "evidence_unavailable"
            result["diagnostics"].append(
                {"sourceArtifactId": ref, "code": "evidence_unavailable"}
            )
    if not members:
        return result
    manifest = {
        "schemaVersion": "assessment-evidence-export/v1",
        "transformationVersion": EVIDENCE_TRANSFORMATION_VERSION,
        "limitations": [
            *EVIDENCE_TRANSFORMATION_LIMITATIONS,
            "Only explicitly selected associated evidence bodies are included. Retention dependencies are not disclosure selections.",
            "The source digest comes from artifact metadata; the observed digest hashes only the authorized returned representation. Preview bytes may be incomplete and are not original native evidence.",
        ],
        "entries": entries,
    }
    try:
        package, manifest_body = build_evidence_archive(
            manifest, members, limit=selection.max_total_bytes
        )
        manifest_ref = await _write_retained(
            execution,
            manifest_body,
            kind="security.evidence-export-manifest",
        )

        async def source_dependencies():
            return [
                ArtifactDependency(ref, digest, True, "security.selected-evidence")
                for ref, digest in dependencies.items()
            ]

        await execution.seal(manifest_ref, source_dependencies)
        # A reused seal is historical proof, not a new integrity observation.
        # Verify selected source/preview bytes through the retention owner even
        # when the current reader is authorized only for the preview.
        await execution.service.verified_dependencies(
            root_artifact_id=manifest_ref, access=execution.access
        )
        package_ref = await _write_retained(
            execution,
            package,
            kind="security.evidence-export",
            content_type="application/zip",
        )

        async def package_dependencies():
            return [
                ArtifactDependency(
                    manifest_ref,
                    hashlib.sha256(manifest_body).hexdigest(),
                    True,
                    "security.export-manifest",
                ),
                *await source_dependencies(),
            ]

        await execution.seal(package_ref, package_dependencies)
        for ref in (manifest_ref, package_ref):
            await execution.service.link_artifact(
                artifact_id=ref,
                principal=execution.principal,
                execution_ref={**execution.execution, "link_type": "report.export"},
            )
        result.update(
            status="partial" if result["diagnostics"] else "exported",
            packageRef=package_ref,
            manifestRef=manifest_ref,
            selectedCount=len(selection.artifact_refs),
            includedCount=len(members),
        )
    except WithheldEvidence as exc:
        result["diagnostics"].append({"code": exc.code})
    except Exception:  # noqa: BLE001 - preserve completed assessment work
        result["status"] = "failed"
        result["diagnostics"].append({"code": "export_publication_failed"})
    return result
