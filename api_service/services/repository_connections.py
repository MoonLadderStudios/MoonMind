"""Single database/service writer for scoped repository connections (#4005).

Canonical writable authority for connections, assignments, and route
defaults.  Deployment JSON files are versioned read-only snapshots published
from these rows (or classified legacy input); this service never reads a
filesystem record as fallback policy and never prefers a stale snapshot
because the database is unavailable.

All mutations run in one database transaction together with the
metadata-only audit record.  Concurrent default/disable/assignment changes
produce one valid admitted snapshot or an explicit conflict, never a dangling
binding.  Permission checks precede candidate enumeration and mutation.
Secret bodies are never persisted here: credential configuration carries only
SecretRef locators or App refs, and raw PAT-looking values are rejected.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, Sequence

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    RepositoryConnectionAssignment,
    RepositoryConnectionAuditEvent,
    RepositoryConnectionRecord,
    RepositoryRouteDefault,
)
from moonmind.workflows.executions.repository_contract import (
    REPOSITORY_DENIED,
    REPOSITORY_ID_REUSE,
    REPOSITORY_POLICY_CONFLICT,
    REPOSITORY_ROUTE_CONFLICT,
    REPOSITORY_SETUP_REQUIRED,
    RepositoryAssignment,
    RepositoryConnection,
    RepositoryIdentity,
    RepositoryRouteError,
    authorize_connection_use,
    normalize_endpoint,
    normalize_scope,
    validate_endpoint_retarget,
    validate_scoped_connection_for_write,
)


class RepositoryConnectionConflict(RepositoryRouteError):
    """Transactional conflict mapped from a uniqueness/revision violation."""


def _forbidden_secret_shapes(config: Mapping[str, Any]) -> str | None:
    """Reject raw secret bodies in metadata-only credential configuration."""

    forbidden_keys = {"token", "pat", "password", "secret", "bearer"}
    stack: list[Any] = [config]
    while stack:
        item = stack.pop()
        if isinstance(item, Mapping):
            for key, value in item.items():
                lowered = str(key).strip().lower()
                if lowered in forbidden_keys and isinstance(value, str) and len(value) >= 8:
                    return str(key)
                stack.append(value)
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return None


def _credential_config(connection: RepositoryConnection) -> dict[str, Any]:
    config = connection.credential.model_dump(by_alias=True, mode="json")
    offending = _forbidden_secret_shapes(config)
    if offending:
        raise RepositoryRouteError(
            REPOSITORY_DENIED,
            "credential configuration must be metadata-only (SecretRef/App refs)",
        )
    return config


def _record_to_connection(record: RepositoryConnectionRecord) -> RepositoryConnection:
    credential = dict(record.credential_config)
    ownership = {
        "ownerRef": record.owner_ref,
        "scopeType": record.scope_type,
        "allowedPrincipalRefs": list(record.allowed_principal_refs or []),
    }
    if record.scope_ref is not None:
        ownership["scopeRef"] = record.scope_ref
    return RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": record.connection_id,
            "provider": record.provider,
            "displayName": record.display_name,
            "endpointRef": record.endpoint_ref,
            "allowedOperations": list(record.allowed_operations or []),
            "clientPolicy": dict(record.client_policy or {}),
            "credential": credential,
            "lifecycle": record.lifecycle,
            "policyRevision": record.policy_revision,
            "credentialRevision": record.credential_revision,
            "ownership": ownership,
            "hostingService": record.hosting_service,
        }
    )


def _repo_key_for(identity: RepositoryIdentity) -> str:
    if (identity.provider_repo_id or "").strip():
        return f"id:{identity.provider_repo_id.strip()}"
    return f"remote:{(identity.canonical_remote or '').strip()}"


def _assignment_to_row(
    assignment: RepositoryAssignment, *, endpoint_normalized: str
) -> RepositoryConnectionAssignment:
    identity = assignment.identity
    return RepositoryConnectionAssignment(
        connection_id=assignment.connection_id,
        endpoint_normalized=endpoint_normalized,
        repo_key=_repo_key_for(identity),
        provider_repo_id=(identity.provider_repo_id or None),
        canonical_remote=(identity.canonical_remote or None),
        display_name=identity.display_name,
        operations=list(assignment.operations),
        revision=assignment.revision,
        verified=assignment.verified,
    )


class RepositoryConnectionService:
    """Transactional writer; one instance per AsyncSession."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- internal helpers -------------------------------------------------

    async def _get_record(self, connection_id: str) -> RepositoryConnectionRecord | None:
        result = await self._session.execute(
            select(RepositoryConnectionRecord).where(
                RepositoryConnectionRecord.connection_id == connection_id
            )
        )
        return result.scalar_one_or_none()

    async def _audit(
        self,
        *,
        request_id: str,
        actor_ref: str,
        action: str,
        connection_id: str,
        scope_type: str,
        scope_ref: str | None,
        policy_revision: int | None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self._session.add(
            RepositoryConnectionAuditEvent(
                request_id=request_id.strip(),
                actor_ref=actor_ref.strip(),
                action=action,
                connection_id=connection_id,
                scope_type=scope_type,
                scope_ref=scope_ref,
                policy_revision=policy_revision,
                detail_json=dict(detail or {}),
            )
        )

    async def _replayed_action(
        self, *, request_id: str, action: str, connection_id: str
    ) -> bool:
        """Detect a stable-request-identity retry already recorded in this DB."""

        if not request_id.strip():
            raise RepositoryRouteError(
                REPOSITORY_SETUP_REQUIRED, "stable request identity required"
            )
        existing = (
            await self._session.execute(
                select(RepositoryConnectionAuditEvent).where(
                    RepositoryConnectionAuditEvent.request_id == request_id.strip(),
                    RepositoryConnectionAuditEvent.action == action,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            return False
        # No hidden suffix or fallback creation: a retry reuses the exact
        # request identity; reuse of one identity for a different connection
        # is a conflict, never a new suffixed row.
        if existing.connection_id != connection_id:
            raise RepositoryRouteError(
                REPOSITORY_ROUTE_CONFLICT, "request identity already used"
            )
        return True

    def _check_use(
        self,
        *,
        record: RepositoryConnectionRecord,
        principal_ref: str,
        principal_scope: tuple[str, str | None],
        action: str,
    ) -> RepositoryConnection:
        connection = _record_to_connection(record)
        authorize_connection_use(
            principal_ref=principal_ref,
            principal_scope=principal_scope,
            connection=connection,
            action=action,
        )
        return connection

    # -- connections ------------------------------------------------------

    async def create_connection(
        self,
        connection: RepositoryConnection,
        *,
        actor_ref: str,
        request_id: str,
        principal_ref: str,
        principal_scope: tuple[str, str | None],
    ) -> RepositoryConnection:
        """Create one scoped connection with its audit record, atomically."""

        validate_scoped_connection_for_write(connection)
        scope_t, scope_n = normalize_scope(
            connection.ownership.scope_type,  # type: ignore[union-attr]
            connection.ownership.scope_ref,  # type: ignore[union-attr]
        )
        if await self._replayed_action(
            request_id=request_id, action="connection.create",
            connection_id=connection.id,
        ):
            existing = await self._get_record(connection.id)
            if existing is None:
                raise RepositoryRouteError(
                    REPOSITORY_ROUTE_CONFLICT, "request identity already used"
                )
            return _record_to_connection(existing)
        if connection.ownership is not None and (
            principal_ref.strip() != connection.ownership.owner_ref.strip()
            and principal_scope[0] != "system"
        ):
            raise RepositoryRouteError(REPOSITORY_DENIED, "creation not admitted")
        existing = await self._get_record(connection.id)
        if existing is not None:
            if existing.tombstone:
                raise RepositoryRouteError(
                    REPOSITORY_ID_REUSE, "connection id was deleted and cannot be reused"
                )
            raise RepositoryRouteError(
                REPOSITORY_ROUTE_CONFLICT, "connection id already exists"
            )
        endpoint_normalized = normalize_endpoint(connection.endpoint_ref)
        record = RepositoryConnectionRecord(
            connection_id=connection.id,
            display_name=connection.display_name,
            provider=connection.provider,
            hosting_service=connection.hosting_service or "",
            endpoint_normalized=endpoint_normalized,
            endpoint_ref=connection.endpoint_ref,
            trust_bundle_ref=connection.trust_bundle_ref,
            allowed_operations=list(connection.allowed_operations),
            client_policy=connection.client_policy.model_dump(
                by_alias=True, mode="json"
            ),
            credential_config=_credential_config(connection),
            lifecycle=connection.lifecycle,
            policy_revision=connection.policy_revision,
            credential_revision=connection.credential_revision,
            owner_ref=connection.ownership.owner_ref.strip(),  # type: ignore[union-attr]
            scope_type=scope_t,
            scope_ref=scope_n,
            allowed_principal_refs=list(
                connection.ownership.allowed_principal_refs  # type: ignore[union-attr]
            ),
        )
        self._session.add(record)
        await self._audit(
            request_id=request_id,
            actor_ref=actor_ref,
            action="connection.create",
            connection_id=connection.id,
            scope_type=scope_t,
            scope_ref=scope_n,
            policy_revision=connection.policy_revision,
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise RepositoryConnectionConflict(
                REPOSITORY_ROUTE_CONFLICT, "connection creation conflict"
            ) from exc
        return connection

    async def update_connection(
        self,
        connection: RepositoryConnection,
        *,
        actor_ref: str,
        request_id: str,
        expected_policy_revision: int | None,
        principal_ref: str,
        principal_scope: tuple[str, str | None],
        explicit_endpoint_revision: bool = False,
    ) -> RepositoryConnection:
        """Replace connection policy transactionally with revision compare."""

        validate_scoped_connection_for_write(connection)
        if await self._replayed_action(
            request_id=request_id, action="connection.update",
            connection_id=connection.id,
        ):
            record = await self._get_record(connection.id)
            if record is None:
                raise RepositoryRouteError(
                    REPOSITORY_ROUTE_CONFLICT, "request identity already used"
                )
            return _record_to_connection(record)
        record = await self._get_record(connection.id)
        if record is None or record.tombstone:
            raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "connection not found")
        self._check_use(
            record=record,
            principal_ref=principal_ref,
            principal_scope=principal_scope,
            action="edit",
        )
        if expected_policy_revision is not None and (
            record.policy_revision != expected_policy_revision
        ):
            raise RepositoryRouteError(
                REPOSITORY_POLICY_CONFLICT, "stale policy revision"
            )
        validate_endpoint_retarget(
            current_endpoint=record.endpoint_normalized,
            proposed_endpoint=connection.endpoint_ref,
            explicit_revision_path=explicit_endpoint_revision
            or record.endpoint_normalized == normalize_endpoint(connection.endpoint_ref),
        )
        if normalize_endpoint(connection.endpoint_ref) != record.endpoint_normalized and (
            connection.credential_revision == record.credential_revision
        ):
            raise RepositoryRouteError(
                REPOSITORY_POLICY_CONFLICT,
                "endpoint change requires a credential revision",
            )
        endpoint_normalized = normalize_endpoint(connection.endpoint_ref)
        record.display_name = connection.display_name
        record.provider = connection.provider
        record.hosting_service = connection.hosting_service or ""
        record.endpoint_normalized = endpoint_normalized
        record.endpoint_ref = connection.endpoint_ref
        record.trust_bundle_ref = connection.trust_bundle_ref
        record.allowed_operations = list(connection.allowed_operations)
        record.client_policy = connection.client_policy.model_dump(
            by_alias=True, mode="json"
        )
        record.credential_config = _credential_config(connection)
        record.lifecycle = connection.lifecycle
        record.policy_revision = record.policy_revision + 1
        record.credential_revision = connection.credential_revision
        if connection.ownership is not None:
            scope_t, scope_n = normalize_scope(
                connection.ownership.scope_type, connection.ownership.scope_ref
            )
            record.owner_ref = connection.ownership.owner_ref.strip()
            record.scope_type = scope_t
            record.scope_ref = scope_n
            record.allowed_principal_refs = list(
                connection.ownership.allowed_principal_refs
            )
        await self._audit(
            request_id=request_id,
            actor_ref=actor_ref,
            action="connection.update",
            connection_id=connection.id,
            scope_type=record.scope_type,
            scope_ref=record.scope_ref,
            policy_revision=record.policy_revision,
            detail={"expected_revision": expected_policy_revision},
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise RepositoryConnectionConflict(
                REPOSITORY_ROUTE_CONFLICT, "connection update conflict"
            ) from exc
        await self._session.refresh(record)
        return _record_to_connection(record)

    async def disable_connection(
        self,
        connection_id: str,
        *,
        actor_ref: str,
        request_id: str,
        principal_ref: str,
        principal_scope: tuple[str, str | None],
    ) -> RepositoryConnection:
        """Disable without breaking active cleanup (bindings stay, use denied)."""

        if await self._replayed_action(
            request_id=request_id, action="connection.disable",
            connection_id=connection_id,
        ):
            record = await self._get_record(connection_id)
            if record is None:
                raise RepositoryRouteError(
                    REPOSITORY_ROUTE_CONFLICT, "request identity already used"
                )
            return _record_to_connection(record)
        record = await self._get_record(connection_id)
        if record is None or record.tombstone:
            raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "connection not found")
        self._check_use(
            record=record,
            principal_ref=principal_ref,
            principal_scope=principal_scope,
            action="disable",
        )
        record.lifecycle = "disabled"
        record.policy_revision = record.policy_revision + 1
        await self._audit(
            request_id=request_id,
            actor_ref=actor_ref,
            action="connection.disable",
            connection_id=connection_id,
            scope_type=record.scope_type,
            scope_ref=record.scope_ref,
            policy_revision=record.policy_revision,
        )
        await self._session.commit()
        await self._session.refresh(record)
        return _record_to_connection(record)

    async def delete_connection(
        self,
        connection_id: str,
        *,
        actor_ref: str,
        request_id: str,
        principal_ref: str,
        principal_scope: tuple[str, str | None],
        has_active_bindings: Callable[[str], bool] | None = None,
    ) -> None:
        """Delete only with no active bindings; tombstone blocks ID reuse."""

        if await self._replayed_action(
            request_id=request_id, action="connection.delete",
            connection_id=connection_id,
        ):
            return
        record = await self._get_record(connection_id)
        if record is None or record.tombstone:
            raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "connection not found")
        self._check_use(
            record=record,
            principal_ref=principal_ref,
            principal_scope=principal_scope,
            action="delete",
        )
        if has_active_bindings is not None and has_active_bindings(connection_id):
            raise RepositoryRouteError(
                REPOSITORY_ROUTE_CONFLICT, "active bindings block deletion"
            )
        remaining = (
            await self._session.execute(
                select(RepositoryConnectionAssignment).where(
                    RepositoryConnectionAssignment.connection_id == connection_id
                )
            )
        ).scalars().all()
        if remaining:
            raise RepositoryRouteError(
                REPOSITORY_ROUTE_CONFLICT, "assignments block deletion"
            )
        defaults = (
            await self._session.execute(
                select(RepositoryRouteDefault).where(
                    RepositoryRouteDefault.connection_id == connection_id
                )
            )
        ).scalars().all()
        if defaults:
            raise RepositoryRouteError(
                REPOSITORY_ROUTE_CONFLICT, "route defaults block deletion"
            )
        record.lifecycle = "deleted"
        record.tombstone = True
        record.policy_revision = record.policy_revision + 1
        await self._audit(
            request_id=request_id,
            actor_ref=actor_ref,
            action="connection.delete",
            connection_id=connection_id,
            scope_type=record.scope_type,
            scope_ref=record.scope_ref,
            policy_revision=record.policy_revision,
        )
        await self._session.commit()

    # -- assignments ------------------------------------------------------

    async def set_assignment(
        self,
        assignment: RepositoryAssignment,
        *,
        actor_ref: str,
        request_id: str,
        principal_ref: str,
        principal_scope: tuple[str, str | None],
    ) -> RepositoryAssignment:
        """Create or replace one assignment atomically with audit."""

        if await self._replayed_action(
            request_id=request_id, action="assignment.set",
            connection_id=assignment.connection_id,
        ):
            return assignment
        record = await self._get_record(assignment.connection_id)
        if record is None or record.tombstone:
            raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "connection not found")
        self._check_use(
            record=record,
            principal_ref=principal_ref,
            principal_scope=principal_scope,
            action="attach",
        )
        if record.lifecycle != "active":
            raise RepositoryRouteError(REPOSITORY_DENIED, "connection is not active")
        endpoint_normalized = normalize_endpoint(assignment.identity.endpoint)
        if endpoint_normalized != record.endpoint_normalized:
            raise RepositoryRouteError(
                REPOSITORY_DENIED, "assignment endpoint must match the connection"
            )
        if assignment.broad_rule:
            raise RepositoryRouteError(
                REPOSITORY_SETUP_REQUIRED, "broad rules need explicit grant"
            )
        if not assignment.verified:
            raise RepositoryRouteError(
                REPOSITORY_SETUP_REQUIRED, "unverified assignments grant nothing"
            )
        existing = (
            await self._session.execute(
                select(RepositoryConnectionAssignment).where(
                    RepositoryConnectionAssignment.connection_id
                    == assignment.connection_id,
                    RepositoryConnectionAssignment.endpoint_normalized
                    == endpoint_normalized,
                    RepositoryConnectionAssignment.repo_key
                    == _repo_key_for(assignment.identity),
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            self._session.add(
                _assignment_to_row(assignment, endpoint_normalized=endpoint_normalized)
            )
        else:
            existing.provider_repo_id = assignment.identity.provider_repo_id
            existing.canonical_remote = assignment.identity.canonical_remote
            existing.display_name = assignment.identity.display_name
            existing.operations = list(assignment.operations)
            existing.revision = existing.revision + 1
            existing.verified = assignment.verified
        await self._audit(
            request_id=request_id,
            actor_ref=actor_ref,
            action="assignment.set",
            connection_id=assignment.connection_id,
            scope_type=record.scope_type,
            scope_ref=record.scope_ref,
            policy_revision=record.policy_revision,
            detail={"repo_key": _repo_key_for(assignment.identity)},
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise RepositoryConnectionConflict(
                REPOSITORY_ROUTE_CONFLICT, "assignment conflict"
            ) from exc
        return assignment

    async def remove_assignment(
        self,
        *,
        connection_id: str,
        identity: RepositoryIdentity,
        actor_ref: str,
        request_id: str,
        principal_ref: str,
        principal_scope: tuple[str, str | None],
    ) -> None:
        """Remove one assignment; never leaves a dangling default binding."""

        if await self._replayed_action(
            request_id=request_id, action="assignment.remove",
            connection_id=connection_id,
        ):
            return
        record = await self._get_record(connection_id)
        if record is None or record.tombstone:
            raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "connection not found")
        self._check_use(
            record=record,
            principal_ref=principal_ref,
            principal_scope=principal_scope,
            action="detach",
        )
        endpoint_normalized = normalize_endpoint(identity.endpoint)
        repo_key = _repo_key_for(identity)
        await self._session.execute(
            delete(RepositoryConnectionAssignment).where(
                RepositoryConnectionAssignment.connection_id == connection_id,
                RepositoryConnectionAssignment.endpoint_normalized
                == endpoint_normalized,
                RepositoryConnectionAssignment.repo_key == repo_key,
            )
        )
        # A removed assignment must not leave a dangling default binding.
        await self._session.execute(
            delete(RepositoryRouteDefault).where(
                RepositoryRouteDefault.connection_id == connection_id,
                RepositoryRouteDefault.endpoint_normalized == endpoint_normalized,
                RepositoryRouteDefault.repo_key == repo_key,
            )
        )
        await self._audit(
            request_id=request_id,
            actor_ref=actor_ref,
            action="assignment.remove",
            connection_id=connection_id,
            scope_type=record.scope_type,
            scope_ref=record.scope_ref,
            policy_revision=record.policy_revision,
            detail={"repo_key": repo_key},
        )
        await self._session.commit()

    # -- route defaults ---------------------------------------------------

    async def set_route_default(
        self,
        *,
        scope_type: str,
        scope_ref: str | None,
        identity: RepositoryIdentity,
        capability_bundle: Sequence[str],
        connection_id: str,
        actor_ref: str,
        request_id: str,
        principal_ref: str,
        principal_scope: tuple[str, str | None],
    ) -> None:
        """Replace the default for one route key transactionally."""

        if await self._replayed_action(
            request_id=request_id, action="route_default.set",
            connection_id=connection_id,
        ):
            return
        bundle = ",".join(
            sorted(
                {
                    str(op).strip().lower()
                    for op in capability_bundle
                    if str(op).strip()
                }
            )
        )
        if not bundle:
            raise RepositoryRouteError(
                REPOSITORY_SETUP_REQUIRED, "empty capability bundle"
            )
        norm_scope_t, norm_scope_n = normalize_scope(scope_type, scope_ref)
        record = await self._get_record(connection_id)
        if record is None or record.tombstone:
            raise RepositoryRouteError(REPOSITORY_SETUP_REQUIRED, "connection not found")
        self._check_use(
            record=record,
            principal_ref=principal_ref,
            principal_scope=principal_scope,
            action="edit",
        )
        if record.lifecycle != "active":
            raise RepositoryRouteError(REPOSITORY_DENIED, "connection is not active")
        endpoint_normalized = normalize_endpoint(identity.endpoint)
        repo_key = _repo_key_for(identity)
        assignment = (
            await self._session.execute(
                select(RepositoryConnectionAssignment).where(
                    RepositoryConnectionAssignment.connection_id == connection_id,
                    RepositoryConnectionAssignment.endpoint_normalized
                    == endpoint_normalized,
                    RepositoryConnectionAssignment.repo_key == repo_key,
                )
            )
        ).scalar_one_or_none()
        if assignment is None or not assignment.verified:
            raise RepositoryRouteError(
                REPOSITORY_SETUP_REQUIRED, "default needs a verified assignment"
            )
        existing = (
            await self._session.execute(
                select(RepositoryRouteDefault).where(
                    RepositoryRouteDefault.scope_type == norm_scope_t,
                    RepositoryRouteDefault.scope_ref == norm_scope_n,
                    RepositoryRouteDefault.endpoint_normalized == endpoint_normalized,
                    RepositoryRouteDefault.repo_key == repo_key,
                    RepositoryRouteDefault.capability_bundle == bundle,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            self._session.add(
                RepositoryRouteDefault(
                    scope_type=norm_scope_t,
                    scope_ref=norm_scope_n,
                    endpoint_normalized=endpoint_normalized,
                    repo_key=repo_key,
                    capability_bundle=bundle,
                    connection_id=connection_id,
                    policy_revision=record.policy_revision,
                    request_id=request_id.strip(),
                )
            )
        else:
            existing.connection_id = connection_id
            existing.policy_revision = record.policy_revision
            existing.request_id = request_id.strip()
        await self._audit(
            request_id=request_id,
            actor_ref=actor_ref,
            action="route_default.set",
            connection_id=connection_id,
            scope_type=norm_scope_t,
            scope_ref=norm_scope_n,
            policy_revision=record.policy_revision,
            detail={"repo_key": repo_key, "bundle": bundle},
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise RepositoryConnectionConflict(
                REPOSITORY_ROUTE_CONFLICT, "concurrent default change conflict"
            ) from exc

    # -- reads (no filesystem fallback) -----------------------------------

    async def list_admitted_routes(
        self,
        *,
        identity: RepositoryIdentity,
        principal_ref: str,
        principal_scope: tuple[str, str | None],
    ) -> list[tuple[RepositoryConnection, RepositoryAssignment]]:
        """Enumerate candidates only after authorization (no secret bodies)."""

        endpoint_normalized = normalize_endpoint(identity.endpoint)
        repo_key = _repo_key_for(identity)
        rows = (
            await self._session.execute(
                select(RepositoryConnectionAssignment).where(
                    RepositoryConnectionAssignment.endpoint_normalized
                    == endpoint_normalized,
                    RepositoryConnectionAssignment.repo_key == repo_key,
                )
            )
        ).scalars().all()
        admitted: list[tuple[RepositoryConnection, RepositoryAssignment]] = []
        for row in rows:
            record = await self._get_record(row.connection_id)
            if record is None or record.tombstone:
                continue
            try:
                connection = self._check_use(
                    record=record,
                    principal_ref=principal_ref,
                    principal_scope=principal_scope,
                    action="use",
                )
            except RepositoryRouteError:
                # Denied candidates are filtered without metadata leakage.
                continue
            admitted.append(
                (
                    connection,
                    RepositoryAssignment(
                        connectionId=row.connection_id,
                        identity=RepositoryIdentity(
                            endpoint=identity.endpoint,
                            providerRepoId=row.provider_repo_id,
                            canonicalRemote=row.canonical_remote,
                            displayName=row.display_name,
                        ),
                        operations=tuple(row.operations or ()),
                        revision=row.revision,
                        verified=row.verified,
                    ),
                )
            )
        return admitted

    async def export_snapshot_connections(
        self,
        *,
        principal_ref: str,
        principal_scope: tuple[str, str | None],
    ) -> list[RepositoryConnection]:
        """Export metadata-only connections the principal may discover."""

        rows = (
            await self._session.execute(select(RepositoryConnectionRecord))
        ).scalars().all()
        visible: list[RepositoryConnection] = []
        for record in rows:
            if record.tombstone:
                continue
            try:
                visible.append(
                    self._check_use(
                        record=record,
                        principal_ref=principal_ref,
                        principal_scope=principal_scope,
                        action="discover",
                    )
                )
            except RepositoryRouteError:
                continue
        return visible


__all__ = ["RepositoryConnectionConflict", "RepositoryConnectionService"]
