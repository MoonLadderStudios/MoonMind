"""Unit coverage for the #4121 session authority (MoonLadderStudios/MoonMind#4121).

Proves the acceptance matrix without a database: real issuance/validation
through the composed #4118 primitives, logout/reset/disable/admin-revoke/
key-rotation semantics across two validator replicas sharing one
revocation store (incl. cached tokens and concurrent issuance), outage
fail-closed behavior, CSRF/origin + credentialed-CORS enforcement,
authority boundaries, leakage-negative assertions with synthetic secrets,
and the stream re-authorization bound.
"""

from __future__ import annotations

import secrets
import time
import uuid

import jwt
import pytest

from moonmind.security import omnigent_auth_qualification as q
from moonmind.security import session_authority_4121 as s


def _config(**overrides):
    base = {
        "mode": "accounts",
        "cookie_name": q.MOONMIND_DEV_COOKIE,
        "cookie_secret": secrets.token_bytes(32),
        "session_ttl_seconds": 3600,
        "require_secure_cookies": False,
    }
    base.update(overrides)
    return q.MoonmindAuthConfig(**base)


def _identity(subject: str = "alice", issuer: str = "moonmind-accounts", **kw):
    return q.ValidatedIdentity(issuer=issuer, subject=subject, **kw)


def _enrolled(store, identity, **kw):
    account = q.AccountRecord(user_id=uuid.uuid4(), **kw)
    store.enroll(identity, account)
    return account


# ---------------------------------------------------------------------------
# 1. Real issuance/validation matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_absent_expired_claim_matrix():
    config = _config()
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = _identity()
    account = _enrolled(store, identity, is_active=True)

    token, user_id = await q.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    assert user_id == account.user_id
    resolved = await s.resolve_session_user(
        cookie_token=token,
        bearer_token=None,
        account_store=store,
        revocation=revocation,
        config=config,
    )
    assert resolved is not None and resolved.user_id == account.user_id

    # Absent at a strict boundary.
    with pytest.raises(q.AuthRequiredError):
        await s.resolve_session_user(
            cookie_token=None,
            bearer_token=None,
            account_store=store,
            revocation=revocation,
            config=config,
        )
    # Absent at an optional boundary proceeds for separate worker auth.
    assert (
        await s.resolve_session_user(
            cookie_token=None,
            bearer_token=None,
            account_store=store,
            revocation=revocation,
            config=config,
            optional=True,
        )
        is None
    )

    # Expired.
    expired, _ = await q.mint_moonmind_session(
        identity, store, config, now=int(time.time()) - 7200, revocation=revocation
    )
    with pytest.raises(q.AuthInvalidError):
        await s.resolve_session_user(
            cookie_token=expired,
            bearer_token=None,
            account_store=store,
            revocation=revocation,
            config=config,
        )


@pytest.mark.asyncio
async def test_wrong_key_issuer_audience_purpose_missing_claims():
    secret = secrets.token_bytes(32)
    config = _config(cookie_secret=secret)
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = _identity()
    _enrolled(store, identity, is_active=True)
    token, _ = await q.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )

    # Wrong key.
    with pytest.raises(q.AuthInvalidError):
        await q.validate_moonmind_session(
            token, store, revocation, _config(cookie_secret=secrets.token_bytes(32))
        )
    # Wrong issuer / audience.
    with pytest.raises(q.AuthInvalidError):
        await q.validate_moonmind_session(
            token, store, revocation, _config(cookie_secret=secret, token_issuer="other")
        )
    with pytest.raises(q.AuthInvalidError):
        await q.validate_moonmind_session(
            token,
            store,
            revocation,
            _config(cookie_secret=secret, token_audience="other"),
        )
    # Wrong purpose: re-sign the claims with a different purpose.
    claims = jwt.decode(token, options={"verify_signature": False})
    claims["purpose"] = "other-purpose"
    repurposed = jwt.encode(claims, secret, algorithm="HS256")
    with pytest.raises(q.AuthInvalidError):
        await q.validate_moonmind_session(repurposed, store, revocation, config)
    # Missing required claims.
    for missing in ("sub", "jti", "exp", "iss", "aud"):
        slim = {k: v for k, v in claims.items() if k != missing}
        slim_token = jwt.encode(slim, secret, algorithm="HS256")
        with pytest.raises(q.AuthInvalidError):
            await q.validate_moonmind_session(slim_token, store, revocation, config)
    # Malformed signatures fail closed.
    with pytest.raises(q.AuthInvalidError):
        await q.validate_moonmind_session("not-a-jwt", store, revocation, config)


def test_reserved_identities_rejected():
    with pytest.raises(q.AuthInvalidError):
        _identity(subject="local")
    with pytest.raises(q.AuthInvalidError):
        _identity(subject="__public__")


