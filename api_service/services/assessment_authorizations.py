"""Current assessment authority and durable reservations in existing owners.

Settings owns reusable authorization. Canonical execution inputs prove legacy
admission. ContainerJob rows retain reservations and the original deadline;
worker payloads never grant authority.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    ContainerJobRecord,
    SettingsOverride,
    TemporalExecutionCanonicalRecord,
)
from moonmind.schemas.container_job_models import ContainerJobSubmitRequest
from moonmind.security.assessment_authorizations import (
    SETTINGS_KEY,
    AssessmentAuthorization,
    AssessmentAuthorizations,
    authorization_scope,
    scope_digest,
)
from moonmind.security.workload_profiles import (
    AdmittedAssessmentScope,
    resolved_job_within_scope,
    selection_within_scope,
)
from moonmind.utils.logging import redact_sensitive_text

_ReviewText = Annotated[str, Field(min_length=1, max_length=8192)]


class _ReviewSection(BaseModel):
    label: Annotated[str, Field(min_length=1, max_length=256)]
    items: Annotated[list[_ReviewText], Field(max_length=256)]


class _ReviewAdmission(BaseModel):
    via: Literal["loopback", "host_publish", "approved_publication", "trusted_ingress"]
    admitted_at: AwareDatetime = Field(alias="admittedAt")


class _AcceptedReview(BaseModel):
    # Copy only the readable contract, never arbitrary canonical inputs,
    # resolved credential values or newly introduced unreviewed metadata.
    schema_version: Literal["assessment-engagement/v1"] = Field(alias="schemaVersion")
    authorization_ref: str = Field(alias="authorizationRef", max_length=128)
    authorization_generation: str = Field(
        alias="authorizationGeneration", max_length=64
    )
    scope_digest: str = Field(alias="scopeDigest", pattern=r"^[a-f0-9]{64}$")
    proposal_digest: str = Field(alias="proposalDigest", pattern=r"^[a-f0-9]{64}$")
    scope: AdmittedAssessmentScope
    sections: Annotated[list[_ReviewSection], Field(min_length=1, max_length=32)]
    limitations: Annotated[list[_ReviewText], Field(min_length=1, max_length=128)]
    admission: _ReviewAdmission


def unavailable_assessment_review():
    return {
        "state": "unavailable",
        "limitations": [
            (
                "The original accepted readable review was not retained or could not be "
                "matched at job admission. Later canonical reviews are not treated "
                "as the original."
            )
        ],
    }


def redact_assessment_evidence(value):
    """Value-free projection; no credential resolution or arbitrary input copy."""
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, dict):
        return {key: redact_assessment_evidence(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_assessment_evidence(item) for item in value]
    return value


async def capture_assessment_review(session, record, scope, request):
    """Capture only at new reservation, while canonical admission is locked.

    Old jobs and exact replays must not call this: a current canonical review
    is not proof of what was accepted when those jobs were originally admitted.
    """
    source = await session.get(
        TemporalExecutionCanonicalRecord, request.source.workflow_id
    )
    try:
        parameters = source.parameters if source else {}
        raw = parameters.get("assessmentReview")
        review = _AcceptedReview.model_validate(raw)
        engagement = scope.engagement
        if (
            AdmittedAssessmentScope.model_validate(parameters.get("assessmentScope"))
            != scope
            or review.scope != scope
            or review.scope_digest != scope_digest(scope)
            or engagement is None
            or review.authorization_ref != engagement.authorization_ref
            or review.authorization_generation != engagement.authorization_generation
            or review.admission.admitted_at > utc(record.created_at)
        ):
            return unavailable_assessment_review()
        projected = review.model_dump(mode="json", by_alias=True, exclude_none=True)
        # Keep the accepted timestamp spelling rather than normalizing its
        # offset. Everything else is the same typed proposal contract.
        projected["admission"]["admittedAt"] = raw["admission"]["admittedAt"]
        if len(json.dumps(projected).encode()) > 65536:
            return unavailable_assessment_review()
    except (TypeError, ValueError):
        return unavailable_assessment_review()
    redacted = redact_assessment_evidence(projected)
    changed = redacted != projected
    return {
        "state": "redacted" if changed else "retained",
        "review": redacted,
        "limitations": ["Credential-shaped text was redacted from the accepted review."]
        if changed
        else [],
    }


class AssessmentAuthorizationDenied(PermissionError):
    """A bounded denial safe to retain in job diagnostics."""


def utc(value: datetime) -> datetime:
    # SQLite drops offsets on persisted UTC timestamps; public scope inputs
    # still require explicit offsets in their Pydantic model.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def _authorization_row(session, *, for_update=False, ensure_legacy_lock=False):
    statement = (
        select(SettingsOverride)
        .where(
            SettingsOverride.scope.is_(None),
            SettingsOverride.key == SETTINGS_KEY,
        )
        .execution_options(populate_existing=True)
    )
    if for_update:
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    if row is None and ensure_legacy_lock:
        # An empty row grants nothing. It gives already-admitted legacy work
        # the same serialization owner as reviewed authorizations.
        try:
            async with session.begin_nested():
                session.add(
                    SettingsOverride(
                        key=SETTINGS_KEY,
                        value_json={"authorizations": []},
                        value_version=1,
                    )
                )
                await session.flush()
        except IntegrityError:
            # Another admission may insert this singleton row first. The
            # savepoint rolls back our insert; select and lock the winner below.
            pass
        row = await session.scalar(statement)
    return row


async def load_assessment_authorization(
    session: AsyncSession,
    authorization_ref: str,
    *,
    for_update: bool = False,
) -> AssessmentAuthorization:
    row = await _authorization_row(session, for_update=for_update)
    try:
        collection = AssessmentAuthorizations.model_validate(
            row.value_json if row else None
        )
        record = next(
            item for item in collection.authorizations if item.ref == authorization_ref
        )
        if not record.enabled or not record.generation:
            raise ValueError("inactive authorization")
        return record
    except (ValueError, TypeError, StopIteration) as exc:
        raise AssessmentAuthorizationDenied(
            "assessment authorization is unavailable or revoked"
        ) from exc


async def validate_current_assessment_authorization(
    session: AsyncSession,
    scope: AdmittedAssessmentScope,
    *,
    for_update: bool = False,
    now: datetime | None = None,
) -> AssessmentAuthorization:
    engagement = scope.engagement
    if engagement is None:
        raise AssessmentAuthorizationDenied(
            "assessment requires its retained canonical legacy admission"
        )
    record = await load_assessment_authorization(
        session, engagement.authorization_ref, for_update=for_update
    )
    if authorization_scope(record) != scope:
        raise AssessmentAuthorizationDenied(
            "assessment authorization changed or was revoked"
        )
    now = now or datetime.now(UTC)
    if not engagement.valid_from <= now < engagement.valid_until:
        raise AssessmentAuthorizationDenied(
            "assessment authorization is outside its validity window"
        )
    return record


async def canonical_assessment_scope(
    session, workflow_id: str | None, *, live=True, for_update=False
):
    source = (
        await session.get(
            TemporalExecutionCanonicalRecord,
            workflow_id,
            populate_existing=True,
            with_for_update=for_update,
        )
        if workflow_id
        else None
    )
    try:
        scope = (
            AdmittedAssessmentScope.model_validate(
                (source.parameters or {}).get("assessmentScope")
            )
            if source
            else None
        )
    except (TypeError, ValueError) as exc:
        raise AssessmentAuthorizationDenied(
            "assessment canonical admission is unavailable"
        ) from exc
    if scope is None:
        raise AssessmentAuthorizationDenied(
            "assessment canonical admission is unavailable"
        )
    if live:
        intent = (source.memo or {}).get("temporalCancel") or {}
        state = getattr(source.state, "value", source.state)
        close = getattr(source.close_status, "value", source.close_status)
        if intent or state == "canceled" or close in {"canceled", "terminated"}:
            raise AssessmentAuthorizationDenied(
                "assessment parent execution was canceled"
            )
    return scope


async def lock_assessment_admission(session, scope, *, workflow_id):
    # Source cancellation and Settings revocation serialize with the brief
    # worker start boundary, in source -> Settings -> job lock order.
    canonical = await canonical_assessment_scope(session, workflow_id, for_update=True)
    if canonical != scope:
        raise AssessmentAuthorizationDenied(
            "assessment scope differs from its canonical admission"
        )
    if scope.engagement is None:
        await _authorization_row(session, for_update=True, ensure_legacy_lock=True)
    else:
        await validate_current_assessment_authorization(session, scope, for_update=True)


def assessment_marked(spec) -> bool:
    profile = spec.workload_profile
    return bool(
        (profile is not None and profile.assessment_ref is not None)
        or any(
            getattr(spec, field, None) is not None
            for field in ("web_assessment", "cloud_assessment", "tls_assessment")
        )
    )


def bind_assessment_job(record, scope, request):
    record.assessment_scope_json = scope.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    if record.assessment_deadline_at is None:
        end = utc(record.created_at) + timedelta(seconds=request.spec.timeout_seconds)
        if scope.engagement is not None:
            end = min(end, scope.engagement.valid_until)
        record.assessment_deadline_at = end


async def persisted_assessment_scope(session, record, request, *, bind=True):
    """Recover old bindings only from retained trusted canonical scope."""
    if record.assessment_scope_json is not None:
        try:
            scope = AdmittedAssessmentScope.model_validate(record.assessment_scope_json)
        except (TypeError, ValueError) as exc:
            raise AssessmentAuthorizationDenied(
                "persisted assessment admission is malformed"
            ) from exc
    elif assessment_marked(request.spec):
        scope = await canonical_assessment_scope(
            session, request.source.workflow_id, live=False
        )
    else:
        return None
    if not selection_within_scope(request.spec, scope):
        raise AssessmentAuthorizationDenied(
            "persisted job exceeds its canonical assessment scope"
        )
    if bind:
        bind_assessment_job(record, scope, request)
    return scope


async def assessment_reserved_budget(session, scope, *, include_native=False):
    """Count admitted reservations without reauthorizing historical actions."""
    spec = ContainerJobRecord.request_json["spec"]
    refs = [
        spec[field]["assessmentRef"].as_string()
        for field in (
            "workloadProfile",
            "webAssessment",
            "cloudAssessment",
            "tlsAssessment",
        )
    ]
    rows = (
        await session.scalars(
            select(ContainerJobRecord).where(
                or_(
                    ContainerJobRecord.assessment_scope_json[
                        "assessmentRef"
                    ].as_string()
                    == scope.assessment_ref,
                    *[ref == scope.assessment_ref for ref in refs],
                )
            )
        )
    ).all()
    count = seconds = 0
    native_reserved = dict.fromkeys(
        ("max_connections", "max_bytes", "max_connections_per_second", "max_concurrent_connections"),
        0,
    )
    for record in rows:
        request = ContainerJobSubmitRequest.model_validate(
            {**record.request_json, "source": record.source_json}
        )
        # These are API-owned reservation records, not a request to act. The
        # recorded reference selects accounting only; it cannot grant launch
        # authority. Today's canonical targets may differ or no longer exist,
        # and counting must neither rebind old jobs nor refund their budgets.
        count += 1
        seconds += request.spec.timeout_seconds
        profile = request.spec.workload_profile
        if profile is not None and profile.native_network is not None:
            for name in native_reserved:
                native_reserved[name] += getattr(profile.native_network.limits, name)
    if include_native:
        return count, seconds, native_reserved
    return count, seconds


async def validate_job_assessment_authority(
    session, record, request, *, live=True, profiles=None
):
    """Called under the worker's verified Activity identity, never user payload authority."""
    saved = ContainerJobSubmitRequest.model_validate(
        {**record.request_json, "source": record.source_json}
    )
    if saved != request:
        raise AssessmentAuthorizationDenied(
            "container job differs from its durable admission"
        )
    if not live:
        # Retained effects need their durable identity, not renewed authority.
        # In particular an old NULL binding need not be reconstructible to
        # observe completed work or publish its already-produced evidence.
        return record.assessment_deadline_at
    # Recover without dirtying the job before acquiring source/Settings locks;
    # an autoflush must not invert the shared source -> Settings -> job order.
    scope = await persisted_assessment_scope(session, record, saved, bind=False)
    if record.cancel_idempotency_key:
        raise AssessmentAuthorizationDenied("container job cancellation was requested")
    if scope is None:
        return None
    await lock_assessment_admission(
        session, scope, workflow_id=saved.source.workflow_id
    )
    bind_assessment_job(record, scope, saved)
    if profiles is not None:
        try:
            resolved_job_within_scope(saved.spec, scope, profiles)
        except (ValueError, PermissionError) as exc:
            raise AssessmentAuthorizationDenied(str(exc)) from exc
    if utc(record.assessment_deadline_at) <= datetime.now(UTC):
        raise AssessmentAuthorizationDenied(
            "assessment job exhausted its original deadline"
        )
    return utc(record.assessment_deadline_at)
