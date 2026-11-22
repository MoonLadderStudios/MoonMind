"""Shared evidence review for built-in readiness packs, using the control owner."""

from moonmind.security.assessments import AssessmentScope, ControlRequirement
from moonmind.security.control_assessments import review_organisational_evidence


def review_requirement(
    *,
    requirement_id,
    title,
    pack_id,
    edition,
    systems,
    period,
    requests,
    required_purposes,
    now,
    evidence_availability
):
    requirement = ControlRequirement(
        id=requirement_id,
        title=title,
        framework_id=pack_id,
        framework_edition=edition,
        scope=AssessmentScope(assets=systems),
        period=period,
        applicability="applicable",
        applicability_reason="Included in the recorded ISMS scope.",
        objective_basis="Purpose-specific documentary review; technical checks are supporting context.",
        objectives=[],
        organisational_evidence=requests,
    )
    reviews = [
        review_organisational_evidence(r, requirement, evidence_availability, now)
        for r in requests
    ]
    accepted = {
        r.purpose for r, review in zip(requests, reviews) if review.state == "accepted"
    }
    missing = sorted(set(required_purposes) - accepted)
    return {
        "status": (
            "incomplete"
            if missing or any(r.state != "accepted" for r in reviews)
            else "evidence_reviewed"
        ),
        "missingPurposes": missing,
        "evidenceReviews": [r.model_dump(mode="json", by_alias=True) for r in reviews],
    }


async def prepare_builtin_framework(
    execution, request, *, module, system, collected_evidence_refs=()
):
    """Resolve current documentary evidence through the existing artifact owner.

    The module owns its typed inputs and conclusions; this boundary owns access,
    byte bounds, immutable caching and input dependencies for both readiness packs.
    """
    import json
    from datetime import UTC, datetime

    from moonmind.security.assessments import content_key
    from moonmind.security.frameworks import (
        FrameworkEvidence,
        FrameworkPack,
        load_framework_pack,
    )
    from moonmind.workflows.skills.tool_plan_contracts import ToolFailure
    from moonmind.workflows.temporal.artifacts import (
        ARTIFACT_ID_PATTERN,
        ArtifactDependency,
        TemporalArtifactAuthorizationError,
        TemporalArtifactNotFoundError,
        TemporalArtifactStateError,
        TemporalArtifactStorageAccessError,
        TemporalArtifactUnavailableError,
        TemporalArtifactValidationError,
    )

    selection = request.framework
    pack = load_framework_pack(selection.pack_id)
    if not set(system.systems) <= set(request.scope.assets):
        raise ValueError("Framework systems exceed admitted assessment scope")
    if selection.audit_ref:
        raise ValueError("Readiness packs accept scoped evidence, not an implied audit")
    refs = list(
        dict.fromkeys(
            revision.artifact_ref
            for requests in system.requirement_evidence.values()
            for item in requests
            for revision in item.revisions[-1:]
        )
    )
    if len(refs) > 64:
        raise ValueError("Framework documentary evidence exceeds reference bound")
    errors = (
        TemporalArtifactAuthorizationError,
        TemporalArtifactNotFoundError,
        TemporalArtifactStateError,
        TemporalArtifactStorageAccessError,
        TemporalArtifactUnavailableError,
        TemporalArtifactValidationError,
        ToolFailure,
        ValueError,
    )
    availability, readable, records = {}, [], []
    total = 0

    async def read(ref):
        nonlocal total
        _, body = await execution.read(ref)
        total += len(body)
        if total > 2 * 1024 * 1024:
            raise ValueError("Framework evidence exceeds byte bound")
        await execution.digest(ref)
        readable.append(ref)
        return body

    for ref in refs:
        try:
            await read(ref)
            availability[ref] = None
        except errors as exc:
            availability[ref] = type(exc).__name__
    invalid = []
    for ref in dict.fromkeys([*selection.evidence_refs, *collected_evidence_refs]):
        try:
            body = await read(ref)
            payload = json.loads(body)
            record = FrameworkEvidence.model_validate({**payload, "artifactRef": ref})
            if ARTIFACT_ID_PATTERN.fullmatch(record.provenance.source_ref):
                await read(record.provenance.source_ref)
            records.append(record.model_dump(mode="json", by_alias=True))
        except (*errors, TypeError):
            invalid.append(ref)
    previous = None
    if selection.previous_assessment_ref:
        previous = json.loads(await read(selection.previous_assessment_ref))
        previous_pack = FrameworkPack.model_validate(previous.get("pack"))
        if previous_pack.id != pack.id:
            raise ValueError(
                "Previous assessment must belong to the selected framework"
            )
    report = module.prepare_assessment(
        selection,
        evidence=records,
        audit=None,
        now=datetime.now(UTC),
        evidence_availability=availability,
        previous=previous,
    )
    for ref in invalid:
        report["evidenceRequests"].append(
            {
                "id": "evidence." + ref,
                "prompt": "Restore readable scoped evidence with provenance; uploaded documents alone cannot establish implementation.",
            }
        )
        report["readiness"] = "incomplete"
    key = content_key(
        {
            "request": selection.model_dump(mode="json", by_alias=True),
            "report": {k: v for k, v in report.items() if k != "evaluatedAt"},
            "evidence": {ref: execution.digests[ref] for ref in sorted(set(readable))},
        }
    )
    saved = await execution.saved(kind="security.framework", key=key)
    if saved:
        root, report = saved
    else:
        root = await execution.write(
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode(),
            kind="security.framework",
            key=key,
        )

    async def dependencies():
        return [
            ArtifactDependency(
                ref, await execution.digest(ref), True, "security.framework-input"
            )
            for ref in dict.fromkeys(readable)
        ]

    await execution.seal(root, dependencies)
    return root, report


