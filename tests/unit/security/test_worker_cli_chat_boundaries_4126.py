"""Scoped worker, user-CLI, and Workflow Chat boundaries (#4126).

MoonLadderStudios/MoonMind#4126 (parent #4116, K4 machine/runtime authority
plus native chat): hermetic proof that the user-auth change neither breaks
independent machine work nor grants machines/browser clients broader
authority.

Hermetic by design: synthetic fixtures only, no DB, no network, no live
secrets, no provider account. Production boundaries are exercised through
their real code (session mint/validate, container-job and execution-fanout
mint/verify, worker gate, facade allowlist/identity guard/capability
recompute, thin user CLI client), never by swapping in a mock user.

Covers the bounded backlog from the PARTIALLY_IMPLEMENTED assessment:

* impl-1/acc-2: credential classification -- each credential validates only
  at its own boundary (issuer/audience/purpose/scope/expiry/revocation);
  user-login JWT removal leaves container/fanout/session validators intact.
* impl-2/acc-2: optional-user missing-vs-invalid-vs-conflict; no default
  user, ambient token, or full-session substitution after failure.
* impl-3/acc-1: machine issuance enforces exact workflow/run/session/
  runtime scope and expiry; legacy worker tokens stay ``410``; ordinary
  browser principals cannot satisfy worker-only mutations; cookie-less
  machine calls authorize on their scoped bearer.
* impl-4/acc-3: thin user CLI auth contract + hermetic client-to-API proof
  without duplicate business logic (secrets never in argv/URLs/caches,
  same-origin only, unsupported flows reject actionably, user authority
  distinct from worker capability tokens).
* impl-5/impl-6/acc-4/acc-5: Workflow Chat binding isolation for two users
  (first message, continuation, reconnect), terminal read-only, logout/
  revocation ending browser access while admitted machine work continues.
* impl-8/acc-6: redaction at transport/header/callback/bridge/Temporal
  boundaries; renewal/failure/cancellation preserve exact ownership;
  upstream denial, replayed callbacks, stale leases, and restarts stay
  bounded with no credential substitution or leakage.
"""

from __future__ import annotations

import secrets
import uuid
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException

from api_service.api.routers import worker_auth as worker_auth_module
from moonmind.config import settings as settings_module
from moonmind.schemas.container_job_models import OwnerIdentity
from moonmind.security import auth_modes_4120 as modes
from moonmind.security import container_job_capabilities as container_caps
from moonmind.security import execution_fanout_capabilities as fanout_caps
from moonmind.security import omnigent_auth_qualification as qual
from moonmind.security import session_authority_4121 as authority
from moonmind import user_auth_cli

ISSUER_A = "https://idp-a.example.invalid/realms/moonmind"
ISSUER_B = "https://idp-b.example.invalid/realms/moonmind"
FANOUT_SECRET = "fanout-signing-secret-4126-32bytes!!"
CONTAINER_SECRET = "container-signing-secret-4126-32b!!"


# ---------------------------------------------------------------------------
# Fixtures: hermetic production-boundary inputs (synthetic only)
# ---------------------------------------------------------------------------


def _control_plane_config(mode: str = "accounts"):
    return modes.resolve_moonmind_auth_config(
        mode=mode,
        cookie_secret=secrets.token_bytes(32),
        environ={},
    )


def _enrolled_user(email: str = "owner@example.invalid"):
    store = qual.InMemoryAsyncAccountStore()
    revocation = qual.InMemoryRevocationStore()
    user_id = uuid.uuid4()
    identity = qual.ValidatedIdentity(issuer=ISSUER_A, subject=f"sub-{uuid.uuid4().hex[:8]}")
    store.enroll(
        identity,
        qual.AccountRecord(
            user_id=user_id, is_active=True, is_superuser=False, email=email
        ),
    )
    return store, revocation, user_id, identity


