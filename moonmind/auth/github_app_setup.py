"""Trusted-boundary GitHub App setup verification (#4022).

One supported enrollment path through the existing Source Control setup,
``RepositoryConnection`` lifecycle, and Secrets System. The public/browser
setup callback never trusts its ``installation_id`` directly: the trusted
server boundary verifies the installation against the configured App,
account, and permitted repositories using GitHub's verified
user-authorization/installation-association flow (injected provider
record), binds single-use state to the initiating admitted interaction,
and rejects replay or destination substitution.

Save retries reconcile through the existing operation identity
(``ConnectionChangeRequest.request_id``); shared installations are never
uninstalled as cleanup. Signing keys live only in managed secret
references. No User table, tenant hierarchy, member roles, or App
marketplace is introduced.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


def _route_error(code: str, message: str) -> ValueError:
    from moonmind.workflows.executions.repository_contract import RepositoryRouteError

    return RepositoryRouteError(code, message)


@dataclass(slots=True)
class SetupPending:
    state: str
    request_id: str
    connection_id: str
    principal_ref: str
    principal_scope: tuple[str, str | None]
    expected_app_ref: str
    expected_account: str
    permitted_repositories: tuple[str, ...]
    created_at: float = field(default_factory=time.monotonic)
    consumed: bool = False


def _sign_state(server_secret: str, raw: str) -> str:
    digest = hmac.new(
        server_secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    return f"{raw}.{digest}"


def _unsign_state(server_secret: str, state: str) -> str | None:
    if "." not in state:
        return None
    raw, _, signature = state.rpartition(".")
    if not raw or not signature:
        return None
    expected = hmac.new(
        server_secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    if not hmac.compare_digest(expected, signature):
        return None
    return raw


class GitHubAppSetupService:
    """Single-use setup-state issuer and trusted-boundary verifier."""

    def __init__(self, *, server_secret: str, state_ttl_seconds: float = 600.0) -> None:
        if not (server_secret or "").strip():
            raise ValueError("setup service requires a server-held secret")
        self._secret = server_secret
        self._ttl = max(60.0, float(state_ttl_seconds))
        self._pending: dict[str, SetupPending] = {}

    def begin_setup(
        self,
        *,
        request_id: str,
        connection_id: str,
        principal_ref: str,
        principal_scope: tuple[str, str | None],
        expected_app_ref: str,
        expected_account: str,
        permitted_repositories: Sequence[str],
    ) -> SetupPending:
        """Bind single-use state to the initiating admitted interaction."""

        from moonmind.workflows.executions.repository_contract import (
            REPOSITORY_SETUP_REQUIRED,
        )

        if not (request_id or "").strip() or not (connection_id or "").strip():
            raise _route_error(REPOSITORY_SETUP_REQUIRED, "setup needs request identity")
        if not (principal_ref or "").strip():
            raise _route_error(REPOSITORY_SETUP_REQUIRED, "setup needs an admitted principal")
        if not (expected_app_ref or "").strip():
            raise _route_error(REPOSITORY_SETUP_REQUIRED, "setup needs a configured App")
        raw = f"{request_id.strip()}:{connection_id.strip()}:{secrets.token_hex(16)}"
        state = _sign_state(self._secret, raw)
        pending = SetupPending(
            state=state,
            request_id=request_id.strip(),
            connection_id=connection_id.strip(),
            principal_ref=principal_ref.strip(),
            principal_scope=(principal_scope[0], principal_scope[1]),
            expected_app_ref=expected_app_ref.strip(),
            expected_account=(expected_account or "").strip(),
            permitted_repositories=tuple(
                str(name).strip() for name in permitted_repositories if str(name).strip()
            ),
        )
        self._pending[raw] = pending
        return pending

    def verify_setup_callback(
        self,
        *,
        state: str,
        installation_ref: str,
        provider_installation: Mapping[str, Any],
        caller_principal: str,
        caller_scope: tuple[str, str | None],
        destination_connection_id: str,
        mark_consumed: bool = True,
    ) -> SetupPending:
        """Verify one setup callback at the trusted server boundary."""

        from moonmind.workflows.executions.repository_contract import (
            REPOSITORY_DENIED,
            REPOSITORY_SETUP_REQUIRED,
        )

        raw = _unsign_state(self._secret, state or "")
        if raw is None:
            raise _route_error(REPOSITORY_DENIED, "setup state is forged or unknown")
        pending = self._pending.get(raw)
        if pending is None:
            raise _route_error(REPOSITORY_DENIED, "setup state is forged or unknown")
        if pending.consumed:
            raise _route_error(REPOSITORY_DENIED, "setup state was already used")
        if time.monotonic() - pending.created_at > self._ttl:
            raise _route_error(REPOSITORY_DENIED, "setup state expired")
        self._check_callback_binding(
            pending,
            installation_ref=installation_ref,
            provider_installation=provider_installation,
            caller_principal=caller_principal,
            caller_scope=caller_scope,
            destination_connection_id=destination_connection_id,
        )
        if mark_consumed:
            pending.consumed = True
        return pending

    def consume_setup_state(self, state: str) -> SetupPending:
        """Mark one verified setup state consumed after its durable outcome.

        Consumption is deferred until the connection save commits so a
        failed save (or a lost response after a committed save) can retry
        on the same state while the writer's ``request_id`` identity
        converges the retry. Replays after consumption still fail closed.
        """

        from moonmind.workflows.executions.repository_contract import (
            REPOSITORY_DENIED,
        )

        raw = _unsign_state(self._secret, state or "")
        if raw is None:
            raise _route_error(REPOSITORY_DENIED, "setup state is forged or unknown")
        pending = self._pending.get(raw)
        if pending is None:
            raise _route_error(REPOSITORY_DENIED, "setup state is forged or unknown")
        if pending.consumed:
            raise _route_error(REPOSITORY_DENIED, "setup state was already used")
        pending.consumed = True
        return pending

    def peek_setup_state(
        self,
        *,
        state: str,
        installation_ref: str,
        provider_installation: Mapping[str, Any],
        caller_principal: str,
        caller_scope: tuple[str, str | None],
        destination_connection_id: str,
    ) -> SetupPending:
        """Re-validate a setup state without mutating consumption.

        Supports the already-committed retry: when the save succeeded but
        its response was lost, the state is consumed yet the connection
        exists under the request identity. The caller re-checks every
        binding and, only on success, reconciles to the recorded
        connection instead of saving again.
        """

        from moonmind.workflows.executions.repository_contract import (
            REPOSITORY_DENIED,
        )

        raw = _unsign_state(self._secret, state or "")
        if raw is None:
            raise _route_error(REPOSITORY_DENIED, "setup state is forged or unknown")
        pending = self._pending.get(raw)
        if pending is None:
            raise _route_error(REPOSITORY_DENIED, "setup state is forged or unknown")
        self._check_callback_binding(
            pending,
            installation_ref=installation_ref,
            provider_installation=provider_installation,
            caller_principal=caller_principal,
            caller_scope=caller_scope,
            destination_connection_id=destination_connection_id,
        )
        return pending

    def _check_callback_binding(
        self,
        pending: SetupPending,
        *,
        installation_ref: str,
        provider_installation: Mapping[str, Any],
        caller_principal: str,
        caller_scope: tuple[str, str | None],
        destination_connection_id: str,
    ) -> None:
        """Enforce every state binding shared by verify and peek."""

        from moonmind.workflows.executions.repository_contract import (
            REPOSITORY_DENIED,
            REPOSITORY_SETUP_REQUIRED,
        )

        # Single-use: the state stays valid across failed verifications so a
        # legitimate retry can succeed, but the first successful verification
        # consumes it and any replay after that fails closed.
        if (caller_principal or "").strip() != pending.principal_ref:
            raise _route_error(REPOSITORY_DENIED, "setup caller is not admitted")
        caller_normalized = ((caller_scope[0] or "").strip(), caller_scope[1])
        pending_scope = (pending.principal_scope[0], pending.principal_scope[1])
        if caller_normalized != pending_scope:
            raise _route_error(REPOSITORY_DENIED, "setup caller scope is not admitted")
        if (destination_connection_id or "").strip() != pending.connection_id:
            raise _route_error(REPOSITORY_DENIED, "setup destination substitution rejected")
        if not isinstance(provider_installation, Mapping):
            raise _route_error(REPOSITORY_DENIED, "installation verification failed")
        # Production provider records arrive raw (numeric id/app_id,
        # account object, suspended_at); the setup boundary and tests use
        # the normalized shape. Normalize once so both bind the same way.
        from moonmind.auth.github_app import _numeric_key, normalize_installation_record

        normalized = normalize_installation_record(provider_installation)
        provider_app = normalized.app_key
        provider_installation_ref = normalized.installation_key
        provider_account = normalized.account
        provider_repos = list(normalized.repositories)
        # The browser callback's installation_id is never trusted directly:
        # it must match the verified provider association.
        if _numeric_key(installation_ref) != provider_installation_ref:
            raise _route_error(REPOSITORY_DENIED, "installation association mismatch")
        if provider_app != _numeric_key(pending.expected_app_ref):
            raise _route_error(REPOSITORY_DENIED, "installation App mismatch")
        if not provider_installation_ref:
            raise _route_error(REPOSITORY_DENIED, "installation identity mismatch")
        if pending.expected_account and provider_account != pending.expected_account:
            raise _route_error(REPOSITORY_DENIED, "installation account is not permitted")
        if normalized.suspended:
            raise _route_error(REPOSITORY_DENIED, "installation is suspended")
        if pending.permitted_repositories and any(
            repo not in provider_repos for repo in pending.permitted_repositories
        ):
            raise _route_error(
                REPOSITORY_SETUP_REQUIRED, "installation repositories are not permitted"
            )

    def verify_operator_import(
        self,
        *,
        provider_installation: Mapping[str, Any],
        expected_app_ref: str,
        expected_account: str,
        permitted_repositories: Sequence[str],
    ) -> None:
        """Explicitly authorized operator-import path (no public self-service).

        Uses the verified configured installation/account scope: the provider
        record (not a browser parameter) is checked against the configured
        scope. App inspection alone never authorizes attachment; callers must
        have passed the existing connection admission before invoking this.
        """

        from moonmind.workflows.executions.repository_contract import REPOSITORY_DENIED

        if not isinstance(provider_installation, Mapping):
            raise _route_error(REPOSITORY_DENIED, "installation verification failed")
        from moonmind.auth.github_app import _numeric_key, normalize_installation_record

        normalized = normalize_installation_record(provider_installation)
        if normalized.app_key != _numeric_key(expected_app_ref):
            raise _route_error(REPOSITORY_DENIED, "installation App mismatch")
        if (expected_account or "").strip() and (
            normalized.account != expected_account.strip()
        ):
            raise _route_error(REPOSITORY_DENIED, "installation account is not permitted")
        if normalized.suspended:
            raise _route_error(REPOSITORY_DENIED, "installation is suspended")
        provider_repos = set(normalized.repositories)
        for repo in permitted_repositories:
            if str(repo).strip() and str(repo).strip() not in provider_repos:
                raise _route_error(
                    REPOSITORY_DENIED, "installation repositories are not permitted"
                )

    def uninstall_installation_as_cleanup(self, installation_ref: str) -> None:
        """Refuse to uninstall a shared installation as cleanup (never called)."""

        raise ValueError(
            f"refusing to uninstall shared installation {installation_ref!r} as cleanup"
        )


def reconcile_setup_save(
    *,
    request_id: str,
    connection_id: str,
    existing_by_request: Mapping[str, str],
) -> str:
    """Converge ambiguous setup-save retries on one connection.

    Mirrors the existing ``ConnectionChangeRequest.request_id`` operation
    identity: the first recorded connection for a request identity wins and
    retries return it instead of creating a second connection.
    """

    from moonmind.workflows.executions.repository_contract import REPOSITORY_SETUP_REQUIRED

    if not (request_id or "").strip() or not (connection_id or "").strip():
        raise _route_error(REPOSITORY_SETUP_REQUIRED, "stable request identity required")
    existing = existing_by_request.get(request_id.strip())
    if existing:
        return existing
    return connection_id.strip()


async def save_verified_app_connection(
    *,
    setup_service: GitHubAppSetupService,
    connection_service: Any,
    request_id: str,
    connection_id: str,
    provider_installation: Mapping[str, Any],
    expected_app_ref: str,
    expected_account: str = "",
    permitted_repositories: Sequence[str] = (),
    existing_by_request: Mapping[str, str] | None = None,
    # Setup-callback path (single-use state bound to the admitted interaction).
    state: str | None = None,
    installation_ref: str | None = None,
    caller_principal: str = "",
    caller_scope: tuple[str, str | None] | None = None,
    destination_connection_id: str = "",
    # Explicitly authorized operator-import path (no public self-service).
    operator_import: bool = False,
    # Connection metadata persisted through the existing writer.
    display_name: str = "GitHub App connection",
    endpoint_ref: str = "https://github.com",
    allowed_operations: Sequence[str] = ("read",),
    owner_ref: str = "",
    principal_ref: str = "",
    principal_scope: tuple[str, str | None] | None = None,
    actor_ref: str = "",
    key_ref: str | None = None,
) -> Any:
    """Verify one enrollment and persist it through the existing writer.

    Mounts :class:`GitHubAppSetupService` at the existing Source Control
    setup boundary: the ``RepositoryConnectionService`` (``api_service``
    database/service writer) remains the single writable authority for
    connections. ``ConnectionChangeRequest.request_id`` is the stable
    operation identity: ambiguous save retries converge on the first
    recorded connection via :func:`reconcile_setup_save` instead of
    creating a second connection. Shared installations are never
    uninstalled as cleanup (there is deliberately no uninstall call here;
    :meth:`GitHubAppSetupService.uninstall_installation_as_cleanup`
    refuses if ever invoked).
    """

    from moonmind.workflows.executions.repository_contract import (
        REPOSITORY_SETUP_REQUIRED,
        RepositoryConnection,
    )

    if not (request_id or "").strip() or not (connection_id or "").strip():
        raise _route_error(REPOSITORY_SETUP_REQUIRED, "stable request identity required")
    if not isinstance(provider_installation, Mapping):
        raise _route_error(REPOSITORY_SETUP_REQUIRED, "installation verification failed")
    pending: SetupPending | None = None
    if operator_import:
        setup_service.verify_operator_import(
            provider_installation=provider_installation,
            expected_app_ref=expected_app_ref,
            expected_account=expected_account,
            permitted_repositories=permitted_repositories,
        )
    else:
        if state is None or installation_ref is None or caller_scope is None:
            raise _route_error(REPOSITORY_SETUP_REQUIRED, "setup callback needs state")
        # Verify without consuming: the state is consumed only after the
        # durable save below, so a failed commit can retry on the same
        # state while the writer's request identity converges the retry.
        try:
            pending = setup_service.verify_setup_callback(
                state=state,
                installation_ref=installation_ref,
                provider_installation=provider_installation,
                caller_principal=caller_principal,
                caller_scope=caller_scope,
                destination_connection_id=destination_connection_id or connection_id,
                mark_consumed=False,
            )
        except ValueError as exc:
            if "already used" not in str(exc):
                raise
            # Already-committed retry (save succeeded, response lost): the
            # state is consumed but the connection exists under the request
            # identity. Re-validate every binding and reconcile to the
            # recorded connection instead of rejecting the retry as replay.
            replayed = setup_service.peek_setup_state(
                state=state,
                installation_ref=installation_ref,
                provider_installation=provider_installation,
                caller_principal=caller_principal,
                caller_scope=caller_scope,
                destination_connection_id=destination_connection_id or connection_id,
            )
            stable_id = reconcile_setup_save(
                request_id=replayed.request_id,
                connection_id=replayed.connection_id,
                existing_by_request=dict(existing_by_request or {}),
            )
            existing = await _read_recorded_connection(
                connection_service,
                connection_id=stable_id,
                principal_ref=replayed.principal_ref,
                principal_scope=replayed.principal_scope,
            )
            if existing is None:
                raise
            return existing
        # The verified pending record owns the enrollment intent: request
        # identity, destination, principal/scope, and repository bundle.
        # Callback arguments cannot widen or redirect it; only server-held
        # configuration (display, endpoint, operations, key ref) applies.
        request_id = pending.request_id
        connection_id = pending.connection_id
        permitted_repositories = pending.permitted_repositories
        principal_scope = pending.principal_scope
        principal_ref = pending.principal_ref
        caller_principal = pending.principal_ref
    # Converge ambiguous retries before touching the existing writer.
    stable_connection_id = reconcile_setup_save(
        request_id=request_id,
        connection_id=connection_id,
        existing_by_request=dict(existing_by_request or {}),
    )
    app_ref = str(provider_installation.get("app_ref") or expected_app_ref or "").strip()
    from moonmind.auth.github_app import normalize_installation_record

    normalized_record = normalize_installation_record(provider_installation)
    resolved_installation = (
        str(provider_installation.get("installation_ref") or "").strip()
        or normalized_record.installation_key
        or str(installation_ref or "").strip()
    )
    account = normalized_record.account or str(expected_account or "").strip()
    permitted = [
        str(name).strip() for name in permitted_repositories if str(name).strip()
    ]
    credential: dict[str, Any] = {
        "source": "github_app",
        "appRef": app_ref,
        "installationRef": resolved_installation,
    }
    if (key_ref or "").strip():
        credential["keyRef"] = str(key_ref).strip()
    if account:
        credential["account"] = account
    if permitted:
        # Persist the issuance restriction inside the credential metadata
        # so it survives a database reload (the record has no
        # allowedRepositoryIds column).
        credential["permittedRepositories"] = list(permitted)
    scope = principal_scope or ("system", None)
    connection = RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": stable_connection_id,
            "provider": "git",
            "displayName": display_name,
            "endpointRef": endpoint_ref,
            "allowedOperations": list(allowed_operations),
            "allowedRepositoryIds": list(permitted),
            "clientPolicy": _deployment_git_client_policy(),
            "credential": credential,
            "lifecycle": "active",
            "policyRevision": 1,
            "credentialRevision": 1,
            "ownership": {
                "ownerRef": (owner_ref or principal_ref or caller_principal).strip()
                or "owner:operator",
                "scopeType": scope[0],
                **({"scopeRef": scope[1]} if scope[1] is not None else {}),
                "allowedPrincipalRefs": [
                    (principal_ref or caller_principal).strip()
                ]
                if (principal_ref or caller_principal).strip()
                else [],
            },
            "hostingService": "github",
        }
    )
    # Existing writer: RepositoryConnectionService.create_connection with the
    # same request identity (replay returns the recorded row; a reused
    # identity for another connection is a conflict, never a suffix).
    result = connection_service.create_connection(
        connection,
        actor_ref=(actor_ref or principal_ref or caller_principal or "owner:operator"),
        request_id=request_id.strip(),
        principal_ref=(principal_ref or caller_principal).strip() or "principal:operator",
        principal_scope=scope,
    )
    if hasattr(result, "__await__"):
        result = await result
    if pending is not None and state is not None:
        setup_service.consume_setup_state(state)
    return result


async def _read_recorded_connection(
    connection_service: Any,
    *,
    connection_id: str,
    principal_ref: str,
    principal_scope: tuple[str, str | None],
) -> Any | None:
    """Return the recorded connection for an already-committed retry."""

    getter = getattr(connection_service, "get_connection", None)
    if callable(getter):
        result = getter(
            connection_id,
            principal_ref=principal_ref,
            principal_scope=principal_scope,
        )
        if hasattr(result, "__await__"):
            result = await result
        return result
    return None


def _deployment_git_client_policy() -> dict[str, Any]:
    """Pin the deployment's observed Git client instead of placeholders.

    Enrollment records the installer host's real ``git --version`` and
    executable digest so ``validate_connection_and_client`` admits the
    connection on normal runtimes instead of failing placeholder evidence.
    """

    from moonmind.workflows.temporal.runtime.launcher import (
        resolve_deployment_git_client_policy,
    )

    return resolve_deployment_git_client_policy().model_dump(
        by_alias=True, mode="json"
    )


__all__ = [
    "GitHubAppSetupService",
    "SetupPending",
    "reconcile_setup_save",
    "save_verified_app_connection",
]