async def prepare_scoped_framework(
    execution, request, *, system, collected_evidence_refs, prepare, evidence_type,
    audit_type=None, scoped_systems=lambda system: system.systems,
    system_refs=lambda system: (), evidence_refs_for=lambda evidence: (),
    audit_refs=lambda audit: (), evidence_guidance="",
):
    """Resolve evidence and retain the scoped report using the registered contracts."""
    import json
    from moonmind.security.assessments import content_key
    from moonmind.security.frameworks import load_framework_pack
    from moonmind.workflows.temporal.artifacts import ARTIFACT_ID_PATTERN, ArtifactDependency

    selection = request.framework
    pack = load_framework_pack(selection.pack_id)
    systems = set(scoped_systems(system))
    if not systems <= set(request.scope.assets):
        raise ValueError("Framework systems exceed admitted assessment scope")
    audit = None
    evidence_refs = list(
        dict.fromkeys([*selection.evidence_refs, *collected_evidence_refs])
    )
    refs = list(evidence_refs)
    previous = None
    if selection.previous_assessment_ref:
        _, body = await execution.read(selection.previous_assessment_ref)
        previous = json.loads(body)
        previous_pack = type(pack).model_validate(
            previous.get("pack") if isinstance(previous, dict) else None
        )
        if previous_pack.id != pack.id:
            raise ValueError(
                "Previous assessment must be a report for the selected framework"
            )
        refs.append(selection.previous_assessment_ref)
    refs.extend(ref for ref in system_refs(system) if ref and ARTIFACT_ID_PATTERN.fullmatch(ref))
    if selection.audit_ref:
        if audit_type is None:
            raise ValueError("GLI evidence belongs in evidenceRefs")
        _, body = await execution.read(selection.audit_ref)
        audit = audit_type.model_validate_json(body)
        refs.append(selection.audit_ref)
        refs.extend(audit_refs(audit))
    records, invalid = [], []
    for ref in evidence_refs:
        _, body = await execution.read(ref)
        try:
            # The artifact owner supplies this identity, not authored JSON.
            payload = json.loads(body)
            evidence = evidence_type.model_validate({**payload, "artifactRef": ref})
        except (ValueError, TypeError):
            invalid.append(ref)
            continue
        records.append(evidence.model_dump(mode="json", by_alias=True))
        refs.extend(evidence_refs_for(evidence))
        if ARTIFACT_ID_PATTERN.fullmatch(evidence.provenance.source_ref):
            refs.append(evidence.provenance.source_ref)
        if evidence.reliance:
            refs.append(evidence.reliance.review_ref)
    for ref in dict.fromkeys(refs):
        await execution.digest(ref)
    report = prepare(
        selection,
        evidence=records,
        audit=audit.model_dump(mode="json", by_alias=True) if audit else None,
    )
    if previous is not None:
        if previous.get("schemaVersion") != report["schemaVersion"]:
            raise ValueError(
                "Previous assessment must use the selected report schema"
            )
        report["reassessment"] = {
            "previousRef": selection.previous_assessment_ref,
            "previousEdition": previous["pack"]["edition"],
            "packChanged": previous["pack"] != report["pack"],
            "jurisdictionBasisChanged": previous.get("jurisdictionBasis")
            != report.get("jurisdictionBasis"),
        }
    for ref in invalid:
        prompt = "Supply a scoped evidence record with provenance, period, systems, control IDs and enquiry/evidence/observation method; an uploaded document alone cannot establish control coverage."
        prompt += evidence_guidance
        report["evidenceRequests"].append(
            {
                "id": "evidence." + ref,
                "artifactRef": ref,
                "prompt": prompt,
            }
        )
        report["readiness"] = "incomplete"
    key = content_key(
        {
            "request": selection.model_dump(mode="json", by_alias=True),
            "pack": pack.model_dump(mode="json", by_alias=True),
            "evidence": {ref: execution.digests[ref] for ref in sorted(set(refs))},
        }
    )
    saved = await execution.saved(kind="security.framework", key=key)
    if saved:
        root, report = saved
    else:
        root = await execution.write(json.dumps(report, sort_keys=True, separators=(",", ":")).encode(), kind="security.framework", key=key)

    async def dependencies():
        return [
            ArtifactDependency(
                ref, await execution.digest(ref), True, "security.framework-input"
            )
            for ref in dict.fromkeys(refs)
        ]

    await execution.seal(root, dependencies)
    return root, report