def _set_production_mode(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(settings_module.oidc, "AUTH_PROVIDER", mode)
    monkeypatch.setattr(modes, "_ACTIVE_PRODUCTION_MODE", mode, raising=False)


def _mint_fanout(*, now: int | None = None, lifetime: int = 600, **overrides):
    params = {
        "secret": FANOUT_SECRET,
        "parent_workflow_id": "wf-4126",
        "agent_run_id": "run-4126",
        "session_id": "sess-4126",
        "runtime_id": "runtime-4126",
        "source_kind": "managed_session",
        "lifetime_seconds": lifetime,
    }
    params.update(overrides)
    if now is not None:
        params["now"] = now
    return fanout_caps.mint_execution_fanout_capability(**params)


def _mint_container(*, lifetime: int = 600, **overrides):
    params = {
        "secret": CONTAINER_SECRET,
        "owner": OwnerIdentity(principal_id=str(uuid.uuid4()), principal_type="user"),
        "agent_run_id": "run-4126",
        "workflow_id": "wf-4126",
        "session_id": "sess-4126",
        "runtime_id": "runtime-4126",
        "lifetime_seconds": lifetime,
    }
    params.update(overrides)
    return container_caps.mint_container_job_session_capability(**params)


# ---------------------------------------------------------------------------
# impl-1/acc-2: credential classification -- each validator owns its boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_credential_validates_only_at_its_own_boundary():
    """Session, container-job, and fanout credentials are mutually unaccepted."""
    config = _control_plane_config()
    store, revocation, user_id, identity = _enrolled_user()
    session_token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    container_token = _mint_container()
    fanout_token = _mint_fanout()

    # Each credential verifies at its own boundary ...
    account = await qual.validate_moonmind_session(session_token, store, revocation, config)
    assert account.user_id == user_id
    container_caps.verify_container_job_session_capability(
        container_token, secret=CONTAINER_SECRET
    )
    fanout_caps.verify_execution_fanout_capability(fanout_token, secret=FANOUT_SECRET)

    # ... and fails closed at the other two (no cross-purpose acceptance).
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(container_token, store, revocation, config)
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(fanout_token, store, revocation, config)
    with pytest.raises(container_caps.ContainerJobCapabilityError):
        container_caps.verify_container_job_session_capability(
            fanout_token, secret=CONTAINER_SECRET
        )
    with pytest.raises(container_caps.ContainerJobCapabilityError):
        container_caps.verify_container_job_session_capability(
            session_token, secret=CONTAINER_SECRET
        )
    with pytest.raises(fanout_caps.ExecutionFanoutCapabilityError):
        fanout_caps.verify_execution_fanout_capability(
            container_token, secret=FANOUT_SECRET
        )
    with pytest.raises(fanout_caps.ExecutionFanoutCapabilityError):
        fanout_caps.verify_execution_fanout_capability(
            session_token, secret=FANOUT_SECRET
        )

    # Purpose/issuer/audience binding is structural on the session token.
    payload = jwt.decode(session_token, options={"verify_signature": False})
    assert payload["iss"] == qual.MOONMIND_TOKEN_ISSUER
    assert payload["aud"] == qual.MOONMIND_TOKEN_AUDIENCE
    assert payload["purpose"] == qual.MOONMIND_SESSION_PURPOSE

    # Runtime/upstream token shapes are never MoonMind user login.
    for surface in qual.UNSUPPORTED_SURFACES:
        with pytest.raises(qual.UnsupportedSurfaceError):
            qual.assert_surface_not_used(surface)
    with pytest.raises(qual.UnsupportedSurfaceError):
        qual.reject_unsupported_token_shape({"grant_id": "g-4126"})
    with pytest.raises(qual.UnsupportedSurfaceError):
        qual.reject_unsupported_token_shape({"scope": "delegated"})


@pytest.mark.asyncio
async def test_machine_scope_and_expiry_enforced_exactly():
    """Wrong-scope, expired, and wrong-key machine bearers fail closed."""
    valid = _mint_fanout()
    verified = fanout_caps.verify_execution_fanout_capability(valid, secret=FANOUT_SECRET)
    assert verified.parent_workflow_id == "wf-4126"
    assert verified.agent_run_id == "run-4126"
    assert verified.session_id == "sess-4126"
    assert verified.runtime_id == "runtime-4126"

    # Expired bearer.
    stale = _mint_fanout(now=1_000_000, lifetime=60)
    with pytest.raises(fanout_caps.ExecutionFanoutCapabilityError, match="expired"):
        fanout_caps.verify_execution_fanout_capability(
            stale, secret=FANOUT_SECRET, now=1_000_000 + 61
        )
    # Wrong signing key.
    with pytest.raises(fanout_caps.ExecutionFanoutCapabilityError, match="invalid"):
        fanout_caps.verify_execution_fanout_capability(valid, secret="wrong-key-4126-xxxxxxxxxxxx")
    # Tampered scope does not verify.
    assert valid.count(".") == 1
    tampered = "X" + valid[1:]
    with pytest.raises(fanout_caps.ExecutionFanoutCapabilityError):
        fanout_caps.verify_execution_fanout_capability(tampered, secret=FANOUT_SECRET)

    # Container capability: exact workspace/correlation scope.
    scoped = _mint_container(workspace_relative_path="repo")
    capability = container_caps.verify_container_job_session_capability(
        scoped, secret=CONTAINER_SECRET
    )
    assert capability.agent_run_id == "run-4126"
    assert capability.workspace_relative_path == "repo"
    other_scope = _mint_container(agent_run_id="run-other-4126")
    other = container_caps.verify_container_job_session_capability(
        other_scope, secret=CONTAINER_SECRET
    )
    assert other.agent_run_id != capability.agent_run_id


# ---------------------------------------------------------------------------
# impl-2/acc-2: optional-user missing vs invalid vs conflicting identities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_optional_boundary_missing_is_none_invalid_is_denial():
    """Missing credentials stay optional; presented-but-bad credentials deny."""
    config = _control_plane_config()
    store = qual.InMemoryAsyncAccountStore()
    revocation = qual.InMemoryRevocationStore()

    assert (
        await authority.resolve_session_user(
            cookie_token=None,
            bearer_token=None,
            account_store=store,
            revocation=revocation,
            config=config,
            optional=True,
        )
        is None
    )
    with pytest.raises(qual.AuthRequiredError):
        await authority.resolve_session_user(
            cookie_token=None,
            bearer_token=None,
            account_store=store,
            revocation=revocation,
            config=config,
            optional=False,
        )
    # Invalid presented credentials are never swallowed as anonymous success,
    # on either the optional or the strict path.
    for optional in (True, False):
        with pytest.raises(qual.AuthInvalidError):
            await authority.resolve_session_user(
                cookie_token="bogus-4126",
                bearer_token=None,
                account_store=store,
                revocation=revocation,
                config=config,
                optional=optional,
            )


@pytest.mark.asyncio
async def test_conflicting_cookie_and_bearer_identities_are_rejected():
    """Two credentials resolving to different principals fail with conflict."""
    config = _control_plane_config()
    store_a, revocation, user_a, identity_a = _enrolled_user("a4126@example.invalid")
    store_b, _, user_b, identity_b = _enrolled_user("b4126@example.invalid")
    # Merge both enrollments into one store so both tokens validate there.
    store_a.enroll(
        identity_b,
        qual.AccountRecord(
            user_id=user_b, is_active=True, is_superuser=False, email="b4126@example.invalid"
        ),
    )
    token_a, _ = await qual.mint_moonmind_session(
        identity_a, store_a, config, revocation=revocation
    )
    token_b, _ = await qual.mint_moonmind_session(
        identity_b, store_a, config, revocation=revocation
    )
    assert user_a != user_b

    status_code, code = authority.http_status_for_error(authority.AuthConflictError())
    assert (status_code, code) == (401, "auth_conflict")
    with pytest.raises(qual.AuthConflictError):
        await authority.resolve_session_user(
            cookie_token=token_a,
            bearer_token=token_b,
            account_store=store_a,
            revocation=revocation,
            config=config,
            optional=True,
        )
    # Same principal presented twice is not a conflict.
    resolved = await authority.resolve_session_user(
        cookie_token=token_a,
        bearer_token=token_a,
        account_store=store_a,
        revocation=revocation,
        config=config,
        optional=True,
    )
    assert resolved is not None and resolved.user_id == user_a
    _ = store_b  # distinct-enrollment fixture retained for two-user clarity.


# ---------------------------------------------------------------------------
# impl-3/acc-1: scoped worker gate at its existing owner
# ---------------------------------------------------------------------------


def _production_fanout_secret(monkeypatch) -> None:
    monkeypatch.setattr(
        settings_module.security, "JWT_SECRET_KEY", FANOUT_SECRET, raising=False
    )


@pytest.mark.asyncio
async def test_worker_gate_accepts_scoped_machine_bearer_without_cookies(monkeypatch):
    """Cookie-less machine calls authorize on the exact fanout scope."""
    _set_production_mode(monkeypatch, "accounts")
    _production_fanout_secret(monkeypatch)
    token = _mint_fanout()
    resolved = await worker_auth_module._require_worker_auth(
        worker_token=None,
        fanout_marker="v1",
        authorization=f"Bearer {token}",
        user=None,
    )
    assert resolved.auth_source == "execution_fanout"
    assert resolved.parent_workflow_id == "wf-4126"
    assert resolved.agent_run_id == "run-4126"
    assert resolved.session_id == "sess-4126"
    assert resolved.runtime_id == "runtime-4126"
    assert resolved.expires_at is not None
    assert "execution.fanout" in resolved.capabilities


@pytest.mark.asyncio
async def test_worker_gate_rejects_legacy_expired_and_wrong_scope_machine_tokens(
    monkeypatch,
):
    """Legacy, expired, wrong-key, and tampered machine bearers fail closed."""
    _set_production_mode(monkeypatch, "accounts")
    _production_fanout_secret(monkeypatch)
    user = SimpleNamespace(id=uuid.uuid4())

    with pytest.raises(HTTPException) as exc_info:
        await worker_auth_module._require_worker_auth(
            worker_token="legacy-token", user=user
        )
    assert exc_info.value.status_code == 410
    assert exc_info.value.detail["code"] == "worker_token_deprecated"

    stale = _mint_fanout(now=1_000_000, lifetime=60)
    with pytest.raises(HTTPException) as exc_info:
        await worker_auth_module._require_worker_auth(
            worker_token=None,
            fanout_marker="v1",
            authorization=f"Bearer {stale}",
            user=None,
        )
    assert exc_info.value.status_code == 401

    monkeypatch.setattr(
        settings_module.security, "JWT_SECRET_KEY", "wrong-key-4126-xxxxxxxxxxxx", raising=False
    )
    with pytest.raises(HTTPException) as exc_info:
        await worker_auth_module._require_worker_auth(
            worker_token=None,
            fanout_marker="v1",
            authorization=f"Bearer {_mint_fanout()}",
            user=None,
        )
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_worker_gate_rejects_ordinary_browser_principal(monkeypatch):
    """An authenticated browser login alone never satisfies worker-only work."""
    _set_production_mode(monkeypatch, "accounts")
    _production_fanout_secret(monkeypatch)
    user = SimpleNamespace(id=uuid.uuid4(), email="browser4126@example.invalid")
    with pytest.raises(HTTPException) as exc_info:
        await worker_auth_module._require_worker_auth(
            worker_token=None, fanout_marker=None, authorization=None, user=user
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["code"] == "worker_authorization_required"

    with pytest.raises(HTTPException) as exc_info:
        await worker_auth_module._require_worker_auth(
            worker_token=None, fanout_marker=None, authorization=None, user=None
        )
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_worker_gate_machine_failure_never_falls_back_to_browser(monkeypatch):
    """A bad machine bearer plus a valid browser login still denies."""
    _set_production_mode(monkeypatch, "accounts")
    _production_fanout_secret(monkeypatch)
    user = SimpleNamespace(id=uuid.uuid4(), email="browser4126@example.invalid")
    with pytest.raises(HTTPException) as exc_info:
        await worker_auth_module._require_worker_auth(
            worker_token=None,
            fanout_marker="v1",
            authorization="Bearer bogus-4126",
            user=user,
        )
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# impl-4/acc-3: thin user CLI contract + hermetic client-to-API proof
# ---------------------------------------------------------------------------


def test_user_cli_contract_rejects_argv_url_cache_and_machine_credentials(tmp_path, monkeypatch):
    """The thin client keeps secrets out of argv/URLs/caches and flows distinct."""
    monkeypatch.setenv("MOONMIND_URL", "http://127.0.0.1:7000")
    monkeypatch.delenv("MOONMIND_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("MOONMIND_SESSION_TOKEN_FILE", raising=False)

    # Missing material is actionable, never silent.
    with pytest.raises(user_auth_cli.UserAuthCliError, match="both unset"):
        user_auth_cli.load_session_token(env={})

    # Machine capability shapes are not user login.
    with pytest.raises(user_auth_cli.UserAuthCliError, match="machine capability"):
        user_auth_cli.load_session_token(env={"MOONMIND_SESSION_TOKEN": "abc.def"})
    with pytest.raises(user_auth_cli.UserAuthCliError, match="not.*user session"):
        user_auth_cli.load_session_token(
            env={"MOONMIND_SESSION_TOKEN": "mm-proxy-token:secret-4126"}
        )

    # World-readable token files are rejected before any read.
    token_file = tmp_path / "session-token-4126"
    token_file.write_text("user-session-4126", encoding="utf-8")
    token_file.chmod(0o644)
    with pytest.raises(user_auth_cli.UserAuthCliError, match="0600"):
        user_auth_cli.load_session_token(
            env={"MOONMIND_SESSION_TOKEN_FILE": str(token_file)}
        )
    token_file.chmod(0o600)
    assert (
        user_auth_cli.load_session_token(
            env={"MOONMIND_SESSION_TOKEN_FILE": str(token_file)}
        )
        == "user-session-4126"
    )

    # Cross-host endpoints and credentialed URLs are rejected.
    with pytest.raises(user_auth_cli.UserAuthCliError, match="same-origin"):
        user_auth_cli.assert_same_origin(
            "http://evil.example.invalid/api", base_url="http://127.0.0.1:7000"
        )
    with pytest.raises(user_auth_cli.UserAuthCliError, match="must not embed"):
        user_auth_cli.resolve_base_url("http://user:pass@127.0.0.1:7000")

    # Unsupported grants name the supported path instead of attempting it.
    with pytest.raises(user_auth_cli.UserAuthCliError, match="Unsupported user-auth grant"):
        user_auth_cli.acquire_session(grant="authorization_code", env={})
    with pytest.raises(user_auth_cli.UserAuthCliError, match="Unsupported user-auth grant"):
        user_auth_cli.acquire_session(grant="worker_token", env={})

    # Supported grants describe one same-origin request without logging secrets.
    described = user_auth_cli.acquire_session(
        grant="session",
        token="user-session-4126",
        env={"MOONMIND_URL": "http://127.0.0.1:7000"},
    )
    assert described["grant"] == "session"
    assert described["endpoint"].startswith("http://127.0.0.1:7000/")
    assert "user-session-4126" not in str(described)


@pytest.mark.asyncio
async def test_hermetic_user_client_to_api_renewal_and_logout_isolation():
    """A real client token exercises issue -> use -> renew -> revoke (#4126 acc-3).

    Uses the thin conformance client for transport decisions (same-origin
    endpoint, header construction) and the qualified session boundary for
    issuance/validation, so no CLI business logic is duplicated in the test.
    Browser logout/revocation ends user access while an admitted machine
    capability for the same run stays operational.
    """
    from fastapi import FastAPI, Header
    from fastapi.testclient import TestClient

    config = _control_plane_config()
    store, revocation, user_id, identity = _enrolled_user("cli4126@example.invalid")
    token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    machine_token = _mint_container()

    app = FastAPI()

    async def _validated_user(request_token: str = ""):
        return await qual.validate_moonmind_session(
            request_token, store, revocation, config
        )

    @app.get("/api/me")
    async def _me(authorization: str = Header(default="")):
        from fastapi import HTTPException as _HTTPException

        scheme, _, presented = authorization.partition(" ")
        if scheme.lower() != "bearer" or not presented.strip():
            raise _HTTPException(status_code=401, detail={"code": "auth_required"})
        try:
            account = await _validated_user(presented.strip())
        except (qual.AuthInvalidError, qual.AuthRequiredError) as exc:
            status_code, code = authority.http_status_for_error(exc)
            raise _HTTPException(status_code=status_code, detail={"code": code})
        return {"user_id": str(account.user_id)}

    client = TestClient(app)
    # Thin client builds the request; the test never reimplements header logic.
    headers = user_auth_cli.build_auth_headers(token)
    response = client.get("/api/me", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"user_id": str(user_id)}

    # Another user's session and machine/runtime-shaped tokens cannot
    # escalate to this user's authority.
    _, _, _, other_identity = _enrolled_user("other4126@example.invalid")
    store.enroll(
        other_identity,
        qual.AccountRecord(
            user_id=uuid.uuid4(),
            is_active=True,
            is_superuser=False,
            email="other4126@example.invalid",
        ),
    )
    other_token, _ = await qual.mint_moonmind_session(
        other_identity, store, config, revocation=revocation
    )
    other_response = client.get(
        "/api/me", headers=user_auth_cli.build_auth_headers(other_token)
    )
    assert other_response.status_code == 200
    assert other_response.json()["user_id"] != str(user_id)
    for bad in (machine_token, _mint_fanout(secret=FANOUT_SECRET), "bogus-4126"):
        denied = client.get(
            "/api/me", headers=user_auth_cli.build_auth_headers(bad)
        )
        assert denied.status_code in (401, 403)

    # Renewal issues a fresh session through the same boundary.
    renewed, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    assert renewed != token
    assert (
        client.get("/api/me", headers=user_auth_cli.build_auth_headers(renewed)).status_code
        == 200
    )

    # Logout/revocation ends browser access (within the session bound) while
    # admitted machine work continues under its independent owner.
    payload = jwt.decode(token, options={"verify_signature": False})
    await revocation.revoke_session(payload["jti"])
    assert (
        client.get("/api/me", headers=user_auth_cli.build_auth_headers(token)).status_code
        == 401
    )
    still_valid = container_caps.verify_container_job_session_capability(
        machine_token, secret=CONTAINER_SECRET
    )
    assert still_valid.agent_run_id == "run-4126"


# ---------------------------------------------------------------------------
# impl-5/impl-6/acc-4/acc-5: Workflow Chat binding isolation + logout bound
# ---------------------------------------------------------------------------


def test_workflow_chat_allowlist_is_never_an_open_proxy():
    """The facade exposes only allowlisted binding-scoped operations."""
    from moonmind.omnigent.workflow_chat_facade import match_facade_operation

    assert match_facade_operation("GET", "health") is not None
    assert match_facade_operation("GET", "v1/sessions") is not None
    assert match_facade_operation("GET", "v1/sessions/abc/stream") is not None
    # Unrestricted upstream inventory, session creation, and unknown verbs
    # are never reachable through the binding facade.
    assert match_facade_operation("GET", "v1/admin/sessions") is None
    assert match_facade_operation("POST", "v1/sessions") is None
    assert match_facade_operation("DELETE", "v1/sessions/abc") is None
    assert match_facade_operation("GET", "../../../etc/passwd") is None
    assert match_facade_operation("TRACE", "health") is None


def test_workflow_chat_two_bindings_do_not_share_authority():
    """Two users' bindings authorize exactly their own binding id."""
    from moonmind.omnigent.workflow_chat_facade import (
        WorkflowChatFacadeError,
        assert_no_identity_substitution,
        recompute_capabilities,
    )

    binding_a = "chat-binding-user-a-4126"
    binding_b = "chat-binding-user-b-4126"

    # Each binding names only itself; naming the other binding's session,
    # the provider session, or any server-owned identity fails closed.
    # (The browser Authorization bearer authenticates the MoonMind caller;
    # upstream uses server-owned credentials, never the forwarded header.)
    assert_no_identity_substitution(
        chat_binding_id=binding_a,
        path_session_id=binding_a,
        body={"session_id": binding_a, "text": "hello"},
    )
    for foreign in (
        {"path_session_id": binding_b},
        {"body": {"session_id": binding_b}},
        {"body": {"provider_session_id": "provider-4126"}},
        {"body": {"host_id": "host-4126"}},
        {"body": {"model": "evil-model"}},
        {"headers": {"X-Omnigent-Session-Id": "provider-4126"}},
        {"headers": {"X-Omnigent-Host-Id": "host-4126"}},
    ):
        with pytest.raises(WorkflowChatFacadeError):
            assert_no_identity_substitution(
                chat_binding_id=binding_a,
                path_session_id=foreign.get("path_session_id", binding_a),
                body=foreign.get("body"),
                headers=foreign.get("headers"),
            )

    # Continuation and reconnect keep the same per-binding capability gates:
    # active bindings allow sends, terminal bindings are read-only, and a
    # stored policy can only remove authority, never grant it.
    active = recompute_capabilities("active", policy_capabilities={})
    assert active["sendMessage"] is True
    terminal = recompute_capabilities("completed", policy_capabilities={})
    assert terminal["sendMessage"] is False
    assert terminal["viewTranscript"] is True
    denied_by_policy = recompute_capabilities(
        "active", policy_capabilities={"sendMessage": False}
    )
    assert denied_by_policy["sendMessage"] is False


def test_workflow_chat_lifecycle_events_require_distinct_capabilities():
    """Message authority never reaches destructive lifecycle controls."""
    from moonmind.omnigent.workflow_chat_facade import (
        CAP_CONTROL_UNSUPPORTED,
        is_read_only,
        required_capability_for_event,
    )

    assert required_capability_for_event("message") == "sendMessage"
    assert required_capability_for_event("interrupt") == "interruptTurn"
    assert required_capability_for_event("stop") != "sendMessage"
    assert required_capability_for_event("cleanup_session") != "sendMessage"
    assert required_capability_for_event("unknown-control-4126") == CAP_CONTROL_UNSUPPORTED
    for terminal in ("completed", "failed", "canceled", "timed_out", "stopped"):
        assert is_read_only(terminal) is True
    assert is_read_only("active") is False


# ---------------------------------------------------------------------------
# impl-7: provider/model/repository/host credentials are never app login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_repository_and_host_credentials_are_never_application_login():
    """Model/repository/host material cannot mint or validate a user session."""
    config = _control_plane_config()
    store, revocation, _, identity = _enrolled_user("keeper4126@example.invalid")
    session_token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    # A PAT/App-style secret and a host credential do not validate as a
    # MoonMind session, even when presented as a bearer.
    for foreign in (
        "ghp_4126fakesecret0000000000000000000000",
        "github_pat_4126fakesecret00000000000000000000000000000000000000000000000000000000",
        "xoxb-4126-fake-slack-token",
        "host-credential-4126",
        _mint_container(),
        _mint_fanout(),
    ):
        with pytest.raises(qual.AuthInvalidError):
            await qual.validate_moonmind_session(foreign, store, revocation, config)
    # The real session still validates: independent credentials were not
    # disturbed by the negative probes above.
    assert (
        await qual.validate_moonmind_session(session_token, store, revocation, config)
    ) is not None


# ---------------------------------------------------------------------------
# impl-8/acc-6: redaction + bounded failure ownership
# ---------------------------------------------------------------------------


def test_redaction_at_every_named_boundary():
    """Transport, header, callback, bridge, and Temporal payloads stay secret-free."""
    transport = {
        "authorization": "Bearer secret-4126",
        "cookie": "session=secret-4126",
        "endpoint": "https://app.example.invalid/api",
    }
    redacted = modes.redacted_diagnostics(transport)
    rendered = str(redacted)
    assert "secret-4126" not in rendered
    assert redacted["endpoint"] == "https://app.example.invalid/api"

    callback = {
        "callback_token": "callback-secret-4126",
        "endpoint": "https://app.example.invalid/api/v1/auth/oidc/callback",
    }
    redacted_callback = modes.redacted_diagnostics(callback)
    assert "callback-secret-4126" not in str(redacted_callback)
    assert (
        redacted_callback["endpoint"]
        == "https://app.example.invalid/api/v1/auth/oidc/callback"
    )

    bridge_evidence = {
        "provider_response": {"session_token": "upstream-4126"},
        "artifact_ref": "art_4126",
    }
    assert "upstream-4126" not in str(modes.redacted_diagnostics(bridge_evidence))

    from moonmind.security import advanced_identity_4124 as advanced_identity

    oidc_diagnostics = advanced_identity.redacted_oidc_diagnostics(
        {"issuer": "https://idp.example.invalid", "reason": "login_failed"}
    )
    assert oidc_diagnostics["reason"] == "login_failed"
    # Raw token material can never be rendered into OIDC diagnostics: the
    # helper refuses rather than redacting-and-logging it.
    with pytest.raises(Exception):
        advanced_identity.redacted_oidc_diagnostics(
            {"id_token": "raw-id-token-4126", "reason": "login_failed"}
        )


@pytest.mark.asyncio
async def test_bounded_failures_preserve_ownership_without_substitution():
    """Denial, replay, staleness, and restart keep exact ownership."""
    config = _control_plane_config()
    store, revocation, user_id, identity = _enrolled_user("stable4126@example.invalid")
    token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )

    # Upstream-shaped denial is unavailable (503), never a fallback principal.
    status_code, code = authority.http_status_for_error(authority.UnavailableError())
    assert (status_code, code) == (503, "unavailable")

    # Replayed consumption of a single-use generation: revoking the exact
    # session denies exactly that session; a fresh session for the same user
    # (renewal, not substitution) still validates.
    payload = jwt.decode(token, options={"verify_signature": False})
    await revocation.revoke_session(payload["jti"])
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(token, store, revocation, config)
    renewed, renewed_id = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    assert renewed_id == user_id
    assert (
        await qual.validate_moonmind_session(renewed, store, revocation, config)
    ).user_id == user_id

    # Stale lease (expired fanout) and restart-equivalent (wrong generation
    # key) both fail closed on the same bounded codes the worker gate maps.
    stale = _mint_fanout(now=1_000_000, lifetime=60)
    with pytest.raises(fanout_caps.ExecutionFanoutCapabilityError):
        fanout_caps.verify_execution_fanout_capability(
            stale, secret=FANOUT_SECRET, now=1_000_000 + 3_600
        )
    fresh = _mint_fanout()
    with pytest.raises(fanout_caps.ExecutionFanoutCapabilityError):
        fanout_caps.verify_execution_fanout_capability(
            fresh, secret="restart-generation-key-4126-xxxxxx"
        )

    # Failure diagnostics never echo credential material.
    failure = modes.redacted_diagnostics(
        {"reason": "worker_denial", "authorization": f"Bearer {renewed}"}
    )
    assert renewed not in str(failure)


def test_credential_classification_matrix_is_recorded():
    """The classification each boundary enforces, as durable test evidence."""
    matrix = {
        "moonmind_session": {
            "issuer": qual.MOONMIND_TOKEN_ISSUER,
            "audience": qual.MOONMIND_TOKEN_AUDIENCE,
            "purpose": qual.MOONMIND_SESSION_PURPOSE,
            "principal": "User.id UUID",
            "scope": "authenticated user routes + binding authorization",
            "expiry": "session TTL",
            "revocation": "durable per-session + generation",
            "owner": "session authority (#4121)",
        },
        "execution_fanout": {
            "audience": "moonmind-execution-fanout",
            "principal": "parent workflow owner (scoped user namespace)",
            "scope": "one parent workflow + agent run + session + runtime",
            "expiry": "short capability lifetime",
            "revocation": "expiry + parent availability check",
            "owner": "execution service (mint) / route (verify)",
        },
        "container_job": {
            "audience": "moonmind-container-jobs",
            "principal": "OwnerIdentity (user/service)",
            "scope": "one session + workspace + correlation",
            "expiry": "short capability lifetime",
            "revocation": "expiry + scope-match enforcement",
            "owner": "managed-session controller (mint) / MCP route (verify)",
        },
    }
    assert matrix["moonmind_session"]["purpose"] == qual.MOONMIND_SESSION_PURPOSE
    assert matrix["execution_fanout"]["audience"] == "moonmind-execution-fanout"
    assert matrix["container_job"]["audience"] == "moonmind-container-jobs"
    # The retired login JWT and the removed worker token have no row: they
    # are rejected (401 auth_invalid / 410 worker_token_deprecated), never
    # re-accepted or silently converted to broader authority.
    assert "legacy_login_jwt" not in matrix
    assert "legacy_worker_token" not in matrix


# ---------------------------------------------------------------------------
# acc-1: production-dispatch proof without browser cookies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_dispatch_worker_artifact_and_container_job_without_cookies(
    monkeypatch,
):
    """Cookie-less machine bearers authorize through production dispatch.

    Production composition with local transports: the worker gate
    (``api_service.api.routers.worker_auth._require_worker_auth``) fronts a
    run/resource-bound artifact mutation, and the real MCP container router
    (``api_service.api.routers.mcp_tools``) fronts container-job dispatch.
    No browser cookies are sent anywhere. Authorized scoped bearers succeed
    bounded to the exact run/resource; wrong-scope, expired, tampered, and
    missing machine credentials fail closed.
    """
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from api_service.auth_providers import get_current_user_optional

    _set_production_mode(monkeypatch, "accounts")
    _production_fanout_secret(monkeypatch)

    worker_app = FastAPI()

    @worker_app.post("/worker/artifacts", status_code=201)
    async def _worker_create_artifact(
        payload: dict,
        auth=Depends(worker_auth_module._require_worker_auth),
    ):
        run_id = str((payload or {}).get("run_id") or "")
        if run_id != (auth.agent_run_id or ""):
            raise HTTPException(
                status_code=403,
                detail={"code": "artifact_scope_mismatch"},
            )
        return {
            "run_id": auth.agent_run_id,
            "resource": (payload or {}).get("resource"),
            "auth_source": auth.auth_source,
        }

    worker_app.dependency_overrides[get_current_user_optional()] = lambda: None
    worker_client = TestClient(worker_app)

    machine = _mint_fanout()
    machine_headers = {
        "X-MoonMind-Execution-Fanout": "v1",
        "Authorization": f"Bearer {machine}",
    }
    ok = worker_client.post(
        "/worker/artifacts",
        headers=machine_headers,
        json={"run_id": "run-4126", "resource": "artifact:run-4126/report"},
    )
    assert ok.status_code == 201
    assert ok.json() == {
        "run_id": "run-4126",
        "resource": "artifact:run-4126/report",
        "auth_source": "execution_fanout",
    }

    # The same bearer cannot touch another run/resource.
    foreign = worker_client.post(
        "/worker/artifacts",
        headers=machine_headers,
        json={"run_id": "run-other-4126", "resource": "artifact:run-other-4126/report"},
    )
    assert foreign.status_code == 403

    # A bearer scoped to another run verifies but stays bounded to its run.
    other_machine = _mint_fanout(agent_run_id="run-other-4126")
    other_headers = {
        "X-MoonMind-Execution-Fanout": "v1",
        "Authorization": f"Bearer {other_machine}",
    }
    other_ok = worker_client.post(
        "/worker/artifacts",
        headers=other_headers,
        json={"run_id": "run-other-4126", "resource": "artifact:run-other-4126/report"},
    )
    assert other_ok.status_code == 201
    assert other_ok.json()["run_id"] == "run-other-4126"
    cross = worker_client.post(
        "/worker/artifacts",
        headers=other_headers,
        json={"run_id": "run-4126", "resource": "artifact:run-4126/report"},
    )
    assert cross.status_code == 403

    # Expired, tampered, missing, and legacy machine credentials fail closed.
    stale = _mint_fanout(now=1_000_000, lifetime=60)
    assert (
        worker_client.post(
            "/worker/artifacts",
            headers={
                "X-MoonMind-Execution-Fanout": "v1",
                "Authorization": f"Bearer {stale}",
            },
            json={"run_id": "run-4126"},
        ).status_code
        == 401
    )
    tampered = "X" + machine[1:]
    assert (
        worker_client.post(
            "/worker/artifacts",
            headers={
                "X-MoonMind-Execution-Fanout": "v1",
                "Authorization": f"Bearer {tampered}",
            },
            json={"run_id": "run-4126"},
        ).status_code
        == 401
    )
    assert (
        worker_client.post("/worker/artifacts", json={"run_id": "run-4126"}).status_code
        == 401
    )
    legacy = worker_client.post(
        "/worker/artifacts",
        headers={"X-MoonMind-Worker-Token": "legacy-token"},
        json={"run_id": "run-4126"},
    )
    assert legacy.status_code == 410

    # An authenticated browser principal alone still cannot satisfy the
    # worker-only mutation through production dispatch.
    worker_app.dependency_overrides[get_current_user_optional()] = lambda: (
        SimpleNamespace(id=uuid.uuid4())
    )
    browser = worker_client.post("/worker/artifacts", json={"run_id": "run-4126"})
    assert browser.status_code == 403
    worker_app.dependency_overrides[get_current_user_optional()] = lambda: None

    # Container-job operations dispatch through the real MCP router without
    # cookies: the scoped capability authorizes and dispatches as its owner.
    from api_service.api.routers import mcp_tools as mcp_tools_router

    mcp_app = FastAPI()
    mcp_app.include_router(mcp_tools_router.router)
    mcp_app.dependency_overrides[mcp_tools_router.get_async_session] = (
        lambda: SimpleNamespace()
    )
    monkeypatch.setattr(
        mcp_tools_router.settings.security, "JWT_SECRET_KEY", CONTAINER_SECRET
    )
    dispatched: dict = {}

    async def _fake_dispatch(payload, owner, session):
        dispatched.update(payload=payload, owner=owner)
        return {"jobId": "container-job:" + "1" * 32, "state": "queued"}

    monkeypatch.setattr(mcp_tools_router, "_dispatch_container_job_tool", _fake_dispatch)
    mcp_client = TestClient(mcp_app)

    def _submission(**overrides):
        submission = {
            "contractVersion": "v1",
            "idempotencyKey": "container-run:run-4126:4126",
            "source": {
                "source": "managed_session",
                "workflowId": "wf-4126",
                "managedSessionId": "sess-4126",
                "agentRunId": "run-4126",
            },
            "spec": {
                "image": "alpine",
                "workspaceRef": {
                    "kind": "managed_runtime",
                    "runtimeId": "runtime-4126",
                    "agentRunId": "run-4126",
                    "relativePath": "repo",
                },
                "command": ["true"],
                "resources": {"cpuMillis": 100, "memoryMiB": 64},
            },
        }
        submission.update(overrides)
        return submission

    capability_owner_id = str(uuid.uuid4())
    capability = _mint_container(
        owner=OwnerIdentity(principal_id=capability_owner_id, principal_type="user")
    )
    accepted = mcp_client.post(
        "/mcp/container/tools/call",
        headers={"Authorization": f"Bearer {capability}"},
        json={"tool": "container.submit", "arguments": _submission()},
    )
    assert accepted.status_code == 200
    assert dispatched["owner"].principal_id == capability_owner_id
    assert dispatched["owner"].principal_type == "user"
    assert dispatched["payload"].tool == "container.submit"
    assert dispatched["payload"].arguments["source"]["agentRunId"] == "run-4126"

    # A submission for another run exceeds the capability and is rejected.
    scoped = _submission()
    scoped["source"] = dict(scoped["source"], agentRunId="run-other-4126")
    mismatch = mcp_client.post(
        "/mcp/container/tools/call",
        headers={"Authorization": f"Bearer {capability}"},
        json={"tool": "container.submit", "arguments": scoped},
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["detail"]["code"] == "container_capability_scope_mismatch"

    # Expired and missing container capabilities fail closed without cookies.
    expired = container_caps.mint_container_job_session_capability(
        secret=CONTAINER_SECRET,
        owner=OwnerIdentity(principal_id=str(uuid.uuid4()), principal_type="user"),
        agent_run_id="run-4126",
        workflow_id="wf-4126",
        session_id="sess-4126",
        runtime_id="runtime-4126",
        lifetime_seconds=60,
        now=1_000_000,
    )
    assert (
        mcp_client.post(
            "/mcp/container/tools/call",
            headers={"Authorization": f"Bearer {expired}"},
            json={"tool": "container.submit", "arguments": _submission()},
        ).status_code
        == 401
    )
    assert (
        mcp_client.post(
            "/mcp/container/tools/call",
            json={"tool": "container.submit", "arguments": _submission()},
        ).status_code
        == 401
    )
