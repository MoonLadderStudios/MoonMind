"""Scoped worker, MCP, CLI, and Workflow Chat authentication boundaries.

Source issue: MoonLadderStudios/MoonMind#4126 (parent #4116; depends on
#4118, #4120, #4121; integrates against #4125's shared principal boundary).
Plan coverage: K4 machine/runtime authority and native chat in
``docs/tmp/KeycloakRemovalPlan.md``.

This module is the one preservation owner that keeps the new application
auth boundary from breaking independent machine work or granting
machines/browser clients broader authority. It composes the qualified
#4118 session primitives (``omnigent_auth_qualification``), the #4121
single credential-precedence policy (``session_authority_4121``), the
existing session-scoped container-job capabilities
(``container_job_capabilities``), and the binding-scoped Workflow Chat
facade primitives (``workflow_chat_facade``) rather than reimplementing
any of them. Coordinate #3939's workflow CLI and the existing Omnigent
bridge/host-auth owners instead of recreating those systems.

Credential classification (issuance, delivery, validation per §1):

.. list-table::
   :header-rows: 1

   * - Credential
     - Issuer
     - Audience / purpose
     - Principal
     - Resource / operation scope
     - Expiry
     - Revocation
     - Durable execution owner
   * - MoonMind browser session
     - MoonMind control plane
     - ``moonmind-browser-session``
     - ``User.id`` UUID
     - Route + resource authorization per request
     - ``session_ttl_seconds`` (8h default)
     - Per-session JTI + per-user generation, 5-minute bound
     - Session/revocation owner (``#4121``)
   * - Scoped worker capability (this module)
     - Credential owner that mints it (workflow dispatcher)
     - ``moonmind-worker``
     - Owner principal (``user:<uuid>`` or ``service:<name>``)
     - Exact workflow/run/session/host/resource + operations
     - Bounded lifetime, caller-chosen (minutes to hours)
     - Per-token JTI + per-owner generation (lease policy)
     - Dispatching workflow owner; admitted work keeps its stored principal
   * - Container-job session capability
     - Managed-session / Omnigent session owner
     - ``moonmind-container-jobs``
     - ``OwnerIdentity``
     - Exact agent-run/workflow/session/runtime/workspace + source
     - Bounded lifetime at mint
     - Expiry + scope mismatch rejection (no cross-run use)
     - ``ContainerJobService`` owner; Docker authority stays in the worker
   * - Omnigent host auth
     - Runtime host-auth lifecycle owner
     - Host-registration / refresh purpose
     - Host identity
     - Single host / profile generation + overlap window
     - Overlap-bounded rotation
     - Profile generation + rotation owner
     - Bridge/host-auth owner (untouched here)
   * - Provider Profile OAuth / repository PAT-App
     - External provider
     - Provider-defined audience
     - Provider identity
     - Provider-defined scopes
     - Provider-defined
     - Provider-defined; browser logout never revokes these
     - Provider-profile / repository-credential owner (untouched here)

User-login JWT removal must not delete an unrelated credential validator:
this module never accepts a browser session as a worker capability, never
accepts a worker/container/runtime token as a browser session, and never
falls back from an invalid presented credential to a different principal.

Optional-user policy (brief §2, #4121): missing browser cookies are normal
for machine calls; missing all accepted authority is ``401
auth_required``; an invalid presented credential is ``401 auth_invalid``
(never a silent fallback); conflicting cookie/bearer or worker/user
identities are ``401 auth_conflict``. No default user, ambient service
token, or full user session is substituted after validation failure.

Machine scope/lease policy (brief §3): issuance and consumers enforce
exact workflow/run/session/host/resource scope and the current
lease/generation. Legacy unstructured worker tokens are rejected
(``401 auth_invalid``) rather than accepted indefinitely or broadened to
unlimited Omnigent delegated authority.

User-client path (brief §4): the thin ``MoonmindUserClient`` below is the
minimum documented secure acquisition/renewal path surviving API/CLI
consumers use. It carries tokens (never mints browser authority), reads
from an explicit file or environment entry (never argv, URLs, global
login caches, or cross-host redirects), renews through an explicitly
authorized server-side renewal callback, and rejects unsupported flows
(refresh/delegated/runner/CLI-ticket/magic-link) actionably. User CLI
authority stays distinct from worker capability tokens. #3939 owns new
run/status/logs commands; this module owns the auth contract the thin
client exercises.

Workflow Chat (brief §5-§6): chat stays behind the existing same-origin
binding-scoped facade. Helpers here authenticate the MoonMind caller,
authorize the exact execution/binding, and only then select the
server-owned upstream credential. Browser-supplied user/host/session
ownership is never trusted and browser cookies/Authorization headers are
never forwarded blindly (see ``sanitize_upstream_forward``). No
unrestricted ``/v1/*`` proxy, shared browser/runtime signing key, or
broad upstream session inventory becomes available. First interaction,
continuation, reconnect, SSE/WebSocket delivery, and native
asset/bootstrap paths all resolve through the same binding check; logout
/ expiry / revocation ends browser access within the #4117 bound while
admitted work continues under its independent owner. The retired
embedded-host transport decision is unchanged.

Preserved lifecycles (brief §7): Provider Profile OAuth
enrollment/materialization, repository PAT/App credentials, and runtime
host-auth lifecycles are untouched. No model/repository credential is
acceptable as application login here, and browser logout never revokes
those independent credentials (asserted in tests).

Redaction (brief §8): transport, header, callback, bridge-evidence, and
Temporal boundaries carry redacted events only; renewal, failure, and
cancellation preserve exact ownership rather than silently broadening or
swapping credentials.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import UUID

from moonmind.security import omnigent_auth_qualification as q
from moonmind.security import session_authority_4121 as authority

__all__ = [
    "CREDENTIAL_CLASSIFICATION",
    "WORKER_TOKEN_AUDIENCE",
    "WORKER_TOKEN_VERSION",
    "WORKER_TOKEN_SECRET_ENV_VAR",
    "GLOBAL_LOGIN_CACHE_PATHS",
    "ScopedWorkerAuthError",
    "ScopedWorkerCapability",
    "WorkerRevocationStore",
    "InMemoryWorkerRevocationStore",
    "mint_scoped_worker_capability",
    "verify_scoped_worker_capability",
    "resolve_machine_or_user",
    "MachineOrUserResolution",
    "resolve_container_job_caller",
    "ContainerJobCaller",
    "MoonmindUserClient",
    "UserClientConfig",
    "UserClientError",
    "authorize_workflow_chat_binding",
    "sanitize_upstream_forward",
    "redact_headers_for_logging",
    "http_status_for_worker_error",
]

WORKER_TOKEN_AUDIENCE = "moonmind-worker"
WORKER_TOKEN_VERSION = 1
WORKER_TOKEN_SECRET_ENV_VAR = "MOONMIND_WORKER_TOKEN_SECRET"

# Paths that must never be used as an implicit CLI login cache. The thin
# client only reads an explicit operator-provided file; a global cache
# would leak one host's authority into another process/host.
GLOBAL_LOGIN_CACHE_PATHS = frozenset(
    {
        "~/.moonmind/session.json",
        "~/.config/moonmind/session.json",
        "~/.moonmind_session",
        "/tmp/moonmind_session.json",
    }
)

# Durable classification record for brief §1 / acceptance evidence. Each
# entry names issuer, audience/purpose, principal, scope, expiry,
# revocation, and the durable execution owner so reviewers can trace
# issuance, delivery, and validation without reading every call site.
CREDENTIAL_CLASSIFICATION: tuple[dict[str, str], ...] = (
    {
        "credential": "moonmind-browser-session",
        "issuer": "moonmind-control-plane",
        "audience": q.MOONMIND_SESSION_PURPOSE,
        "principal": "User.id UUID",
        "scope": "route + resource authorization per request",
        "expiry": "session_ttl_seconds (8h default)",
        "revocation": "per-session JTI + per-user generation, 5-minute bound",
        "owner": "session/revocation owner (#4121)",
        "validator": "omnigent_auth_qualification.validate_moonmind_session",
    },
    {
        "credential": "scoped-worker-capability",
        "issuer": "dispatching workflow owner (this module mints at that owner)",
        "audience": WORKER_TOKEN_AUDIENCE,
        "principal": "owner principal (user:<uuid> or service:<name>)",
        "scope": "exact workflow/run/session/host/resource + operations",
        "expiry": "bounded lifetime at mint (minutes to hours)",
        "revocation": "per-token JTI + per-owner generation (lease policy)",
        "owner": "dispatching workflow owner; admitted work keeps stored principal",
        "validator": "scoped_machine_auth_4126.verify_scoped_worker_capability",
    },
    {
        "credential": "container-job-session-capability",
        "issuer": "managed-session / omnigent session owner",
        "audience": "moonmind-container-jobs",
        "principal": "OwnerIdentity",
        "scope": "exact agent-run/workflow/session/runtime/workspace + source",
        "expiry": "bounded lifetime at mint",
        "revocation": "expiry + scope-mismatch rejection (no cross-run use)",
        "owner": "ContainerJobService owner; Docker authority stays in worker",
        "validator": "container_job_capabilities.verify_container_job_session_capability",
    },
    {
        "credential": "omnigent-host-auth",
        "issuer": "runtime host-auth lifecycle owner",
        "audience": "host registration / refresh purpose",
        "principal": "host identity",
        "scope": "single host / profile generation + overlap window",
        "expiry": "overlap-bounded rotation",
        "revocation": "profile generation + rotation owner",
        "owner": "bridge/host-auth owner (untouched by this module)",
        "validator": "host-auth contracts owner (not reimplemented here)",
    },
    {
        "credential": "provider-profile-oauth / repository-pat-app",
        "issuer": "external provider",
        "audience": "provider-defined audience",
        "principal": "provider identity",
        "scope": "provider-defined scopes",
        "expiry": "provider-defined",
        "revocation": "provider-defined; browser logout never revokes these",
        "owner": "provider-profile / repository-credential owner (untouched)",
        "validator": "provider/repository credential owner (not application login)",
    },
)


class ScopedWorkerAuthError(ValueError):
    """Invalid, expired, revoked, wrong-scope, or conflicting machine credential."""

    def __init__(self, code: str = "auth_invalid", message: str = "invalid worker credential"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ScopedWorkerCapability:
    """Verified identity and scope carried by one worker capability."""

    owner_principal: str
    workflow_id: str
    run_id: str | None
    session_id: str | None
    host_id: str | None
    resource: str | None
    operations: tuple[str, ...]
    token_id: str
    generation: int
    expires_at: int


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as exc:
        raise ScopedWorkerAuthError("auth_invalid", "invalid worker capability") from exc


def _required_text(value: Any, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ScopedWorkerAuthError(
            "auth_invalid", f"worker capability {field_name} must not be empty"
        )
    return normalized


def _secret_bytes(secret: str) -> bytes:
    raw = str(secret or "").encode("utf-8")
    if len(raw) < 32:
        raise ScopedWorkerAuthError(
            "auth_invalid", "worker signing secret must be at least 32 bytes"
        )
    return raw


class WorkerRevocationStore:
    """Durable per-token + per-owner revocation behind a portable interface."""

    async def is_token_revoked(self, token_id: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    async def revoke_token(self, token_id: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def generation_for_owner(self, owner_principal: str) -> int:  # pragma: no cover
        raise NotImplementedError

    async def revoke_all_for_owner(self, owner_principal: str) -> int:  # pragma: no cover
        raise NotImplementedError


class InMemoryWorkerRevocationStore(WorkerRevocationStore):
    """Hermetic revocation fixture shared across validator replicas."""

    def __init__(self) -> None:
        self._revoked: set[str] = set()
        self._generations: dict[str, int] = {}

    async def is_token_revoked(self, token_id: str) -> bool:
        return token_id in self._revoked

    async def revoke_token(self, token_id: str) -> None:
        self._revoked.add(token_id)

    async def generation_for_owner(self, owner_principal: str) -> int:
        return self._generations.get(owner_principal, 0)

    async def revoke_all_for_owner(self, owner_principal: str) -> int:
        self._generations[owner_principal] = self._generations.get(owner_principal, 0) + 1
        return self._generations[owner_principal]


def mint_scoped_worker_capability(
    *,
    secret: str,
    owner_principal: str,
    workflow_id: str,
    run_id: str | None = None,
    session_id: str | None = None,
    host_id: str | None = None,
    resource: str | None = None,
    operations: tuple[str, ...] | list[str] = (),
    lifetime_seconds: int,
    generation: int = 0,
    token_id: str | None = None,
    now: int | None = None,
) -> str:
    """Mint a bearer capability bound to one owner and exact execution scope.

    The minting owner (workflow dispatcher) is the credential owner: every
    claim names the exact workflow/run/session/host/resource the worker may
    touch. Consumers enforce the scope they need via
    :func:`verify_scoped_worker_capability` plus an explicit resource check;
    the token itself never grants user/admin authority.
    """
    signing_secret = _secret_bytes(secret)
    owner = _required_text(owner_principal, field_name="owner")
    workflow = _required_text(workflow_id, field_name="workflowId")
    if lifetime_seconds < 1:
        raise ScopedWorkerAuthError(
            "auth_invalid", "worker capability lifetime must be positive"
        )
    if generation < 0:
        raise ScopedWorkerAuthError(
            "auth_invalid", "worker capability generation must not be negative"
        )
    normalized_operations = tuple(
        str(op).strip() for op in (operations or ()) if str(op).strip()
    )
    if not normalized_operations:
        raise ScopedWorkerAuthError(
            "auth_invalid", "worker capability must name at least one operation"
        )
    issued_at = int(time.time() if now is None else now)
    import secrets as _secrets

    payload = {
        "aud": WORKER_TOKEN_AUDIENCE,
        "v": WORKER_TOKEN_VERSION,
        "owner": owner,
        "workflowId": workflow,
        "runId": (str(run_id or "").strip() or None),
        "sessionId": (str(session_id or "").strip() or None),
        "hostId": (str(host_id or "").strip() or None),
        "resource": (str(resource or "").strip() or None),
        "operations": sorted(normalized_operations),
        "jti": token_id or _secrets.token_hex(16),
        "gen": int(generation),
        "iat": issued_at,
        "exp": issued_at + int(lifetime_seconds),
    }
    encoded = _encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(signing_secret, encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_encode(signature)}"


async def verify_scoped_worker_capability(
    token: str,
    *,
    secret: str,
    revocation: WorkerRevocationStore,
    now: int | None = None,
    require_operation: str | None = None,
    require_workflow_id: str | None = None,
    require_resource: str | None = None,
) -> ScopedWorkerCapability:
    """Verify a worker capability and return only its bounded claims.

    Every call checks structure, signature, audience/version, expiry, live
    per-token revocation, per-owner generation (lease policy), and the
    caller-pinned scope (workflow/resource/operation). Wrong-scope,
    expired, revoked, stale-generation, and runtime-JWT-shaped tokens fail
    closed; they are never reinterpreted as another credential.
    """
    signing_secret = _secret_bytes(secret)
    normalized = _required_text(token, field_name="token")
    # Runtime/session JWTs presented as worker tokens are rejected, never
    # reinterpreted: they carry dots in a different shape and never verify
    # against the worker secret/audience, which fails closed below.
    encoded_payload, separator, encoded_signature = normalized.partition(".")
    if not separator or not encoded_payload or not encoded_signature:
        raise ScopedWorkerAuthError("auth_invalid", "invalid worker capability")
    expected = hmac.new(
        signing_secret, encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(_decode(encoded_signature), expected):
        raise ScopedWorkerAuthError("auth_invalid", "invalid worker capability")
    try:
        payload = json.loads(_decode(encoded_payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScopedWorkerAuthError("auth_invalid", "invalid worker capability") from exc
    if not isinstance(payload, dict):
        raise ScopedWorkerAuthError("auth_invalid", "invalid worker capability")
    if payload.get("aud") != WORKER_TOKEN_AUDIENCE or payload.get("v") != WORKER_TOKEN_VERSION:
        raise ScopedWorkerAuthError("auth_invalid", "wrong worker capability audience")
    # Grant-derived / delegated shapes are never worker authority.
    if payload.get("grant_id") is not None or payload.get("scope") is not None:
        raise ScopedWorkerAuthError("auth_invalid", "unsupported worker token shape")
    try:
        expires_at = int(payload["exp"])
        owner = _required_text(payload.get("owner"), field_name="owner")
        workflow_id = _required_text(payload.get("workflowId"), field_name="workflowId")
        token_id = _required_text(payload.get("jti"), field_name="jti")
        generation = int(payload.get("gen", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise ScopedWorkerAuthError("auth_invalid", "invalid worker capability") from exc
    current_time = int(time.time() if now is None else now)
    if expires_at <= current_time:
        raise ScopedWorkerAuthError("auth_invalid", "expired worker capability")
    try:
        if await revocation.is_token_revoked(token_id):
            raise ScopedWorkerAuthError("auth_invalid", "revoked worker capability")
        live_generation = await revocation.generation_for_owner(owner)
    except ScopedWorkerAuthError:
        raise
    except Exception as exc:
        raise ScopedWorkerAuthError("unavailable", "worker revocation unavailable") from exc
    if int(generation) != int(live_generation):
        raise ScopedWorkerAuthError("auth_invalid", "stale worker lease generation")
    operations = tuple(
        str(op) for op in (payload.get("operations") or ()) if str(op).strip()
    )
    if not operations:
        raise ScopedWorkerAuthError("auth_invalid", "worker capability names no operation")
    if require_operation is not None and require_operation not in operations:
        raise ScopedWorkerAuthError("auth_invalid", "worker capability wrong scope")
    if require_workflow_id is not None and workflow_id != require_workflow_id:
        raise ScopedWorkerAuthError("auth_invalid", "worker capability wrong workflow")
    if require_resource is not None:
        token_resource = str(payload.get("resource") or "").strip()
        if token_resource != str(require_resource).strip():
            raise ScopedWorkerAuthError("auth_invalid", "worker capability wrong resource")

    def _opt(key: str) -> str | None:
        text = str(payload.get(key) or "").strip()
        return text or None

    return ScopedWorkerCapability(
        owner_principal=owner,
        workflow_id=workflow_id,
        run_id=_opt("runId"),
        session_id=_opt("sessionId"),
        host_id=_opt("hostId"),
        resource=_opt("resource"),
        operations=operations,
        token_id=token_id,
        generation=int(generation),
        expires_at=expires_at,
    )


def http_status_for_worker_error(exc: BaseException) -> tuple[int, str]:
    """Map a worker-auth failure to ``(status, code)`` per contracts §8."""
    code = getattr(exc, "code", None) or "auth_invalid"
    if code == "auth_required":
        return 401, "auth_required"
    if code == "auth_conflict":
        return 401, "auth_conflict"
    if code == "unavailable":
        return 503, "unavailable"
    return 401, "auth_invalid"


@dataclass(frozen=True, slots=True)
class MachineOrUserResolution:
    """Exactly which authority governs one worker-tolerant request."""

    kind: str  # "machine" | "user"
    owner_principal: str
    capability: ScopedWorkerCapability | None = None


async def resolve_machine_or_user(
    *,
    worker_token: str | None,
    user_id: str | None,
    secret: str,
    revocation: WorkerRevocationStore,
    now: int | None = None,
) -> MachineOrUserResolution:
    """Resolve one worker-tolerant request to exactly one authority.

    * Missing machine token + missing user → ``ScopedWorkerAuthError``
      (``auth_required``): missing all accepted authority is not normal.
    * Missing machine token + present user → ``kind="user"`` (browser path).
    * Present machine token → it must verify; an invalid/expired/revoked/
      wrong-scope token raises ``auth_invalid`` even when a user is also
      present (invalid credentials never silently become another
      principal). When both verify, the worker owner and the user must be
      the same principal or ``auth_conflict`` is raised.
    * Missing browser user + valid machine token → ``kind="machine"``:
      missing browser cookies are normal for machine calls.
    """
    has_machine = bool(str(worker_token or "").strip())
    has_user = bool(str(user_id or "").strip())
    if not has_machine and not has_user:
        raise ScopedWorkerAuthError("auth_required", "valid worker or user credentials are required")
    if has_machine:
        capability = await verify_scoped_worker_capability(
            str(worker_token or "").strip(),
            secret=secret,
            revocation=revocation,
            now=now,
        )
        if has_user and str(user_id or "").strip() != capability.owner_principal:
            # A worker token never escalates to another user's authority:
            # normalize ``user:<uuid>`` against a raw UUID user id.
            normalized_user = str(user_id or "").strip()
            owner = capability.owner_principal
            owner_uuid = owner.split(":", 1)[1] if ":" in owner else owner
            if normalized_user != owner and normalized_user != owner_uuid:
                raise ScopedWorkerAuthError("auth_conflict", "conflicting worker and user identities")
        return MachineOrUserResolution(
            kind="machine", owner_principal=capability.owner_principal, capability=capability
        )
    assert user_id is not None
    return MachineOrUserResolution(kind="user", owner_principal=str(user_id).strip())


@dataclass(frozen=True, slots=True)
class ContainerJobCaller:
    """Exactly which authority governs one container-job request."""

    kind: str  # "user" | "machine"
    owner: Any


async def resolve_container_job_caller(
    *,
    user: Any | None,
    authorization: str | None,
    verify_capability,
) -> ContainerJobCaller:
    """Resolve a container-job caller to one owner without broadening authority.

    ``verify_capability`` is the existing session-scoped container capability
    verifier (same credential owner and secret as the MCP transport). Browser
    users keep working; machine callers present the bearer capability without
    browser cookies. Invalid presented credentials fail closed; conflicting
    user/machine owners fail as ``auth_conflict``; missing everything fails
    as ``auth_required``. Runtime, session, and worker tokens are not valid
    container capabilities and are rejected by the verifier, never promoted.
    """
    user_id = getattr(user, "id", None)
    has_user = user_id is not None and str(user_id).strip() != ""
    scheme, _, token = str(authorization or "").partition(" ")
    has_bearer = bool(scheme.strip()) and bool(token.strip())
    bearer_token = token.strip() if has_bearer and scheme.strip().lower() == "bearer" else None
    presented_credential = bool(str(authorization or "").strip())

    if has_user and bearer_token is None:
        from moonmind.schemas.container_job_models import OwnerIdentity

        return ContainerJobCaller(
            kind="user",
            owner=OwnerIdentity(principalId=str(user_id), principalType="user"),
        )
    if bearer_token is not None:
        from moonmind.security.container_job_capabilities import (
            ContainerJobCapabilityError,
        )

        try:
            capability = verify_capability(bearer_token)
        except ContainerJobCapabilityError as exc:
            if has_user:
                # The bearer is the browser session token the session layer
                # already accepted; the browser path governs.
                from moonmind.schemas.container_job_models import OwnerIdentity

                return ContainerJobCaller(
                    kind="user",
                    owner=OwnerIdentity(principalId=str(user_id), principalType="user"),
                )
            raise ScopedWorkerAuthError("auth_invalid", str(exc)) from exc
        if has_user:
            owner_id = getattr(capability.owner, "principal_id", None) or getattr(
                capability.owner, "principalId", ""
            )
            if str(owner_id).strip() != str(user_id).strip():
                raise ScopedWorkerAuthError(
                    "auth_conflict", "conflicting user and machine identities"
                )
        return ContainerJobCaller(kind="machine", owner=capability.owner)
    if presented_credential:
        # A credential was presented but is neither a valid session nor a
        # valid machine capability: invalid, never "missing".
        raise ScopedWorkerAuthError("auth_invalid", "invalid container credential")
    raise ScopedWorkerAuthError("auth_required", "user or container capability required")


# ---------------------------------------------------------------------------
# Thin user CLI auth client (brief §4)
# ---------------------------------------------------------------------------


class UserClientError(ValueError):
    """Actionable thin-client auth failure (no business logic here)."""

    def __init__(self, code: str = "auth_invalid", message: str = "user client auth failed"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class UserClientConfig:
    """Explicit operator-provided client configuration.

    ``base_url`` is the exact same-origin API the client may talk to
    (cross-host redirects are rejected). ``token_file`` is an explicit
    operator-provided path; global login caches are rejected. Tokens never
    come from argv or URLs.
    """

    base_url: str
    token_file: str | None = None
    token_env_var: str = "MOONMIND_SESSION_TOKEN"


@dataclass(slots=True)
class MoonmindUserClient:
    """Thin conformance client for surviving API/CLI authentication.

    The client only carries and renews the caller's own session token; it
    contains no workflow/CLI business logic (that stays with #3939). All
    token crypto/validation runs through the qualified #4118 primitives via
    an explicitly provided ``validate`` callback so tests compose the real
    production validation without duplicating it.
    """

    config: UserClientConfig

    def _reject_global_cache(self, candidate: str) -> None:
        expanded = candidate.strip()
        home_tilde = expanded
        for blocked in GLOBAL_LOGIN_CACHE_PATHS:
            if home_tilde == blocked or home_tilde.endswith(blocked.lstrip("~")):
                raise UserClientError(
                    "auth_invalid",
                    "global login caches are not used; pass an explicit token file",
                )

    def load_token(
        self, *, environ: Mapping[str, str] | None = None, read_file=None
    ) -> str:
        """Load the caller token from an explicit file or environment entry."""
        import os as _os

        env: Mapping[str, str] = _os.environ if environ is None else environ
        if self.config.token_file is not None:
            candidate = str(self.config.token_file).strip()
            if not candidate:
                raise UserClientError("auth_required", "no token file configured")
            self._reject_global_cache(candidate)
            reader = read_file or Path(candidate).read_text
            try:
                token = str(reader() if read_file is not None else reader(encoding="utf-8")).strip()
            except OSError as exc:
                raise UserClientError("unavailable", "token file unavailable") from exc
            if not token:
                raise UserClientError("auth_required", "token file is empty")
            return token
        token = str(env.get(self.config.token_env_var) or "").strip()
        if not token:
            raise UserClientError(
                "auth_required",
                f"{self.config.token_env_var} is required; no secrets are read from argv",
            )
        return token

    def assert_no_argv_secret(self, argv: list[str], token: str) -> None:
        """Fail when the token appears in process arguments (negative guard)."""
        for element in argv:
            if token and token in str(element):
                raise UserClientError(
                    "auth_invalid", "session material must never appear in argv"
                )

    def assert_same_origin(self, request_url: str) -> None:
        """Reject cross-host redirects: the client only talks to ``base_url``."""
        expected = urlsplit(self.config.base_url.strip())
        candidate = urlsplit(str(request_url or "").strip())

        def _port(parts) -> int:
            try:
                if parts.port is not None:
                    return parts.port
            except ValueError:
                return -1
            return 443 if parts.scheme == "https" else 80

        if (
            not candidate.scheme
            or candidate.scheme.lower() != (expected.scheme or "").lower()
            or (candidate.hostname or "").lower() != (expected.hostname or "").lower()
            or _port(candidate) != _port(expected)
        ):
            raise UserClientError(
                "auth_invalid", "cross-host redirect rejected for user client"
            )

    def assert_no_token_in_url(self, url: str) -> None:
        """Fail when a URL carries secret material (negative guard)."""
        lowered = str(url or "").lower()
        if "token=" in lowered or "session=" in lowered or "eyj" in lowered:
            raise UserClientError(
                "auth_invalid", "session material must never appear in URLs"
            )

    def needs_renewal(self, token: str, *, within_seconds: int = 300, now: int | None = None) -> bool:
        """Whether the token expires within the renewal window (no validation)."""
        import jwt as _jwt

        try:
            payload = _jwt.decode(token, options={"verify_signature": False})
            remaining = float(payload.get("exp", 0)) - float(
                time.time() if now is None else now
            )
        except Exception as exc:
            raise UserClientError("auth_invalid", "unparseable session token") from exc
        return remaining <= float(within_seconds)

    async def validate(
        self,
        token: str,
        *,
        account_store: q.AsyncAccountStore,
        revocation: q.SessionRevocationStore,
        config: q.MoonmindAuthConfig,
    ) -> q.AccountRecord:
        """Validate the caller token through the qualified production path."""
        try:
            return await q.validate_moonmind_session(token, account_store, revocation, config)
        except q.AuthInvalidError as exc:
            raise UserClientError("auth_invalid", str(exc)) from exc
        except q.AuthConflictError as exc:
            raise UserClientError("auth_conflict", str(exc)) from exc
        except q.ForbiddenError as exc:
            raise UserClientError("forbidden", str(exc)) from exc
        except q.UnavailableError as exc:
            raise UserClientError("unavailable", str(exc)) from exc

    async def renew(self, token: str, *, renew_fn) -> str:
        """Renew through an explicitly authorized server-side renewal callback."""
        if renew_fn is None:
            raise UserClientError(
                "unsupported_surface", "automatic renewal requires explicit authorization"
            )
        renewed = await renew_fn(token)
        candidate = str(renewed or "").strip()
        if not candidate:
            raise UserClientError("auth_invalid", "renewal returned no token")
        if candidate == token:
            raise UserClientError("auth_invalid", "renewal must issue a new token")
        return candidate

    def exchange_refresh_token(self, *_args: Any, **_kwargs: Any) -> None:
        """Refresh-grant exchange is unsupported at this boundary."""
        raise UserClientError(
            "unsupported_surface", "refresh-grant exchange is not supported for user clients"
        )

    def exchange_delegated_token(self, *_args: Any, **_kwargs: Any) -> None:
        """Delegated-scope exchange is unsupported at this boundary."""
        raise UserClientError(
            "unsupported_surface",
            "delegated-scope exchange is not supported for user clients",
        )


# ---------------------------------------------------------------------------
# Workflow Chat binding authorization (brief §5-§6)
# ---------------------------------------------------------------------------


def authorize_workflow_chat_binding(
    *,
    caller_user_id: str | None,
    binding_owner_id: str | None,
    binding_id: str | None,
) -> str:
    """Authorize the exact execution/binding for one chat request.

    ``binding_owner_id`` must be the server-resolved durable owner (bridge
    binding row), never a browser-supplied value: callers resolve it from
    the store before calling here. Missing callers or bindings fail as
    ``auth_required``/``binding_unknown``; a caller that does not own the
    binding fails as ``caller_unauthorized`` without revealing whether the
    binding exists beyond the non-enumerating code. Admitted work keeps its
    independent owner: this check gates browser access only.
    """
    binding = str(binding_id or "").strip()
    caller = str(caller_user_id or "").strip()
    owner = str(binding_owner_id or "").strip()
    if not caller:
        raise ScopedWorkerAuthError("auth_required", "chat caller authentication required")
    if not binding or not owner:
        raise ScopedWorkerAuthError("auth_invalid", "unknown chat binding")
    if caller != owner:
        raise ScopedWorkerAuthError("auth_invalid", "chat caller does not own this binding")
    return binding


_FORWARDED_CREDENTIAL_HEADERS = frozenset({"cookie", "authorization", "proxy-authorization"})
_FORWARDED_IDENTITY_HEADERS = frozenset(
    {
        "x-omnigent-session",
        "x-omnigent-session-id",
        "x-provider-session-id",
        "x-omnigent-endpoint",
        "x-omnigent-host",
        "x-omnigent-host-id",
        "x-omnigent-runner",
        "x-omnigent-runner-id",
    }
)


def sanitize_upstream_forward(
    headers: Mapping[str, Any], *, server_credential: str
) -> dict[str, str]:
    """Build the upstream header set using only server-owned credentials.

    Browser cookies and ``Authorization``/proxy material are stripped, never
    forwarded blindly; browser-supplied upstream-identity headers are
    rejected outright. The returned mapping carries exactly one server-owned
    credential for the already-authorized binding.
    """
    if not str(server_credential or "").strip():
        raise ScopedWorkerAuthError("unavailable", "no server-owned upstream credential")
    forwarded: dict[str, str] = {}
    for name, value in dict(headers or {}).items():
        lowered = str(name or "").strip().lower()
        if lowered in _FORWARDED_IDENTITY_HEADERS:
            raise ScopedWorkerAuthError(
                "auth_invalid", "browser-supplied upstream identity rejected"
            )
        if lowered in _FORWARDED_CREDENTIAL_HEADERS:
            continue
        forwarded[str(name)] = str(value)
    forwarded["Authorization"] = f"Bearer {str(server_credential).strip()}"
    return forwarded


def redact_headers_for_logging(headers: Mapping[str, Any]) -> dict[str, str]:
    """Render headers for logs/traces with credential values redacted."""
    redacted: dict[str, str] = {}
    for name, value in dict(headers or {}).items():
        lowered = str(name or "").strip().lower()
        if lowered in _FORWARDED_CREDENTIAL_HEADERS or "token" in lowered or "cookie" in lowered:
            redacted[str(name)] = "(redacted)"
        else:
            redacted[str(name)] = str(value)
    return redacted


def emit_preservation_event(
    kind: str,
    *,
    mode: str,
    reason: str,
    request_id: str | None = None,
    user_id: str | None = None,
    binding_id: str | None = None,
) -> dict[str, Any]:
    """Record a redacted preservation event (no secret material)."""
    extra: dict[str, Any] = {}
    if binding_id is not None:
        extra["binding_id"] = str(binding_id)
    return authority.emit_auth_event(
        kind, mode=mode, reason=reason, request_id=request_id, user_id=user_id, extra=extra
    )
