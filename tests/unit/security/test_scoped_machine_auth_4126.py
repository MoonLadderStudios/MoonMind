"""Preservation tests for #4126 scoped worker, MCP, CLI, and chat boundaries.

MoonLadderStudios/MoonMind#4126 (parent #4116). Hermetic coverage for the
six acceptance criteria through real composition (no live providers):

* acceptance-1: machine artifact/worker + MCP/container-job operations
  without browser cookies, bounded to the correct run/resource;
* acceptance-2: expired/revoked/wrong-scope tokens, another user's
  session, runtime JWTs, and conflicting credentials cannot escalate;
* acceptance-3: surviving user API/CLI authentication and renewal via the
  thin conformance client (no duplicate CLI business logic);
* acceptance-4: two users' Workflow Chat first-message, continuation, and
  reconnect paths authorize the exact binding;
* acceptance-5: browser logout/revocation ends access while admitted
  machine work continues under its independent owner;
* acceptance-6: upstream denial/outage, replayed callback, stale lease,
  and restart produce bounded safe outcomes with no substitution or leak.
"""

from __future__ import annotations

import secrets
import time
import uuid
from types import SimpleNamespace

import pytest

from moonmind.security import omnigent_auth_qualification as q
from moonmind.security import scoped_machine_auth_4126 as m
from moonmind.security.container_job_capabilities import (
    mint_container_job_session_capability,
    verify_container_job_session_capability,
)
from moonmind.security.scoped_machine_auth_4126 import (
    InMemoryWorkerRevocationStore,
    MoonmindUserClient,
    UserClientConfig,
    UserClientError,
)
from moonmind.omnigent.workflow_chat_facade import (
    assert_no_identity_substitution,
    match_facade_operation,
)


def _strong_secret() -> str:
    # 32+ bytes of key material as a str.
    return secrets.token_hex(32)


def _worker_token(**overrides):
    params = {
        "secret": _strong_secret(),
        "owner_principal": "user:owner-1",
        "workflow_id": "wf-1",
        "run_id": "run-1",
        "session_id": "sess-1",
        "host_id": "host-1",
        "resource": "artifact:run-1/report",
        "operations": ("artifact.write", "container.submit"),
        "lifetime_seconds": 3600,
    }
    params.update(overrides)
    secret = params.pop("secret")
    return m.mint_scoped_worker_capability(secret=secret, **params), secret


# ---------------------------------------------------------------------------
# acceptance-1: machine work without browser cookies, bounded to run/resource
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_machine_without_cookies_bounded_to_scope():
    token, secret = _worker_token()
    revocation = InMemoryWorkerRevocationStore()
    resolution = await m.resolve_machine_or_user(
        worker_token=token, user_id=None, secret=secret, revocation=revocation
    )
    assert resolution.kind == "machine"
    assert resolution.owner_principal == "user:owner-1"
    capability = await m.verify_scoped_worker_capability(
        token,
        secret=secret,
        revocation=revocation,
        require_operation="artifact.write",
        require_workflow_id="wf-1",
        require_resource="artifact:run-1/report",
    )
    assert capability.workflow_id == "wf-1"
    assert capability.run_id == "run-1"


@pytest.mark.asyncio
async def test_container_capability_machine_path_without_cookies():
    from moonmind.schemas.container_job_models import OwnerIdentity

    secret = _strong_secret()
    owner = OwnerIdentity(principalId="owner-1", principalType="user")
    token = mint_container_job_session_capability(
        secret=secret,
        owner=owner,
        agent_run_id="run-9",
        workflow_id="wf-9",
        session_id="sess-9",
        runtime_id="rt-9",
        lifetime_seconds=600,
    )

    def _verify(raw: str):
        return verify_container_job_session_capability(raw, secret=secret)

    caller = await m.resolve_container_job_caller(
        user=None, authorization=f"Bearer {token}", verify_capability=_verify
    )
    assert caller.kind == "machine"
    assert caller.owner.principal_id == "owner-1"


