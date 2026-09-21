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
        provider_app = str(provider_installation.get("app_ref") or "").strip()
        provider_installation_ref = str(
            provider_installation.get("installation_ref") or ""
        ).strip()
        provider_account = str(provider_installation.get("account") or "").strip()
        provider_repos = [
            str(name).strip()
            for name in (provider_installation.get("repositories") or [])
            if str(name).strip()
        ]
        # The browser callback's installation_id is never trusted directly:
        # it must match the verified provider association.
        if (installation_ref or "").strip() != provider_installation_ref:
            raise _route_error(REPOSITORY_DENIED, "installation association mismatch")
        if provider_app != pending.expected_app_ref:
            raise _route_error(REPOSITORY_DENIED, "installation App mismatch")
        if not provider_installation_ref:
            raise _route_error(REPOSITORY_DENIED, "installation identity mismatch")
        if pending.expected_account and provider_account != pending.expected_account:
            raise _route_error(REPOSITORY_DENIED, "installation account is not permitted")
        if bool(provider_installation.get("suspended", False)):
            raise _route_error(REPOSITORY_DENIED, "installation is suspended")
        if pending.permitted_repositories and any(
            repo not in provider_repos for repo in pending.permitted_repositories
        ):
            raise _route_error(
                REPOSITORY_SETUP_REQUIRED, "installation repositories are not permitted"
            )
        pending.consumed = True
        return pending

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
        if str(provider_installation.get("app_ref") or "").strip() != (
            expected_app_ref or ""
        ).strip():
            raise _route_error(REPOSITORY_DENIED, "installation App mismatch")
        if (expected_account or "").strip() and str(
            provider_installation.get("account") or ""
        ).strip() != expected_account.strip():
            raise _route_error(REPOSITORY_DENIED, "installation account is not permitted")
        if bool(provider_installation.get("suspended", False)):
            raise _route_error(REPOSITORY_DENIED, "installation is suspended")
        provider_repos = {
            str(name).strip()
            for name in (provider_installation.get("repositories") or [])
            if str(name).strip()
        }
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
        setup_service.verify_setup_callback(
            state=state,
            installation_ref=installation_ref,
            provider_installation=provider_installation,
            caller_principal=caller_principal,
            caller_scope=caller_scope,
            destination_connection_id=destination_connection_id or connection_id,
        )
    # Converge ambiguous retries before touching the existing writer.
    stable_connection_id = reconcile_setup_save(
        request_id=request_id,
        connection_id=connection_id,
        existing_by_request=dict(existing_by_request or {}),
    )
    app_ref = str(provider_installation.get("app_ref") or expected_app_ref or "").strip()
    resolved_installation = str(
        provider_installation.get("installation_ref") or installation_ref or ""
    ).strip()
    account = str(provider_installation.get("account") or expected_account or "").strip()
    credential: dict[str, Any] = {
        "source": "github_app",
        "appRef": app_ref,
        "installationRef": resolved_installation,
    }
    if (key_ref or "").strip():
        credential["keyRef"] = str(key_ref).strip()
    if account:
        credential["account"] = account
    scope = principal_scope or ("system", None)
    connection = RepositoryConnection.model_validate(
        {
            "schemaVersion": "moonmind.repository-connection.v1",
            "id": stable_connection_id,
            "provider": "git",
            "displayName": display_name,
            "endpointRef": endpoint_ref,
            "allowedOperations": list(allowed_operations),
            "allowedRepositoryIds": [
                str(name).strip() for name in permitted_repositories if str(name).strip()
            ],
            "clientPolicy": {
                "pinnedVersion": "2.46.0",
                "toolBundleRef": "tool-bundle:git-2.46",
                "executableSha256": "sha256:git",
            },
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
    return result


__all__ = [
    "GitHubAppSetupService",
    "SetupPending",
    "reconcile_setup_save",
    "save_verified_app_connection",
]