@pytest.mark.asyncio
async def test_conflicting_cookie_bearer_identities_rejected():
    config = _config()
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    alice = _enrolled(store, _identity("alice"), is_active=True)
    bob = _enrolled(store, _identity("bob"), is_active=True)
    token_a, _ = await q.mint_moonmind_session(
        _identity("alice"), store, config, revocation=revocation
    )
    token_b, _ = await q.mint_moonmind_session(
        _identity("bob"), store, config, revocation=revocation
    )
    assert alice.user_id != bob.user_id
    assert s.resolve_credential_source(token_a, token_b) == "conflict"
    with pytest.raises(q.AuthConflictError):
        await s.resolve_session_user(
            cookie_token=token_a,
            bearer_token=token_b,
            account_store=store,
            revocation=revocation,
            config=config,
        )
    status, code = s.http_status_for_error(q.AuthConflictError())
    assert (status, code) == (401, "auth_conflict")


# ---------------------------------------------------------------------------
# 2. Logout / reset / disable / admin revoke / key rotation (two replicas)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_revokes_across_replicas_including_cache():
    config = _config()
    store = q.InMemoryAsyncAccountStore()
    shared_revocation = q.InMemoryRevocationStore()
    identity = _identity()
    _enrolled(store, identity, is_active=True)
    token, _ = await q.mint_moonmind_session(
        identity, store, config, revocation=shared_revocation
    )
    replica_cache = s.BoundedSessionCache(config=config)
    replica_two_cache = s.BoundedSessionCache(config=config)
    # Both replicas validate (one through cache).
    await replica_cache.validate(token, store, shared_revocation)
    await q.validate_moonmind_session(token, store, shared_revocation, config)
    # Logout revokes the session durably.
    claims = jwt.decode(token, options={"verify_signature": False})
    await shared_revocation.revoke_session(claims["jti"])
    with pytest.raises(q.AuthInvalidError):
        await replica_cache.validate(token, store, shared_revocation)
    with pytest.raises(q.AuthInvalidError):
        await replica_two_cache.validate(token, store, shared_revocation)


@pytest.mark.asyncio
async def test_generation_bump_invalidates_sessions_and_blocks_resurrection():
    config = _config()
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = _identity()
    account = _enrolled(store, identity, is_active=True)
    stale, _ = await q.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    # Concurrent issuance captures the pre-bump generation; the reset/admin
    # bump (password reset / disable / admin revoke / rotation) wins.
    racing, _ = await q.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    new_generation = await revocation.revoke_all_for_user(account.user_id)
    assert new_generation == 1
    for doomed in (stale, racing):
        with pytest.raises(q.AuthInvalidError):
            await q.validate_moonmind_session(doomed, store, revocation, config)
    # Fresh issuance after the boundary validates.
    fresh, _ = await q.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    resolved = await q.validate_moonmind_session(fresh, store, revocation, config)
    assert resolved.user_id == account.user_id


@pytest.mark.asyncio
async def test_key_rotation_bounded_overlap():
    secret_old = secrets.token_bytes(32)
    secret_new = secrets.token_bytes(32)
    base = _config(cookie_secret=secret_old)
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = _identity()
    _enrolled(store, identity, is_active=True)
    old_token, _ = await q.mint_moonmind_session(
        identity, store, base, revocation=revocation
    )
    ring = s.SessionKeyRing(current=secret_old).rotate(
        secret_new, overlap_seconds=600
    )
    rotated_base = _config(cookie_secret=secret_new)
    # During overlap the old token still validates via the previous secret.
    resolved = await s.validate_with_key_ring(
        old_token, store, revocation, rotated_base, ring
    )
    assert resolved is not None
    # After the window the old key is refused (no indefinite acceptance).
    with pytest.raises(q.AuthInvalidError):
        await s.validate_with_key_ring(
            old_token,
            store,
            revocation,
            rotated_base,
            ring,
            now=time.time() + 3600,
        )
    # New-key tokens validate on the current secret.
    new_token, _ = await q.mint_moonmind_session(
        identity, store, rotated_base, revocation=revocation
    )
    assert (
        await s.validate_with_key_ring(
            new_token, store, revocation, rotated_base, ring
        )
    ) is not None


# ---------------------------------------------------------------------------
# 3. Outage fails closed with bounded unavailable (never an admin stub)
# ---------------------------------------------------------------------------


class _OutageRevocation(q.InMemoryRevocationStore):
    async def is_session_revoked(self, jti: str) -> bool:
        raise ConnectionError("revocation store down")

    async def generation_for_user(self, user_id) -> int:
        raise ConnectionError("revocation store down")


@pytest.mark.asyncio
async def test_store_outage_is_unavailable_not_admin():
    config = _config()
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = _identity()
    _enrolled(store, identity, is_active=True)
    token, _ = await q.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    with pytest.raises(q.UnavailableError):
        await q.validate_moonmind_session(token, store, _OutageRevocation(), config)
    status, code = s.http_status_for_error(q.UnavailableError("down"))
    assert (status, code) == (503, "unavailable")


