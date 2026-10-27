"""Owner-scoped durable container-job operations for MoonMind#3259.

This module is the single API-owned service that both the authenticated HTTP
router and the MCP transport call. Neither transport executes Docker or waits
for terminal completion; Temporal owns long-running execution. Reads and cancel
are always owner-scoped, and log/artifact evidence is returned as bounded pages
over durable references, never as an unbounded daemon stream.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import ContainerJobRecord
from api_service.services.registry_authorization import (
    PrivateImageAuthorizationService,
    load_default_authorization_policy,
)
from moonmind.config.container_backend_settings import (
    ContainerBackendConfigError,
    ContainerBackendSettings,
    RegistryImageSource,
    resolve_container_backend_settings,
)
from moonmind.schemas.container_job_models import (
    MAX_ARTIFACT_PAGE_ENTRIES,
    MAX_LOG_PAGE_ENTRIES,
    AuxiliaryOutcome,
    ContainerJobAccepted,
    ContainerJobArtifact,
    ContainerJobArtifactPage,
    ContainerJobCancelRequest,
    ContainerJobCancelResult,
    ContainerJobFailureClass,
    ContainerJobLogEntry,
    ContainerJobLogPage,
    ContainerJobLogQuery,
    ContainerJobSpec,
    ContainerJobState,
    ContainerJobStatus,
    ContainerJobSubmitRequest,
    ContainerJobWorkflowInput,
    GpuObservation,
    ImageObservation,
    OwnerIdentity,
    RegistryAuthorization,
    TerminalOutcome,
    container_job_workflow_id,
    require_explicit_resources,
)
from moonmind.security.container_job_capabilities import (
    ContainerJobSessionCapability,
    container_job_matches_capability,
)
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.workflows.temporal.artifacts import ArtifactAccessContext
from moonmind.workflows.temporal.client import TemporalClientAdapter

_STDERR_MARKER = "[stderr]"
_TEMPORAL_START_FAILURE_MESSAGE = (
    "Temporal workflow start failed before the durable handoff completed."
)


class ContainerJobNotFoundError(RuntimeError):
    pass


class ContainerJobIdempotencyConflictError(RuntimeError):
    pass


class ContainerJobAuthorizationError(RuntimeError):
    """Raised when a submission is denied private-image authorization.

    Carries the bounded, non-sensitive authorization outcome so callers can map
    the specific failure class (denied image use, repository-scope mismatch, ...)
    to a permission-denied response without leaking credential material.
    """

    def __init__(self, authorization: RegistryAuthorization) -> None:
        self.authorization = authorization
        super().__init__(authorization.message or "private-image use is denied")


class ContainerJobEvidenceUnavailableError(RuntimeError):
    """Raised when a durable log/artifact reference cannot be resolved."""


def owner_artifact_principal(owner: OwnerIdentity) -> str:
    """Return the artifact-store principal a job's evidence was published under."""

    return f"{owner.principal_type}:{owner.principal_id}"


class ContainerJobArtifactReader(Protocol):
    """Minimal read surface the service needs from the artifact store.

    ``TemporalArtifactService`` satisfies this protocol directly; the service
    never resolves raw host paths and only reads bounded durable references.
    """

    async def get_metadata(
        self, *, artifact_id: str, access: ArtifactAccessContext
    ) -> tuple[Any, Any, bool, Any]:
        pass

    async def read(
        self,
        *,
        artifact_id: str,
        access: ArtifactAccessContext,
    ) -> tuple[Any, bytes]:
        pass

    async def delegate_read_access(
        self,
        *,
        artifact_ids: Iterable[str],
        workflow_id: str,
        access: ArtifactAccessContext,
    ) -> None:
        pass