@pytest.mark.asyncio
async def test_browser_user_without_machine_token_still_resolves():
    revocation = InMemoryWorkerRevocationStore()
    resolution = await m.resolve_machine_or_user(
        worker_token=None,
        user_id="owner-1",
        secret=_strong_secret(),
        revocation=revocation,
    )
    assert resolution.kind == "user"


# ---------------------------------------------------------------------------
# acceptance-2: negative matrix cannot escalate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_worker_token_rejected():
    secret = _strong_secret()
    token = m.mint_scoped_worker_capability(
        secret=secret,
        owner_principal="user:owner-1",
        workflow_id="wf-1",
        operations=("artifact.write",),
        lifetime_seconds=60,
        now=int(time.time()) - 3600,
    )
    with pytest.raises(m.ScopedWorkerAuthError) as exc_info:
        await m.verify_scoped_worker_capability(
            token, secret=secret, revocation=InMemoryWorkerRevocationStore()
        )
    assert exc_info.value.code == "auth_invalid"


@pytest.mark.asyncio
async def test_revoked_worker_token_rejected():
    token, secret = _worker_token()
    revocation = InMemoryWorkerRevocationStore()
    capability = await m.verify_scoped_worker_capability(
        token, secret=secret, revocation=revocation
    )
    await revocation.revoke_token(capability.token_id)
    with pytest.raises(m.ScopedWorkerAuthError) as exc_info:
        await m.verify_scoped_worker_capability(
            token, secret=secret, revocation=revocation
        )
    assert exc_info.value.code == "auth_invalid"


@pytest.mark.asyncio
async def test_wrong_scope_worker_token_rejected():
    token, secret = _worker_token(resource="artifact:run-1/report")
    revocation = InMemoryWorkerRevocationStore()
    with pytest.raises(m.ScopedWorkerAuthError):
        await m.verify_scoped_worker_capability(
            token, secret=secret, revocation=revocation, require_resource="artifact:run-2/other"
        )
    with pytest.raises(m.ScopedWorkerAuthError):
        await m.verify_scoped_worker_capability(
            token, secret=secret, revocation=revocation, require_operation="admin.delete"
        )
    with pytest.raises(m.ScopedWorkerAuthError):
        await m.verify_scoped_worker_capability(
            token, secret=secret, revocation=revocation, require_workflow_id="wf-other"
        )


@pytest.mark.asyncio
async def test_legacy_unstructured_token_rejected_not_accepted():
    revocation = InMemoryWorkerRevocationStore()
    with pytest.raises(m.ScopedWorkerAuthError) as exc_info:
        await m.resolve_machine_or_user(
            worker_token="legacy-token",
            user_id=None,
            secret=_strong_secret(),
            revocation=revocation,
        )
    assert exc_info.value.code == "auth_invalid"


@pytest.mark.asyncio
async def test_runtime_jwt_presented_as_worker_token_rejected():
    import jwt as _jwt

    runtime_token = _jwt.encode(
        {"sub": "someone", "aud": "other", "exp": int(time.time()) + 600},
        "not-the-worker-secret-at-all-0123456789",
        algorithm="HS256",
    )
    with pytest.raises(m.ScopedWorkerAuthError) as exc_info:
        await m.verify_scoped_worker_capability(
            runtime_token,
            secret=_strong_secret(),
            revocation=InMemoryWorkerRevocationStore(),
        )
    assert exc_info.value.code == "auth_invalid"


@pytest.mark.asyncio
async def test_conflicting_worker_and_user_identities_rejected():
    token, secret = _worker_token(owner_principal="user:owner-1")
    with pytest.raises(m.ScopedWorkerAuthError) as exc_info:
        await m.resolve_machine_or_user(
            worker_token=token,
            user_id="user:owner-2",
            secret=secret,
            revocation=InMemoryWorkerRevocationStore(),
        )
    assert exc_info.value.code == "auth_conflict"


@pytest.mark.asyncio
async def test_invalid_worker_token_with_valid_user_still_rejected():
    # An invalid presented machine credential never silently becomes the
    # accompanying user session.
    with pytest.raises(m.ScopedWorkerAuthError) as exc_info:
        await m.resolve_machine_or_user(
            worker_token="bogus",
            user_id="owner-1",
            secret=_strong_secret(),
            revocation=InMemoryWorkerRevocationStore(),
        )
    assert exc_info.value.code == "auth_invalid"


