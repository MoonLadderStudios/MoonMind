"""Deterministic draft and acceptance steps for packs from an operator's policy.

Both run outside any model step. Drafting checks a ``security-policy-pack``
Skill response against the cited documents; acceptance applies the operator's
review, publishes a new revision and registers it in one saved environment.
Earlier revisions and the assessments that used them are never rewritten.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated

from pydantic import Field, ValidationError, model_validator

from api_service.services.settings_catalog import SettingsCatalogService
from moonmind.security.assessment_projects import SETTINGS_KEY
from moonmind.security.framework_packs import (
    MAX_PACK_BYTES,
    ArtifactRef,
    Sha256,
    VersionedFrameworkPack,
    load_declared_pack,
    verify_source_documents,
)
from moonmind.security.frameworks import (
    FrameworkContract,
    FrameworkPackIdentity,
    PackIdentifier,
)
from moonmind.security.policy_packs import (
    MAX_DOCUMENTS,
    DocumentRecord,
    DraftResponse,
    MappingSelection,
    PackReference,
    PackReview,
    PolicyIdentity,
    PolicyPackDraft,
    UnusableDocument,
    accept_pack,
    build_draft,
    read_policy_document,
    render_review_sheet,
)
from moonmind.workflows.temporal.artifacts import (
    ArtifactDependency,
    TemporalArtifactError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class PolicyPackError(ValueError):
    """A fixed diagnostic that never echoes document or response text."""


class DocumentSelection(FrameworkContract):
    artifact_ref: ArtifactRef
    sha256: Sha256


class PolicyPackDraftRequest(FrameworkContract):
    policy: PolicyIdentity
    documents: Annotated[
        list[DocumentSelection], Field(min_length=1, max_length=MAX_DOCUMENTS)
    ]
    response_artifact_ref: ArtifactRef
    response_sha256: Sha256
    previous_pack: FrameworkPackIdentity | None = None
    mapping_target: MappingSelection | None = None
    rule_inventory_refs: Annotated[list[ArtifactRef], Field(max_length=16)] = Field(
        default_factory=list
    )
    revision: PackIdentifier | None = None

    @model_validator(mode="after")
    def distinct_documents(self):
        refs = [item.artifact_ref for item in self.documents]
        if len(refs) != len(set(refs)):
            raise ValueError("duplicate policy document")
        return self


class PolicyPackAcceptRequest(FrameworkContract):
    draft_artifact_ref: ArtifactRef | None = None
    draft_sha256: Sha256 | None = None
    pack: FrameworkPackIdentity | None = None
    review: PackReview
    project_id: PackIdentifier
    environment_id: PackIdentifier

    @model_validator(mode="after")
    def one_base(self):
        draft = self.draft_artifact_ref is not None and self.draft_sha256 is not None
        partial = (self.draft_artifact_ref is None) != (self.draft_sha256 is None)
        if partial or draft == (self.pack is not None):
            raise ValueError(
                "accept either a draft (draftArtifactRef and draftSha256) or a published pack"
            )
        return self


def _service(session) -> TemporalArtifactService:
    return TemporalArtifactService(TemporalArtifactRepository(session))


async def _read(artifacts, ref, access, *, limit, label, sha256=None):
    try:
        artifact, _, _, policy = await artifacts.get_metadata(
            artifact_id=ref, access=access
        )
        if not policy.raw_access_allowed:
            raise PolicyPackError(
                f"{label} {ref} does not permit reading its original bytes"
            )
        if artifact.size_bytes is not None and artifact.size_bytes > limit:
            raise PolicyPackError(f"{label} {ref} exceeds its byte bound")
        _, raw = await artifacts.read(artifact_id=ref, access=access)
    except TemporalArtifactError as exc:
        raise PolicyPackError(
            f"{label} {ref} is missing or unauthorized ({type(exc).__name__})"
        ) from None
    if len(raw) > limit:
        raise PolicyPackError(f"{label} {ref} exceeds its byte bound")
    if sha256 is not None and hashlib.sha256(raw).hexdigest() != sha256:
        raise PolicyPackError(f"{label} {ref} changed; its SHA-256 differs")
    return artifact, raw


def _parse(model, raw, label):
    from moonmind.security.evidence_exports import validation_summary

    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        raise PolicyPackError(
            f"{label} is invalid: {validation_summary(exc, model)}"
        ) from None


async def _write(artifacts, access, payload: bytes, content_type: str, kind: str):
    artifact, _ = await artifacts.put_content_addressed_payload_complete(
        principal=access.principal,
        payload=payload,
        content_type=content_type,
        scope="framework-packs",
        metadata_json={"artifact_type": kind},
    )
    return artifact.artifact_id, hashlib.sha256(payload).hexdigest()


def _canonical(document) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


async def _documents(artifacts, access, selections):
    documents, records = [], []
    for item in selections:
        name, media = item.artifact_ref, None
        try:
            artifact, raw = await _read(
                artifacts,
                item.artifact_ref,
                access,
                limit=2 * MAX_PACK_BYTES,
                label="Policy document",
                sha256=item.sha256,
            )
            meta = artifact.metadata_json or {}
            name = str(meta.get("filename") or meta.get("name") or item.artifact_ref)[
                :1024
            ]
            media = artifact.content_type
            documents.append(
                read_policy_document(
                    artifact_ref=item.artifact_ref,
                    sha256=item.sha256,
                    name=name,
                    media_type=media,
                    data=raw,
                )
            )
            status, reason = "read", None
        except UnusableDocument as exc:
            status, reason = exc.status, exc.reason
        except PolicyPackError as exc:
            # Named, never skipped: the review sheet lists every unread input.
            status, reason = "unreadable", str(exc)
        records.append(
            DocumentRecord(
                artifact_ref=item.artifact_ref,
                sha256=item.sha256,
                name=name,
                media_type=media,
                status=status,
                reason=reason,
            )
        )
    return documents, records


async def _installed_rules(artifacts, access, refs):
    """Rules an admitted deterministic check actually ran, from retained observations."""
    if not refs:
        return None
    from moonmind.security.assessments import Observation

    rules = set()
    for ref in refs:
        _, raw = await _read(
            artifacts, ref, access, limit=8 * 1024 * 1024, label="Rule inventory"
        )
        observation = _parse(Observation, raw, f"Rule inventory {ref}")
        for row in observation.coverage:
            if row.evidence_method == "deterministic_check":
                rules.update((row.source, row.tool, rule) for rule in row.rule_ids)
    return rules


def _mapping_universe(selection: MappingSelection | None):
    if selection is None:
        return None
    from moonmind.security.framework_registry import standard_requirement_ids

    ids = standard_requirement_ids(selection.framework_id, selection.edition)
    if ids is None:
        raise PolicyPackError(
            "Select an installed catalog standard and its exact edition as the mapping target"
        )
    return ids


async def _pack(artifacts, access, identity: FrameworkPackIdentity, label):
    _, raw = await _read(
        artifacts,
        identity.artifact_ref,
        access,
        limit=MAX_PACK_BYTES,
        label=label,
        sha256=identity.sha256,
    )
    try:
        return load_declared_pack(
            raw,
            pack_id=identity.pack_id,
            edition=identity.edition,
            revision=identity.revision,
        )
    except (ValueError, RecursionError):
        raise PolicyPackError(
            f"{label} format, ID, edition or revision is invalid"
        ) from None


async def draft_policy_pack(
    request: PolicyPackDraftRequest, *, session, access, artifacts=None
):
    artifacts = artifacts or _service(session)
    _, raw = await _read(
        artifacts,
        request.response_artifact_ref,
        access,
        limit=MAX_RESPONSE_BYTES,
        label="Drafting response",
        sha256=request.response_sha256,
    )
    response = _parse(DraftResponse, raw, "Drafting response")
    documents, records = await _documents(artifacts, access, request.documents)
    previous = None
    if request.previous_pack is not None:
        prior = await _pack(artifacts, access, request.previous_pack, "Previous pack")
        previous = (
            PackReference.model_validate(request.previous_pack.model_dump()),
            prior,
        )
    try:
        draft = build_draft(
            response,
            policy=request.policy,
            documents=documents,
            records=records,
            drafted_on=datetime.now(UTC).date(),
            installed_rules=await _installed_rules(
                artifacts, access, request.rule_inventory_refs
            ),
            mapping_target=request.mapping_target,
            mapping_universe=_mapping_universe(request.mapping_target),
            previous=previous,
            revision=request.revision,
        )
    except ValidationError as exc:
        from moonmind.security.evidence_exports import validation_summary

        raise PolicyPackError(
            f"Drafted pack is invalid: {validation_summary(exc, VersionedFrameworkPack)}"
        ) from None
    except PolicyPackError:
        raise
    except ValueError as exc:
        raise PolicyPackError(str(exc)) from None
    draft_ref, draft_sha = await _write(
        artifacts,
        access,
        _canonical(draft.model_dump(mode="json", by_alias=True)),
        "application/json",
        "policy_pack_draft",
    )
    sheet_ref, _ = await _write(
        artifacts,
        access,
        render_review_sheet(draft).encode(),
        "text/markdown",
        "policy_pack_review_sheet",
    )
    return {
        "draftArtifactRef": draft_ref,
        "draftSha256": draft_sha,
        "reviewSheetRef": sheet_ref,
        "packId": draft.pack.id,
        "edition": draft.pack.edition,
        "revision": draft.pack.revision,
        "publicationStatus": draft.pack.publication_status,
        "distribution": draft.pack.distribution,
        "requirementCount": len(draft.pack.requirements),
        "openQuestionCount": len(draft.open_questions),
        "flags": [flag.model_dump(mode="json", by_alias=True) for flag in draft.flags],
        "documents": [
            record.model_dump(mode="json", by_alias=True) for record in draft.documents
        ],
        "changes": (
            draft.changes.model_dump(mode="json", by_alias=True)
            if draft.changes
            else None
        ),
    }


def _register(document: dict, request: PolicyPackAcceptRequest, identity: dict) -> dict:
    """Replace this pack's earlier registration in exactly one saved environment."""
    for project in document.get("projects") or []:
        if project.get("id") != request.project_id:
            continue
        for environment in project.get("environments") or []:
            if environment.get("id") != request.environment_id:
                continue
            frameworks = [
                item
                for item in environment.get("frameworks") or []
                if not (
                    isinstance(item, dict)
                    and "artifactRef" in item
                    and item.get("packId") == identity["packId"]
                )
            ]
            frameworks.append(identity)
            if len(frameworks) > 16:
                raise PolicyPackError("The saved environment already has 16 standards")
            environment["frameworks"] = frameworks
            return document
    raise PolicyPackError("Select an existing saved project environment")