class ContainerJobRepository:

    def __init__(
        self,
        session: AsyncSession,
        *,
        capability: ContainerJobSessionCapability | None = None,
        operator: OperatorAdmission | None = None,
    ) -> None:
        if operator is not None and (
            not isinstance(operator, OperatorAdmission) or capability is not None
        ):
            raise ValueError("Operator and machine authority must remain separate")
        self._session = session
        self._capability = capability
        self._operator = operator

    def _owner_scope(self, owner: OwnerIdentity):
        exact = and_(
            ContainerJobRecord.owner_id == owner.principal_id,
            ContainerJobRecord.owner_type == owner.principal_type,
        )
        if self._operator is None:
            return exact
        return or_(
            exact,
            and_(
                ContainerJobRecord.owner_type == "user",
                ContainerJobRecord.operator_access_granted.is_(True),
            ),
        )

    async def create_or_replay(
        self,
        *,
        owner: OwnerIdentity,
        request: ContainerJobSubmitRequest,
        authorization: RegistryAuthorization | None = None,
    ) -> tuple[ContainerJobRecord, bool]:
        existing = await self._by_idempotency(owner, request.idempotency_key)
        request_json = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        if existing is not None:
            if existing.request_json != request_json:
                raise ContainerJobIdempotencyConflictError("idempotency key was already used with a different request")
            return existing, True
        now = datetime.now(timezone.utc)
        record = ContainerJobRecord(
            job_id=f"container-job:{uuid4().hex}",
            owner_id=owner.principal_id,
            owner_type=owner.principal_type,
            idempotency_key=request.idempotency_key,
            source_json=request.source.model_dump(mode="json", by_alias=True, exclude_none=True),
            request_json=request_json,
            authorization_observation_json=(
                authorization.model_dump(mode="json", by_alias=True, exclude_none=True)
                if authorization is not None
                else None
            ),
            state=ContainerJobState.QUEUED.value,
            publication_outcome_json={"state": "not_attempted"},
            cleanup_outcome_json={"state": "not_attempted"},
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(record)
                await self._session.flush()
            return record, False
        except IntegrityError:
            existing = await self._by_idempotency(owner, request.idempotency_key)
            if existing is None:
                raise
            if existing.request_json != request_json:
                raise ContainerJobIdempotencyConflictError("idempotency key was already used with a different request")
            return existing, True

    async def _by_idempotency(self, owner: OwnerIdentity, key: str) -> ContainerJobRecord | None:
        rows = (
            await self._session.scalars(
                select(ContainerJobRecord)
                .where(
                    self._owner_scope(owner),
                    ContainerJobRecord.idempotency_key == key,
                )
                .limit(2)
            )
        ).all()
        if len(rows) > 1:
            raise ContainerJobIdempotencyConflictError(
                "idempotency key identifies multiple retained jobs"
            )
        return rows[0] if rows else None

    async def find_exact_replay(
        self, *, owner: OwnerIdentity, request: ContainerJobSubmitRequest
    ) -> ContainerJobRecord | None:
        existing = await self._by_idempotency(owner, request.idempotency_key)
        if existing is None:
            return None
        request_json = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        if existing.request_json != request_json:
            raise ContainerJobIdempotencyConflictError(
                "idempotency key was already used with a different request"
            )
        return existing

    async def get_for_owner(self, *, owner: OwnerIdentity, job_id: str) -> ContainerJobRecord | None:
        result = await self._session.execute(
            select(ContainerJobRecord).where(
                ContainerJobRecord.job_id == job_id,
                self._owner_scope(owner),
            )
        )
        record = result.scalar_one_or_none()
        if record is not None and self._capability is not None:
            if owner != self._capability.owner:
                return None
            try:
                # Source correlation is the API-owned persisted authority,
                # never an assertion supplied with a later read/cancel call.
                saved = ContainerJobSubmitRequest.model_validate({
                    **record.request_json, "source": record.source_json,
                })
            except (ValueError, TypeError):
                return None
            if not container_job_matches_capability(saved, self._capability):
                return None
        return record

    async def record_observation(
        self, *, owner: OwnerIdentity, job_id: str, state: ContainerJobState,
        backend_kind: str | None = None, backend_ref: str | None = None,
        image: ImageObservation | None = None, terminal: TerminalOutcome | None = None,
        gpu: GpuObservation | None = None,
        publication: AuxiliaryOutcome | None = None, cleanup: AuxiliaryOutcome | None = None,
        logs_ref: str | None = None, artifacts_ref: str | None = None,
    ) -> ContainerJobRecord:
        record = await self.get_for_owner(owner=owner, job_id=job_id)
        if record is None:
            raise ContainerJobNotFoundError(job_id)
        record.state = state.value
        if backend_kind is not None:
            record.backend_kind = backend_kind
        if backend_ref is not None:
            record.backend_ref = backend_ref
        if image is not None:
            record.image_observation_json = image.model_dump(mode="json", by_alias=True, exclude_none=True)
        if gpu is not None:
            record.gpu_observation_json = gpu.model_dump(mode="json", by_alias=True, exclude_none=True)
        if terminal is not None:
            record.terminal_outcome_json = terminal.model_dump(mode="json", by_alias=True, exclude_none=True)
        if publication is not None:
            record.publication_outcome_json = publication.model_dump(mode="json", by_alias=True, exclude_none=True)
        if cleanup is not None:
            record.cleanup_outcome_json = cleanup.model_dump(mode="json", by_alias=True, exclude_none=True)
        if logs_ref is not None:
            record.logs_ref = logs_ref
        if artifacts_ref is not None:
            record.artifacts_ref = artifacts_ref
        await self._session.flush()
        return record


class ContainerJobService:

    def __init__(
        self,
        session: AsyncSession,
        *,
        temporal: TemporalClientAdapter | None = None,
        authorizer: PrivateImageAuthorizationService | None = None,
        artifacts: ContainerJobArtifactReader | None = None,
        backend_settings: ContainerBackendSettings | None = None,
        capability: ContainerJobSessionCapability | None = None,
        operator: OperatorAdmission | None = None,
        artifact_access: ArtifactAccessContext | None = None,
    ) -> None:
        self._session = session
        self.repository = ContainerJobRepository(
            session, capability=capability, operator=operator
        )
        self._artifact_access = (
            ArtifactAccessContext.require(artifact_access)
            if artifact_access is not None
            else (
                ArtifactAccessContext.for_operator(operator)
                if operator is not None
                else (
                    ArtifactAccessContext.for_execution(capability.workflow_id)
                    if capability is not None
                    else None
                )
            )
        )
        self._temporal = temporal or TemporalClientAdapter()
        self._artifacts = artifacts
        self._authorizer = authorizer or PrivateImageAuthorizationService(
            load_default_authorization_policy()
        )
        self._backend_settings = (
            backend_settings or resolve_container_backend_settings()
        )

    async def submit(self, *, owner: OwnerIdentity, request: ContainerJobSubmitRequest) -> ContainerJobAccepted:
        from moonmind.security.repository_scanners import validate_scanner_job

        validate_scanner_job(request.spec)
        await self._authorize_input_attachments(owner=owner, spec=request.spec)
        # Authorize private-image execution and credential use before creating
        # durable identity. Fails closed: a denied request never starts a
        # workflow and never persists a queued record.
        self._backend_settings.authorize_service(request.spec)
        self._backend_settings.authorize_egress(request)
        authorization = None
        if request.spec.image is not None:
            authorization = self._authorizer.authorize(
                owner=owner, spec=request.spec
            )
            if not authorization.authorized:
                raise ContainerJobAuthorizationError(authorization)
        else:
            # Public requests select only an opaque deployment-approved alias.
            # Recipe paths and registry references remain worker-side policy.
            try:
                source = self._backend_settings.image_source(
                    request.spec.image_source_ref or ""
                )
            except ContainerBackendConfigError as exc:
                raise ValueError("container image source is not configured") from exc
            if isinstance(source, RegistryImageSource):
                authorization = self._authorizer.authorize(
                    owner=owner,
                    spec=request.spec.model_copy(
                        update={
                            "image_source_ref": None,
                            "image": source.image,
                            "pull_policy": source.pull_policy,
                            "registry_credential_ref": source.registry_credential_ref,
                        }
                    ),
                )
                if not authorization.authorized:
                    raise ContainerJobAuthorizationError(authorization)
        existing = await self.repository.find_exact_replay(owner=owner, request=request)
        if existing is not None:
            # An exact idempotent replay preserves the existing job identity
            # and the original serialized request comparison, including
            # historical zero-valued or memory-range requests. Only new
            # execution is gated below.
            record, replayed = existing, True
        else:
            # Already-admitted legacy scanner jobs stay executable, including
            # by exact replay; new ones must read a captured input instead of
            # the live workspace.
            validate_scanner_job(request.spec, allow_legacy=False)
            try:
                require_explicit_resources(request.spec.resources)
            except ValueError as exc:
                raise ValueError(
                    "container-job resources must be explicit for new jobs: "
                    f"{exc}"
                ) from exc
            record, replayed = await self.repository.create_or_replay(
                owner=owner, request=request, authorization=authorization
            )

        terminal = record.terminal_outcome_json
        recovering_start_failure = bool(
            record.state == ContainerJobState.FAILED.value
            and isinstance(terminal, dict)
            and terminal.get("failureClass")
            == ContainerJobFailureClass.TEMPORAL_START.value
        )
        completed_replay = bool(
            existing is not None
            and not recovering_start_failure
            and record.state
            in {
                ContainerJobState.SUCCEEDED.value,
                ContainerJobState.FAILED.value,
                ContainerJobState.CANCELED.value,
                ContainerJobState.TIMED_OUT.value,
                ContainerJobState.REJECTED.value,
            }
        )
        if completed_replay:
            # Confirmed terminal work needs no new workflow start, including
            # when Temporal history has expired or its service is unavailable.
            return ContainerJobAccepted(
                jobId=record.job_id, replayed=True, createdAt=record.created_at
            )
        inputs = [
            item.artifact_ref
            for item in request.spec.input_attachments or ()
            if item.artifact_ref
        ]
        if inputs:
            # The trusted caller just approved these exact rights, including
            # restricted raw bytes. Persist them for the canonical job before
            # starting it; its Activity payload cannot grant those rights later.
            await self._artifacts.delegate_read_access(
                artifact_ids=inputs,
                workflow_id=container_job_workflow_id(record.job_id),
                access=ArtifactAccessContext.require(self._artifact_access),
            )
        if recovering_start_failure:
            record.state = ContainerJobState.QUEUED.value
            record.terminal_outcome_json = None
            record.updated_at = datetime.now(timezone.utc)

        # The API-owned job identity is authoritative for every later status
        # read and for worker projection Activities. Commit it before handing
        # execution to Temporal; a request-scoped AsyncSession otherwise rolls
        # back the flushed row when the transport returns. Exact replays also
        # reissue the deterministic Temporal start so a prior start failure can
        # recover without creating another job identity.
        await self._session.commit()
        try:
            await self._temporal.start_container_job(
                ContainerJobWorkflowInput(
                    jobId=record.job_id,
                    owner=OwnerIdentity(
                        principalId=record.owner_id, principalType=record.owner_type
                    ),
                    request=request,
                    registryAuthorization=authorization,
                )
            )
        except Exception:
            record.state = ContainerJobState.FAILED.value
            record.terminal_outcome_json = TerminalOutcome(
                failureClass=ContainerJobFailureClass.TEMPORAL_START,
                message=_TEMPORAL_START_FAILURE_MESSAGE,
            ).model_dump(mode="json", by_alias=True, exclude_none=True)
            record.updated_at = datetime.now(timezone.utc)
            await self._session.commit()
            raise
        created_at = record.created_at or datetime.now(timezone.utc)
        return ContainerJobAccepted(jobId=record.job_id, replayed=replayed, createdAt=created_at)

    async def _authorize_input_attachments(
        self, *, owner: OwnerIdentity, spec: ContainerJobSpec
    ) -> None:
        """Admit artifact inputs under the job owner's existing read authority.

        The worker reads them under this job's delegated input rights, so
        quarantine or retention lost before launch still fails closed. Missing and
        unreadable artifacts are refused alike, without revealing which.
        """

        for item in spec.input_attachments or ():
            if item.artifact_ref is None:
                continue
            if self._artifacts is None:
                raise ValueError("artifact inputs require the artifact store")
            try:
                artifact, _links, _pinned, policy = await self._artifacts.get_metadata(
                    artifact_id=item.artifact_ref,
                    access=ArtifactAccessContext.require(self._artifact_access),
                )
                if not policy.raw_access_allowed:
                    raise ValueError("input attachment raw bytes are not readable")
            except Exception as exc:  # noqa: BLE001 - authority decides
                raise ValueError(
                    "input attachment artifact is not readable by the job owner"
                ) from exc
            status = getattr(artifact, "status", None)
            if getattr(status, "value", status) != "complete":
                raise ValueError("input attachment artifact is not complete")
            size = getattr(artifact, "size_bytes", None)
            if isinstance(size, int) and size > item.max_bytes:
                raise ValueError("input attachment artifact exceeds its bound")

    async def status(self, *, owner: OwnerIdentity, job_id: str) -> ContainerJobStatus:
        record = await self.repository.get_for_owner(owner=owner, job_id=job_id)
        if record is None:
            raise ContainerJobNotFoundError(job_id)
        return ContainerJobStatus(
            jobId=record.job_id, state=record.state, backendKind=record.backend_kind,
            backendRef=record.backend_ref, image=record.image_observation_json,
            authorization=record.authorization_observation_json,
            gpu=record.gpu_observation_json,
            terminal=record.terminal_outcome_json, publication=record.publication_outcome_json,
            cleanup=record.cleanup_outcome_json, logsRef=record.logs_ref,
            artifactsRef=record.artifacts_ref, updatedAt=record.updated_at or record.created_at,
        )

    async def cancel(self, *, owner: OwnerIdentity, job_id: str, request: ContainerJobCancelRequest) -> ContainerJobCancelResult:
        record = await self.repository.get_for_owner(owner=owner, job_id=job_id)
        if record is None:
            raise ContainerJobNotFoundError(job_id)
        replayed = record.cancel_idempotency_key == request.idempotency_key
        terminal = record.state in {"succeeded", "failed", "canceled", "timed_out", "rejected"}
        if not terminal and not replayed:
            record.cancel_idempotency_key = request.idempotency_key
            record.state = ContainerJobState.CANCELING.value
            await self.repository._session.flush()
            await self._temporal.signal_container_job_cancel(job_id)
            await self._session.commit()
        return ContainerJobCancelResult(jobId=job_id, state=record.state, accepted=not terminal, replayed=replayed)

    async def logs(
        self, *, owner: OwnerIdentity, job_id: str, query: ContainerJobLogQuery | None = None
    ) -> ContainerJobLogPage:
        """Return one bounded log page over the job's durable log reference."""

        query = query or ContainerJobLogQuery()
        record = await self.repository.get_for_owner(owner=owner, job_id=job_id)
        if record is None:
            raise ContainerJobNotFoundError(job_id)
        if not record.logs_ref:
            # Evidence is not published yet (or the job produced none): the
            # bounded contract is an empty page, not an unbounded daemon stream.
            return ContainerJobLogPage(jobId=job_id, entries=[], nextCursor=None)

        text = await self._read_evidence_text(
            owner=OwnerIdentity(
                principalId=record.owner_id, principalType=record.owner_type
            ),
            artifact_ref=record.logs_ref,
        )
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()

        offset = self._decode_cursor(query.cursor)
        limit = min(query.limit, MAX_LOG_PAGE_ENTRIES)
        timestamp = record.updated_at or record.created_at or datetime.now(timezone.utc)

        stream = "stdout"
        entries: list[ContainerJobLogEntry] = []
        for index, line in enumerate(lines):
            if line.strip() == _STDERR_MARKER:
                stream = "stderr"
            if index < offset or len(entries) >= limit:
                continue
            entries.append(
                ContainerJobLogEntry(
                    sequence=index,
                    timestamp=timestamp,
                    stream=stream,
                    text=line[:8192],
                )
            )
        next_index = offset + len(entries)
        next_cursor = str(next_index) if next_index < len(lines) else None
        return ContainerJobLogPage(jobId=job_id, entries=entries, nextCursor=next_cursor)

    async def artifacts(
        self, *, owner: OwnerIdentity, job_id: str, cursor: str | None = None, limit: int = MAX_ARTIFACT_PAGE_ENTRIES
    ) -> ContainerJobArtifactPage:
        """Return authorized output references plus publication diagnostics."""

        record = await self.repository.get_for_owner(owner=owner, job_id=job_id)
        if record is None:
            raise ContainerJobNotFoundError(job_id)
        publication = AuxiliaryOutcome.model_validate(
            record.publication_outcome_json or {"state": "not_attempted"}
        )
        artifacts: list[ContainerJobArtifact] = []
        if record.artifacts_ref:
            # ``artifacts_ref`` points at the bounded output manifest. Return
            # its collected output references, rather than describing the
            # manifest wrapper as though it were the job's sole output.
            manifest_text = await self._read_evidence_text(
                owner=OwnerIdentity(
                    principalId=record.owner_id, principalType=record.owner_type
                ),
                artifact_ref=record.artifacts_ref,
            )
            try:
                manifest = ContainerJobArtifactPage.model_validate(
                    json.loads(manifest_text)
                )
            except (ValueError, TypeError) as exc:
                raise ContainerJobEvidenceUnavailableError(
                    "artifact manifest is invalid"
                ) from exc
            artifacts.extend(manifest.artifacts)
        offset = self._decode_cursor(cursor)
        limit = min(max(1, limit), MAX_ARTIFACT_PAGE_ENTRIES)
        page = artifacts[offset : offset + limit]
        next_index = offset + len(page)
        next_cursor = str(next_index) if next_index < len(artifacts) else None
        return ContainerJobArtifactPage(
            jobId=job_id, artifacts=page, nextCursor=next_cursor, publication=publication
        )

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if not cursor:
            return 0
        try:
            value = int(cursor)
        except ValueError as exc:
            raise ValueError("cursor is not a valid offset") from exc
        if value < 0:
            raise ValueError("cursor must be non-negative")
        return value

    async def _read_evidence_text(self, *, owner: OwnerIdentity, artifact_ref: str) -> str:
        if self._artifacts is None:
            raise ContainerJobEvidenceUnavailableError("artifact store is not configured")
        try:
            _artifact, payload = await self._artifacts.read(
                artifact_id=artifact_ref,
                # The public read already selected this exact durable reference
                # from the owner/capability-checked job record. It grants only
                # ordinary result bytes, never raw or operator authority.
                access=ArtifactAccessContext.require(
                    self._artifact_access
                )._with_verified_inputs(
                    [artifact_ref.removeprefix("artifact://").removeprefix("artifact:")]
                ),
            )
        except ContainerJobEvidenceUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalized to a stable evidence error
            raise ContainerJobEvidenceUnavailableError(
                "log evidence is not available"
            ) from exc
        return payload.decode("utf-8", errors="replace")

    async def _describe_artifact(
        self, *, owner: OwnerIdentity, job_id: str, artifact_ref: str
    ) -> ContainerJobArtifact:
        if self._artifacts is None:
            raise ContainerJobEvidenceUnavailableError("artifact store is not configured")
        try:
            artifact, _links, _pinned, _read_policy = (
                await self._artifacts.get_metadata(
                    artifact_id=artifact_ref,
                    access=ArtifactAccessContext.require(
                        self._artifact_access
                    )._with_verified_inputs(
                        [
                            artifact_ref.removeprefix("artifact://").removeprefix(
                                "artifact:"
                            )
                        ]
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - normalized to a stable evidence error
            raise ContainerJobEvidenceUnavailableError(
                "artifact evidence is not available"
            ) from exc
        sha256 = getattr(artifact, "sha256", None)
        size_bytes = getattr(artifact, "size_bytes", None)
        metadata = getattr(artifact, "metadata_json", None) or {}
        if not sha256 or size_bytes is None:
            raise ContainerJobEvidenceUnavailableError(
                "artifact evidence is incomplete"
            )
        name = str(metadata.get("name") or f"{job_id}-outputs")
        return ContainerJobArtifact(
            name=name[:255],
            artifactRef=artifact_ref,
            sizeBytes=int(size_bytes),
            sha256=str(sha256),
        )