@pytest.mark.asyncio
async def test_missing_all_authority_rejected():
    with pytest.raises(m.ScopedWorkerAuthError) as exc_info:
        await m.resolve_machine_or_user(
            worker_token=None,
            user_id=None,
            secret=_strong_secret(),
            revocation=InMemoryWorkerRevocationStore(),
        )
    assert exc_info.value.code == "auth_required"


@pytest.mark.asyncio
async def test_malformed_container_credential_is_invalid_not_missing():
    async def _never(_token: str):
        from moonmind.security.container_job_capabilities import (
            ContainerJobCapabilityError,
        )

        raise ContainerJobCapabilityError("invalid container-job capability")

    with pytest.raises(m.ScopedWorkerAuthError) as exc_info:
        await m.resolve_container_job_caller(
            user=None, authorization="Basic bm90LWEtYmVhcmVy", verify_capability=_never
        )
    assert exc_info.value.code == "auth_invalid"
    with pytest.raises(m.ScopedWorkerAuthError) as exc_info:
        await m.resolve_container_job_caller(
            user=None, authorization=None, verify_capability=_never
        )
    assert exc_info.value.code == "auth_required"


@pytest.mark.asyncio
async def test_conflicting_container_caller_rejected():
    from moonmind.schemas.container_job_models import OwnerIdentity

    secret = _strong_secret()
    owner = OwnerIdentity(principalId="owner-1", principalType="user")
    token = mint_container_job_session_capability(
        secret=secret,
        owner=owner,
        agent_run_id="run-1",
        workflow_id="wf-1",
        session_id="sess-1",
        runtime_id="rt-1",
        lifetime_seconds=600,
    )

    def _verify(raw: str):
        return verify_container_job_session_capability(raw, secret=secret)

    other_user = SimpleNamespace(id="owner-2")
    with pytest.raises(m.ScopedWorkerAuthError) as exc_info:
        await m.resolve_container_job_caller(
            user=other_user, authorization=f"Bearer {token}", verify_capability=_verify
        )
    assert exc_info.value.code == "auth_conflict"


# ---------------------------------------------------------------------------
# acceptance-3: thin user CLI auth contract
# ---------------------------------------------------------------------------


def _session_config(**overrides):
    base = {
        "mode": "accounts",
        "cookie_name": q.MOONMIND_DEV_COOKIE,
        "cookie_secret": secrets.token_bytes(32),
        "session_ttl_seconds": 3600,
        "require_secure_cookies": False,
    }
    base.update(overrides)
    return q.MoonmindAuthConfig(**base)


@pytest.mark.asyncio
async def test_thin_client_acquire_validate_renew_hermetic():
    config = _session_config()
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = q.ValidatedIdentity(issuer="moonmind-accounts", subject="alice")
    account = q.AccountRecord(user_id=uuid.uuid4(), is_active=True)
    store.enroll(identity, account)

    token, _ = await q.mint_moonmind_session(identity, store, config)
    client = MoonmindUserClient(
        UserClientConfig(base_url="http://127.0.0.1:7000", token_env_var="MOONMIND_SESSION_TOKEN")
    )
    # Acquisition from an explicit environment entry (never argv/URL/cache).
    loaded = client.load_token(environ={"MOONMIND_SESSION_TOKEN": token})
    assert loaded == token
    client.assert_no_argv_secret(["moonmind", "status"], loaded)
    client.assert_same_origin("http://127.0.0.1:7000/api/v1/status")
    client.assert_no_token_in_url("http://127.0.0.1:7000/api/v1/status")
    # Real production validation through the qualified primitives.
    resolved = await client.validate(token, account_store=store, revocation=revocation, config=config)
    assert resolved.user_id == account.user_id
    # Renewal issues a distinct token through explicit server authorization.
    async def _renew(old: str) -> str:
        renewed, _ = await q.mint_moonmind_session(identity, store, config)
        assert renewed != old
        return renewed

    renewed = await client.renew(token, renew_fn=_renew)
    assert renewed != token
    resolved_renewed = await client.validate(
        renewed, account_store=store, revocation=revocation, config=config
    )
    assert resolved_renewed.user_id == account.user_id


