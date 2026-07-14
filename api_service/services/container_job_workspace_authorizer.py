"""API-owned container-job workspace authorization service (MoonMind#3255).

Authority boundary
------------------

The public container-job contract carries only a typed logical workspace
reference (:mod:`moonmind.schemas.workspace_locator_models`). Ownership is
proven in two places with distinct responsibilities:

* **This API-owned service** runs at submission time, *before* the durable
  ``MoonMind.ContainerJob`` workflow is started. It authenticates that the
  referenced run / managed-session / Omnigent-session workspace is backed by a
  live canonical ownership record (``managed_session_store``,
  ``omnigent/bridge_store``, or the run-ownership record) and that the record
  correlates with the authenticated principal and the trusted source
  correlation. It rejects absent, terminally deleted, cross-user, and
  cross-session references *before* any workflow, image acquisition, or mount
  planning happens.
* The **owner-side worker resolver**
  (:mod:`moonmind.workloads.container_workspace`) re-proves containment,
  symlink safety, and source/owner correlation deterministically at every
  Docker boundary and never emits a host path.

Because the durable ownership record lookup requires database / store access
that must not cross a Temporal Activity boundary, the record lookup lives here
at the API. The worker still correlates against the pre-authenticated source
recorded on the trusted workflow input as defense in depth.

The service is deliberately store-agnostic: each supported kind is authorized
through an injected async lookup that returns a normalized
:class:`WorkspaceOwnershipRecord`. Deployments wire the lookups to the real
stores; tests inject fakes. A kind whose lookup is not configured fails closed
with ``permission_denied`` so an unwired deployment can never silently
fall open.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from moonmind.schemas.container_job_models import (
    ContainerJobSubmitRequest,
    OwnerIdentity,
)
from moonmind.schemas.workspace_locator_models import (
    CONTAINER_JOB_WORKSPACE_ADAPTER,
    CONTAINER_WORKSPACE_NOT_FOUND,
    CONTAINER_WORKSPACE_PERMISSION_DENIED,
    ContainerArtifactWorkspaceLocator,
    ContainerManagedSessionWorkspaceLocator,
    ContainerOmnigentWorkspaceLocator,
    ContainerRunWorkspaceLocator,
)


@dataclass(frozen=True, slots=True)
class WorkspaceOwnershipRecord:
    """Normalized durable ownership record for a referenced workspace.

    ``is_terminal`` marks a stale / terminally deleted record. The identity
    fields are whatever the authoritative store persists for the kind; only the
    populated ones participate in correlation.
    """

    principal_id: str | None = None
    workflow_id: str | None = None
    agent_run_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    is_terminal: bool = False


OwnershipLookup = Callable[[str], Awaitable[WorkspaceOwnershipRecord | None]]


class ContainerJobWorkspaceAuthorizationError(RuntimeError):
    """Stable, caller-visible workspace authorization failure.

    ``code`` is one of the container-job classification strings
    (``workspace_not_found`` / ``permission_denied``) so the API surface can map
    it onto the durable failure taxonomy without leaking any host path.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class ContainerJobWorkspaceAuthorizer:
    """Authorize a logical workspace reference against durable ownership."""

    def __init__(
        self,
        *,
        run_owner_lookup: OwnershipLookup | None = None,
        managed_session_lookup: OwnershipLookup | None = None,
        omnigent_owner_lookup: OwnershipLookup | None = None,
    ) -> None:
        self._run_owner_lookup = run_owner_lookup
        self._managed_session_lookup = managed_session_lookup
        self._omnigent_owner_lookup = omnigent_owner_lookup

    async def authorize(
        self, *, owner: OwnerIdentity, request: ContainerJobSubmitRequest
    ) -> None:
        """Fail closed unless the reference is backed by a live owned record."""

        locator = CONTAINER_JOB_WORKSPACE_ADAPTER.validate_python(
            request.spec.workspace_ref
        )
        source = request.source

        if isinstance(locator, ContainerArtifactWorkspaceLocator):
            # Artifact-materialization workspaces are owner-scoped structurally
            # by the worker resolver; here we only require a genuinely
            # authenticated principal (never the default system placeholder).
            if not _is_authenticated(owner):
                raise ContainerJobWorkspaceAuthorizationError(
                    CONTAINER_WORKSPACE_PERMISSION_DENIED,
                    "artifact workspace requires an authenticated owner",
                )
            return

        if isinstance(locator, ContainerRunWorkspaceLocator):
            record = await self._load(self._run_owner_lookup, locator.run_id, "run")
            self._correlate(
                record,
                owner=owner,
                primary_field="run_id",
                primary_trusted={source.run_id, source.workflow_id, source.agent_run_id},
                secondary={"workflow_id": {source.workflow_id}},
            )
            return

        if isinstance(locator, ContainerManagedSessionWorkspaceLocator):
            record = await self._load(
                self._managed_session_lookup, locator.session_id, "managed session"
            )
            self._correlate(
                record,
                owner=owner,
                primary_field="session_id",
                primary_trusted={source.managed_session_id},
                secondary={"agent_run_id": {source.agent_run_id}},
                require_principal=True,
            )
            return

        if isinstance(locator, ContainerOmnigentWorkspaceLocator):
            record = await self._load(
                self._omnigent_owner_lookup, locator.session_id, "omnigent session"
            )
            self._correlate(
                record,
                owner=owner,
                primary_field="session_id",
                primary_trusted={source.omnigent_session_id},
                secondary={
                    "workflow_id": {source.workflow_id},
                    "agent_run_id": {source.agent_run_id},
                },
            )
            return

        # No competing locator family is permitted here; the discriminated
        # adapter above guarantees one of the known kinds.
        raise ContainerJobWorkspaceAuthorizationError(  # pragma: no cover
            CONTAINER_WORKSPACE_PERMISSION_DENIED,
            "unsupported container-job workspace kind",
        )

    async def _load(
        self, lookup: OwnershipLookup | None, identity: str, label: str
    ) -> WorkspaceOwnershipRecord:
        if lookup is None:
            # An unconfigured kind can never be proven owned; fail closed.
            raise ContainerJobWorkspaceAuthorizationError(
                CONTAINER_WORKSPACE_PERMISSION_DENIED,
                f"{label} workspace ownership cannot be authorized on this deployment",
            )
        record = await lookup(identity)
        if record is None:
            raise ContainerJobWorkspaceAuthorizationError(
                CONTAINER_WORKSPACE_NOT_FOUND,
                f"{label} ownership record does not exist",
            )
        if record.is_terminal:
            raise ContainerJobWorkspaceAuthorizationError(
                CONTAINER_WORKSPACE_NOT_FOUND,
                f"{label} ownership record is terminally deleted",
            )
        return record

    @staticmethod
    def _correlate(
        record: WorkspaceOwnershipRecord,
        *,
        owner: OwnerIdentity,
        primary_field: str,
        primary_trusted: set[str | None],
        secondary: dict[str, set[str | None]] | None = None,
        require_principal: bool = False,
    ) -> None:
        # Owner principal correlation when the record persists one.
        if require_principal and not record.principal_id:
            raise ContainerJobWorkspaceAuthorizationError(
                CONTAINER_WORKSPACE_PERMISSION_DENIED,
                "workspace ownership record does not contain principal proof",
            )
        if record.principal_id and record.principal_id != owner.principal_id:
            raise ContainerJobWorkspaceAuthorizationError(
                CONTAINER_WORKSPACE_PERMISSION_DENIED,
                "workspace is owned by a different principal",
            )
        # The primary identity must be proven by a value the API authenticated
        # on the trusted SourceCorrelation, NOT by the caller-supplied locator.
        # The record is loaded by the caller's locator id, so requiring the
        # record's persisted id to be one the source authenticated is what
        # rejects a caller naming another run's / session's workspace id.
        trusted = {value for value in primary_trusted if value}
        if not trusted:
            raise ContainerJobWorkspaceAuthorizationError(
                CONTAINER_WORKSPACE_PERMISSION_DENIED,
                "authenticated source does not identify the referenced workspace",
            )
        record_primary = getattr(record, primary_field)
        if record_primary is None or record_primary not in trusted:
            raise ContainerJobWorkspaceAuthorizationError(
                CONTAINER_WORKSPACE_PERMISSION_DENIED,
                "workspace reference does not correlate with the owning record",
            )
        # Secondary identities correlate only when both sides carry them.
        for field, candidates in (secondary or {}).items():
            record_value = getattr(record, field)
            allowed = {value for value in candidates if value}
            if record_value is not None and allowed and record_value not in allowed:
                raise ContainerJobWorkspaceAuthorizationError(
                    CONTAINER_WORKSPACE_PERMISSION_DENIED,
                    "workspace reference does not correlate with the owning record",
                )