async def accept_policy_pack(
    request: PolicyPackAcceptRequest, *, session, access, artifacts=None
):
    artifacts = artifacts or _service(session)
    settings = SettingsCatalogService(session=session)
    # Refuse a locked registration before publishing anything.
    settings.ensure_write_allowed(SETTINGS_KEY)
    draft = None
    if request.pack is None:
        _, raw = await _read(
            artifacts,
            request.draft_artifact_ref,
            access,
            limit=MAX_PACK_BYTES * 2,
            label="Policy pack draft",
            sha256=request.draft_sha256,
        )
        draft = _parse(PolicyPackDraft, raw, "Policy pack draft")
        base = draft.pack
    else:
        base = await _pack(artifacts, access, request.pack, "Pack")

    async def digest(ref):
        _, raw = await _read(
            artifacts, ref, access, limit=2 * MAX_PACK_BYTES, label="Source document"
        )
        return hashlib.sha256(raw).hexdigest()

    try:
        await verify_source_documents(base, digest)
        published = accept_pack(base, request.review, draft=draft)
    except ValidationError as exc:
        from moonmind.security.evidence_exports import validation_summary

        raise PolicyPackError(
            f"Accepted pack is invalid: {validation_summary(exc, VersionedFrameworkPack)}"
        ) from None
    except PolicyPackError:
        raise
    except ValueError as exc:
        # Contract diagnostics are fixed strings without document text.
        raise PolicyPackError(str(exc)) from None
    payload = _canonical(published.model_dump(mode="json", by_alias=True))
    if len(payload) > MAX_PACK_BYTES:
        raise PolicyPackError("Accepted pack exceeds the 2 MiB byte bound")
    current = await settings.effective_value_async(SETTINGS_KEY)
    # Check the saved environment before publishing; the write below rechecks it.
    _register(
        json.loads(json.dumps(current.value or {})), request, {"packId": published.id}
    )
    ref, sha = await _write(
        artifacts, access, payload, "application/json", "framework_pack"
    )
    # A registered revision must outlive its own expiry: pin it, and seal its
    # source documents as required dependencies so the pin keeps them readable.
    await artifacts.record_verified_dependencies(
        root_artifact_id=ref,
        dependencies=[
            ArtifactDependency(doc_ref, doc_sha, True, "framework_pack.source")
            for doc_ref, doc_sha in sorted(published.source_documents().items())
        ],
        access=access,
    )
    await artifacts.pin(
        artifact_id=ref,
        principal=access.principal,
        reason=(
            f"Framework pack {published.id} revision {published.revision} "
            f"registered in {request.project_id}/{request.environment_id}"
        ),
    )
    identity = FrameworkPackIdentity(
        artifact_ref=ref,
        pack_id=published.id,
        edition=published.edition,
        revision=published.revision,
        sha256=sha,
    ).model_dump(mode="json", by_alias=True)
    document = _register(json.loads(json.dumps(current.value or {})), request, identity)
    result = await settings.apply_overrides(
        changes={SETTINGS_KEY: document},
        expected_versions={SETTINGS_KEY: current.value_version},
        reason=f"Accept framework pack {published.id} revision {published.revision}",
    )
    return {
        **identity,
        "publicationStatus": published.publication_status,
        "effectiveFrom": published.effective_from.isoformat(),
        "effectiveUntil": (
            published.effective_until.isoformat() if published.effective_until else None
        ),
        "projectId": request.project_id,
        "environmentId": request.environment_id,
        "settingsVersion": _version(result),
    }


def _version(result) -> int | None:
    value = result.values.get(SETTINGS_KEY)
    return value.value_version if value is not None else None