def test_thin_client_rejects_global_cache_argv_url_cross_host():
    client = MoonmindUserClient(UserClientConfig(base_url="http://127.0.0.1:7000"))
    with pytest.raises(UserClientError):
        MoonmindUserClient(
            UserClientConfig(
                base_url="http://127.0.0.1:7000",
                token_file="~/.moonmind/session.json",
            )
        ).load_token(environ={}, read_file=lambda: "x")
    with pytest.raises(UserClientError):
        client.assert_no_argv_secret(["moonmind", "--token", "sekret-token"], "sekret-token")
    with pytest.raises(UserClientError):
        client.assert_no_token_in_url("http://127.0.0.1:7000/api?token=sekret-token")
    with pytest.raises(UserClientError):
        client.assert_same_origin("http://evil.example:7000/api/v1/status")
    with pytest.raises(UserClientError):
        client.exchange_refresh_token("refresh-material")
    with pytest.raises(UserClientError):
        client.exchange_delegated_token("delegated-material")


# ---------------------------------------------------------------------------
# acceptance-4: two-user chat binding isolation
# ---------------------------------------------------------------------------


def test_two_users_chat_binding_isolation_first_continue_reconnect():
    binding_a = m.authorize_workflow_chat_binding(
        caller_user_id="user-a", binding_owner_id="user-a", binding_id="binding-a"
    )
    binding_b = m.authorize_workflow_chat_binding(
        caller_user_id="user-b", binding_owner_id="user-b", binding_id="binding-b"
    )
    assert binding_a == "binding-a"
    assert binding_b == "binding-b"
    # Cross-user access to the exact binding is denied without enumeration.
    with pytest.raises(m.ScopedWorkerAuthError):
        m.authorize_workflow_chat_binding(
            caller_user_id="user-b", binding_owner_id="user-a", binding_id="binding-a"
        )
    with pytest.raises(m.ScopedWorkerAuthError):
        m.authorize_workflow_chat_binding(
            caller_user_id="user-a", binding_owner_id="user-b", binding_id="binding-b"
        )
    # First message, continuation, and reconnect all resolve through the same
    # allowlisted facade operations on the bound session only.
    for path in (
        "v1/sessions/binding-a/events",
        "v1/sessions/binding-a/stream",
        "v1/sessions/binding-a",
    ):
        method = "POST" if path.endswith("/events") else "GET"
        matched = match_facade_operation(method, path)
        assert matched is not None
    # A path naming another binding's session is a substitution attempt.
    with pytest.raises(Exception):
        assert_no_identity_substitution(
            chat_binding_id="binding-a",
            path_session_id="binding-b",
            body={"message": "hi"},
        )
    # Browser-supplied upstream ownership is never trusted.
    with pytest.raises(Exception):
        assert_no_identity_substitution(
            chat_binding_id="binding-a",
            path_session_id="binding-a",
            body={"provider_session_id": "upstream-sess", "message": "hi"},
        )


def test_chat_forward_uses_server_credential_only():
    forwarded = m.sanitize_upstream_forward(
        {"Content-Type": "application/json", "Cookie": "mm_session=x", "Authorization": "Bearer browser"},
        server_credential="server-owned-upstream-token",
    )
    assert forwarded["Authorization"] == "Bearer server-owned-upstream-token"
    assert "Cookie" not in forwarded
    with pytest.raises(m.ScopedWorkerAuthError):
        m.sanitize_upstream_forward(
            {"X-Omnigent-Session": "upstream-sess"},
            server_credential="server-owned-upstream-token",
        )