def _is_authenticated(owner: OwnerIdentity) -> bool:
    from moonmind.schemas.container_job_models import (
        DEFAULT_CONTAINER_JOB_PRINCIPAL_ID,
    )

    if not owner.principal_id:
        return False
    return not (
        owner.principal_id == DEFAULT_CONTAINER_JOB_PRINCIPAL_ID
        and owner.principal_type == "system"
    )


# ---------------------------------------------------------------------------
# Concrete adapters that wire the authorizer to the real durable stores. These
# are the deployment-facing bridge from an authoritative store record to the
# store-agnostic normalized :class:`WorkspaceOwnershipRecord` the authorizer
# consumes. Tests may use them with real stores or substitute fakes.
# ---------------------------------------------------------------------------


def managed_session_ownership_lookup(store) -> OwnershipLookup:
    """Build a managed-session lookup backed by a real ``ManagedSessionStore``.

    A terminal supervision status (terminated / degraded / failed) marks the
    record as terminally deleted so a stale reference fails closed.
    """

    from moonmind.workflows.temporal.runtime.managed_session_store import (
        TERMINAL_MANAGED_SESSION_STATUSES,
    )

    async def lookup(session_id: str) -> WorkspaceOwnershipRecord | None:
        record = await asyncio.to_thread(store.load, session_id)
        if record is None:
            return None
        return WorkspaceOwnershipRecord(
            session_id=record.session_id,
            agent_run_id=record.agent_run_id,
            principal_id=record.metadata.get("principalId"),
            is_terminal=record.status in TERMINAL_MANAGED_SESSION_STATUSES,
        )

    return lookup


def omnigent_ownership_lookup(store) -> OwnershipLookup:
    """Build an Omnigent lookup backed by a real ``OmnigentBridgeSessionStore``.

    Ownership is proven by the durable MoonMind workflow / agent-run binding the
    bridge store persists for the provider session id.
    """

    async def lookup(session_id: str) -> WorkspaceOwnershipRecord | None:
        binding = await store.get_session_owner(session_id)
        if binding is None:
            return None
        return WorkspaceOwnershipRecord(
            session_id=session_id,
            workflow_id=binding.workflow_id,
            agent_run_id=binding.agent_run_id,
        )

    return lookup