# ---------------------------------------------------------------------------
# 4. Bounded cache: size, TTL, and hits that cannot bypass checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bounded_cache_evicts_and_honors_ttl():
    config = _config()
    cache = s.BoundedSessionCache(config=config, max_entries=2, ttl_seconds=60)
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    tokens = []
    for name in ("u1", "u2", "u3"):
        identity = _identity(name)
        _enrolled(store, identity, is_active=True)
        token, _ = await q.mint_moonmind_session(
            identity, store, config, revocation=revocation
        )
        tokens.append(token)
    for token in tokens:
        await cache.validate(token, store, revocation)
    assert cache.size <= 2
    assert cache.evictions >= 1


@pytest.mark.asyncio
async def test_cache_hit_reruns_revocation_and_status():
    config = _config()
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = _identity()
    account = _enrolled(store, identity, is_active=True)
    token, _ = await q.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    cache = s.BoundedSessionCache(config=config)
    await cache.validate(token, store, revocation)
    assert cache.misses == 1
    # Revocation between calls is effective on the next hit.
    claims = jwt.decode(token, options={"verify_signature": False})
    await revocation.revoke_session(claims["jti"])
    with pytest.raises(q.AuthInvalidError):
        await cache.validate(token, store, revocation)
    # Deactivation is likewise effective on hits.
    token2_identity = _identity("second")
    account2 = _enrolled(store, token2_identity, is_active=True)
    token2, _ = await q.mint_moonmind_session(
        token2_identity, store, config, revocation=revocation
    )
    await cache.validate(token2, store, revocation)
    store._accounts[account2.user_id] = q.AccountRecord(
        user_id=account2.user_id, is_active=False
    )
    with pytest.raises(q.ForbiddenError):
        await cache.validate(token2, store, revocation)
    assert account.user_id is not None


# ---------------------------------------------------------------------------
# 5. Cookies
# ---------------------------------------------------------------------------


def test_cookie_issue_and_clear_policy():
    prod = s.build_set_cookie_header(
        token="synthetic-session",
        cookie_name=s.MOONMIND_PROD_COOKIE,
        require_secure_cookies=True,
        max_age_seconds=3600,
    )
    assert "HttpOnly" in prod and "Secure" in prod and "SameSite" in prod
    assert f"{s.MOONMIND_PROD_COOKIE}=" in prod and "Path=/" in prod
    assert "Domain=" not in prod  # __Host- restriction
    dev = s.build_set_cookie_header(
        token="synthetic-session",
        cookie_name=s.MOONMIND_DEV_COOKIE,
        require_secure_cookies=False,
        max_age_seconds=3600,
    )
    assert s.MOONMIND_DEV_COOKIE in dev and "Secure" not in dev
    assert s.MOONMIND_DEV_COOKIE != s.MOONMIND_PROD_COOKIE
    with pytest.raises(q.AuthConfigError):
        s.build_set_cookie_header(
            token="x",
            cookie_name=s.MOONMIND_PROD_COOKIE,
            require_secure_cookies=False,
            max_age_seconds=60,
        )
    cleared = s.build_clear_cookie_header(
        cookie_name=s.MOONMIND_PROD_COOKIE, require_secure_cookies=True
    )
    assert "Max-Age=0" in cleared and "Secure" in cleared and "Path=/" in cleared
    with pytest.raises(q.AuthConfigError):
        s.assert_no_token_in_json({"session_token": "synthetic-secret-token"})


# ---------------------------------------------------------------------------
# 6. CSRF / origin / credentialed CORS
# ---------------------------------------------------------------------------


def test_csrf_origin_boundary():
    base = "https://moonmind.example.invalid"
    # Safe methods and bearer-only machine flows pass through.
    s.enforce_csrf_origin(
        method="GET",
        cookie_present=True,
        origin=None,
        referer=None,
        host="moonmind.example.invalid",
        base_url=base,
    )
    s.enforce_csrf_origin(
        method="POST",
        cookie_present=False,
        origin=None,
        referer=None,
        host=None,
        base_url=base,
    )
    # Same-origin cookie mutation is allowed.
    s.enforce_csrf_origin(
        method="POST",
        cookie_present=True,
        origin="https://moonmind.example.invalid",
        referer=None,
        host="moonmind.example.invalid",
        base_url=base,
    )
    # Cross-origin cookie mutations are rejected.
    with pytest.raises(q.AuthInvalidError):
        s.enforce_csrf_origin(
            method="POST",
            cookie_present=True,
            origin="https://evil.example.invalid",
            referer=None,
            host="moonmind.example.invalid",
            base_url=base,
        )
    # Missing origin on a cookie mutation fails closed.
    with pytest.raises(q.AuthInvalidError):
        s.enforce_csrf_origin(
            method="POST",
            cookie_present=True,
            origin=None,
            referer=None,
            host="moonmind.example.invalid",
            base_url=base,
        )