# ---------------------------------------------------------------------------
# acceptance-5: logout bound, admitted work continues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_ends_browser_access_while_machine_continues():
    config = _session_config()
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = q.ValidatedIdentity(issuer="moonmind-accounts", subject="alice")
    account = q.AccountRecord(user_id=uuid.uuid4(), is_active=True)
    store.enroll(identity, account)
    token, _ = await q.mint_moonmind_session(identity, store, config, revocation=revocation)

    import jwt as _jwt

    payload = _jwt.decode(token, options={"verify_signature": False})
    # Browser logout revokes the browser session only.
    await revocation.revoke_session(payload["jti"])
    with pytest.raises(q.AuthInvalidError):
        await q.validate_moonmind_session(token, store, revocation, config)

    # Admitted machine work continues under its independent owner.
    worker_token, worker_secret = _worker_token(owner_principal="user:owner-1")
    worker_revocation = InMemoryWorkerRevocationStore()
    resolution = await m.resolve_machine_or_user(
        worker_token=worker_token,
        user_id=None,
        secret=worker_secret,
        revocation=worker_revocation,
    )
    assert resolution.kind == "machine"
    # Browser logout did not revoke the independent worker credential.
    assert await worker_revocation.is_token_revoked(resolution.capability.token_id) is False


# ---------------------------------------------------------------------------
# acceptance-6: bounded failure outcomes, no substitution or leakage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_lease_generation_rejected():
    secret = _strong_secret()
    revocation = InMemoryWorkerRevocationStore()
    token = m.mint_scoped_worker_capability(
        secret=secret,
        owner_principal="user:owner-1",
        workflow_id="wf-1",
        operations=("artifact.write",),
        lifetime_seconds=3600,
        generation=0,
    )
    await revocation.revoke_all_for_owner("user:owner-1")
    with pytest.raises(m.ScopedWorkerAuthError) as exc_info:
        await m.verify_scoped_worker_capability(
            token, secret=secret, revocation=revocation
        )
    assert exc_info.value.code == "auth_invalid"


@pytest.mark.asyncio
async def test_replayed_callback_rejected_and_restart_bounded():
    # Replayed bearer material (same token presented twice after revocation)
    # fails closed instead of granting fresh authority.
    token, secret = _worker_token()
    revocation = InMemoryWorkerRevocationStore()
    first = await m.verify_scoped_worker_capability(
        token, secret=secret, revocation=revocation
    )
    await revocation.revoke_token(first.token_id)
    with pytest.raises(m.ScopedWorkerAuthError):
        await m.verify_scoped_worker_capability(
            token, secret=secret, revocation=revocation
        )
    # A restart with an unknown secret fails closed, never substitutes.
    with pytest.raises(m.ScopedWorkerAuthError):
        await m.verify_scoped_worker_capability(
            token,
            secret=_strong_secret(),
            revocation=InMemoryWorkerRevocationStore(),
        )


def test_redaction_at_transport_header_callback_bridge_temporal_bounds():
    secret_value = "synthetic-worker-secret-4126"
    headers = {
        "Authorization": f"Bearer {secret_value}",
        "Cookie": f"mm_session={secret_value}",
        "Content-Type": "application/json",
    }
    redacted = m.redact_headers_for_logging(headers)
    assert secret_value not in str(redacted)
    assert redacted["Content-Type"] == "application/json"
    # Bridge evidence / Temporal payloads must not carry the secret.
    from moonmind.security.session_authority_4121 import assert_no_secret_leak

    assert_no_secret_leak(
        {"binding": "binding-a", "headers": redacted, "event": "chat.denied"},
        [secret_value],
    )
    with pytest.raises(AssertionError):
        assert_no_secret_leak({"token": secret_value}, [secret_value])
    # Upstream denial surfaces one bounded code without secret material.
    event = m.emit_preservation_event(
        "denial", mode="accounts", reason="upstream_denied", binding_id="binding-a"
    )
    assert event["auth_event"] == "denial"
    assert secret_value not in str(event)


def test_credential_classification_covers_all_boundaries():
    required = {
        "credential",
        "issuer",
        "audience",
        "principal",
        "scope",
        "expiry",
        "revocation",
        "owner",
        "validator",
    }
    assert len(m.CREDENTIAL_CLASSIFICATION) == 5
    for entry in m.CREDENTIAL_CLASSIFICATION:
        assert required <= set(entry.keys()), entry