def test_credentialed_wildcard_cors_rejected():
    with pytest.raises(q.AuthConfigError):
        s.resolve_credentialed_cors_origins(["*"], allow_credentials=True)
    origins = s.resolve_credentialed_cors_origins(
        ["https://moonmind.example.invalid"], allow_credentials=True
    )
    assert origins == ["https://moonmind.example.invalid"]


# ---------------------------------------------------------------------------
# 7. Authority boundaries: runtime / delegated / worker / browser
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upstream_runtime_and_delegated_tokens_rejected():
    config = _config()
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = _identity()
    _enrolled(store, identity, is_active=True)
    # An upstream runtime-shaped token (different issuer/purpose) is never
    # a MoonMind user even when the signature verifies under some key.
    runtime = jwt.encode(
        {
            "sub": "runtime-user",
            "iss": "omnigent-runtime",
            "aud": "omnigent-runtime-clients",
            "purpose": "omnigent-runtime",
            "jti": secrets.token_hex(8),
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        config.cookie_secret,
        algorithm="HS256",
    )
    with pytest.raises(q.AuthInvalidError):
        await q.validate_moonmind_session(runtime, store, revocation, config)
    # Delegated/grant-derived shapes are rejected, not broadened.
    token, _ = await q.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    claims = jwt.decode(token, options={"verify_signature": False})
    claims["scope"] = "delegated:read"
    delegated = jwt.encode(claims, config.cookie_secret, algorithm="HS256")
    with pytest.raises(q.UnsupportedSurfaceError):
        await q.validate_moonmind_session(delegated, store, revocation, config)
    for surface in ("refresh", "delegated", "runner_token", "cli_ticket", "magic_link"):
        with pytest.raises(q.UnsupportedSurfaceError):
            q.assert_surface_not_used(surface)


def test_http_status_mapping():
    assert s.http_status_for_error(q.AuthRequiredError()) == (401, "auth_required")
    assert s.http_status_for_error(q.AuthInvalidError()) == (401, "auth_invalid")
    assert s.http_status_for_error(q.ForbiddenError()) == (403, "forbidden")
    assert s.http_status_for_error(q.UnavailableError()) == (503, "unavailable")


# ---------------------------------------------------------------------------
# 8. Observability negatives with synthetic secrets
# ---------------------------------------------------------------------------


def test_redacted_events_and_no_token_printing_hooks():
    synthetic_cookie = f"mm-cookie-{secrets.token_hex(16)}"
    synthetic_reset = f"reset-{secrets.token_hex(16)}"
    event = s.emit_auth_event(
        "denial", mode="accounts", reason="auth_invalid", request_id="req-1"
    )
    assert event["auth_event"] == "denial"
    with pytest.raises(q.AuthConfigError):
        s.emit_auth_event(
            "success",
            mode="accounts",
            reason="ok",
            extra={"session_token": synthetic_cookie},
        )
    with pytest.raises(q.AuthConfigError):
        s.emit_auth_event(
            "success",
            mode="accounts",
            reason="ok",
            extra={"note": f"issued {synthetic_reset}"},
        )
    s.assert_no_secret_leak({"user": "alice"}, [synthetic_cookie])
    with pytest.raises(AssertionError):
        s.assert_no_secret_leak({"cookie": synthetic_cookie}, [synthetic_cookie])
    # The replaced auth paths must not print token material.
    import inspect

    import api_service.auth as auth_module

    source = inspect.getsource(auth_module)
    assert "Reset token:" not in source
    assert "Verification token:" not in source
    assert "print(" not in source or "token" not in source.lower().split("print(")[-1][:200]


# ---------------------------------------------------------------------------
# 9. Stream re-authorization within the agreed bound
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_reauth_enforces_revocation_bound():
    config = _config()
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = _identity()
    account = _enrolled(store, identity, is_active=True)
    token, _ = await q.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    policy = s.StreamReauthPolicy()
    assert policy.max_reauth_interval_seconds == 5 * 60
    assert policy.reauth_due(0.0, now_epoch=301.0) is True
    assert policy.reauth_due(time.time(), now_epoch=time.time()) is False
    # Reconnect re-authorizes against live authority; ownership is unchanged.
    resolved = await s.reauthorize_stream_token(
        token, account_store=store, revocation=revocation, config=config
    )
    assert resolved.user_id == account.user_id
    await revocation.revoke_all_for_user(account.user_id)
    with pytest.raises(q.AuthInvalidError):
        await s.reauthorize_stream_token(
            token, account_store=store, revocation=revocation, config=config
        )
