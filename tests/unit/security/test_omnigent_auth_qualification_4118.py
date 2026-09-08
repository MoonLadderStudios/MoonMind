"""K2 conformance fixtures for MoonLadderStudios/MoonMind#4118.

Hermetic qualification of the portable Omnigent authentication boundary:
real upstream primitives (session-JWT machinery, argon2 passwords,
fail-closed config validation) composed in the MoonMind process with
explicit config and injected persistence. No second account database, no
remote runtime request, no private monkey-patches, no network, no DB.

Consumed by later Keycloak-removal issues (K3/K4) as the adapter
conformance contract.
"""

from __future__ import annotations

import os
import secrets
import time
import uuid

import jwt
import pytest

from moonmind.security import omnigent_auth_qualification as q

q._ensure_omnigent_bundle_on_path()


def _config(mode: str = "accounts", **overrides) -> q.MoonmindAuthConfig:
    base = {
        "mode": mode,
        "cookie_name": q.MOONMIND_DEV_COOKIE,
        "cookie_secret": secrets.token_bytes(32),
        "session_ttl_seconds": 3600,
        "require_secure_cookies": False,
    }
    base.update(overrides)
    return q.MoonmindAuthConfig(**base)  # type: ignore[arg-type]


def _identity(
    issuer: str = "moonmind-accounts",
    subject: str = "alice",
    **overrides,
) -> q.ValidatedIdentity:
    base = {"issuer": issuer, "subject": subject}
    base.update(overrides)
    return q.ValidatedIdentity(**base)  # type: ignore[arg-type]


def _enrolled(store: q.InMemoryAsyncAccountStore, identity, user_id=None, **kw):
    account = q.AccountRecord(user_id=user_id or uuid.uuid4(), **kw)
    store.enroll(identity, account)
    return account


# ---------------------------------------------------------------------------
# 1. Minimal real authentication flow in the MoonMind process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minimal_accounts_flow_with_explicit_config_and_injected_persistence():
    config = _config("accounts")
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = _identity()
    account = _enrolled(store, identity, is_active=True, is_superuser=False)

    token, user_id = await q.mint_moonmind_session(identity, store, config)
    assert user_id == account.user_id
    # Session subject is the MoonMind UUID, not the login name.
    assert jwt.decode(token, options={"verify_signature": False})["sub"] == str(account.user_id)

    resolved = await q.resolve_current_user(
        cookie_token=token,
        bearer_token=None,
        account_store=store,
        revocation=revocation,
        config=config,
    )
    assert resolved is not None and resolved.user_id == account.user_id
    # Identity hook ran before minting: exactly one store resolution call.
    assert store.login_calls == [(identity.issuer, identity.subject)]


@pytest.mark.asyncio
async def test_oidc_flow_resolves_verified_issuer_subject_before_minting():
    config = _config("oidc")
    store = q.InMemoryAsyncAccountStore()
    identity = _identity(
        issuer="https://idp.example.invalid",
        subject="UserA-123",
        email="alice@example.invalid",
    )
    account = _enrolled(store, identity)
    token, _ = await q.mint_moonmind_session(identity, store, config)
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["id_issuer"] == "https://idp.example.invalid"
    assert claims["sub"] == str(account.user_id)
    assert claims["sub"] != "alice@example.invalid"


# ---------------------------------------------------------------------------
# 2. No email/username-only UUID mapping; case-sensitive, issuer-scoped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_only_match_does_not_resolve_identity():
    config = _config("accounts")
    store = q.InMemoryAsyncAccountStore()
    enrolled_identity = _identity(subject="alice")
    account = _enrolled(store, enrolled_identity, email="alice@example.invalid")
    assert account.email == "alice@example.invalid"
    # Same email, different subject: enrollment required, not auto-linked.
    with pytest.raises(q.AuthInvalidError):
        await q.mint_moonmind_session(
            _identity(subject="alice2", email="alice@example.invalid"), store, config
        )


@pytest.mark.asyncio
async def test_same_email_across_issuers_resolves_distinct_uuids():
    config = _config("oidc")
    store = q.InMemoryAsyncAccountStore()
    id_a = _identity(issuer="https://a.example.invalid", subject="sub-1", email="x@y.invalid")
    id_b = _identity(issuer="https://b.example.invalid", subject="sub-1", email="x@y.invalid")
    acc_a = _enrolled(store, id_a)
    acc_b = _enrolled(store, id_b)
    assert acc_a.user_id != acc_b.user_id
    _, uid_a = await q.mint_moonmind_session(id_a, store, config)
    _, uid_b = await q.mint_moonmind_session(id_b, store, config)
    assert uid_a == acc_a.user_id and uid_b == acc_b.user_id


@pytest.mark.asyncio
async def test_subject_matching_is_case_sensitive():
    store = q.InMemoryAsyncAccountStore()
    config = _config("oidc")
    lower = _identity(issuer="https://idp.example.invalid", subject="usera")
    _enrolled(store, lower)
    with pytest.raises(q.AuthInvalidError):
        await q.mint_moonmind_session(
            _identity(issuer="https://idp.example.invalid", subject="UserA"), store, config
        )


def test_reserved_identities_rejected_before_minting():
    with pytest.raises(q.AuthInvalidError):
        _identity(subject="local")
    with pytest.raises(q.AuthInvalidError):
        _identity(subject="__public__")


# ---------------------------------------------------------------------------
# 3. Async-safe persistence, passwords, admin policy, durable revocation
# ---------------------------------------------------------------------------


def test_password_roundtrip_uses_qualified_upstream_argon2():
    hashed = q.qualify_password_hash("correct horse 4118")
    assert hashed.startswith("$argon2")
    assert q.verify_account_password("correct horse 4118", hashed) is True
    assert q.verify_account_password("wrong password", hashed) is False
    assert q.verify_account_password("anything", "not-a-hash") is False


def test_password_enrollment_required_for_missing_or_foreign_hashes():
    assert q.password_enrollment_required(None) is True
    assert q.password_enrollment_required("") is True
    assert q.password_enrollment_required("$2b$12$bcrypt-hash") is True
    assert q.password_enrollment_required(q.qualify_password_hash("x" * 12)) is False


@pytest.mark.asyncio
async def test_upstream_admin_advisory_never_promotes_to_superuser():
    config = _config("accounts")
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = _identity(upstream_is_admin=True)
    _enrolled(store, identity, is_superuser=False)
    token, _ = await q.mint_moonmind_session(identity, store, config)
    resolved = await q.validate_moonmind_session(token, store, revocation, config)
    assert resolved.is_superuser is False


@pytest.mark.asyncio
async def test_inactive_account_blocked_at_mint_and_at_validation():
    config = _config("accounts")
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = _identity()
    account = _enrolled(store, identity, is_active=False)
    with pytest.raises(q.ForbiddenError):
        await q.mint_moonmind_session(identity, store, config)
    # Even a minted-shaped token for a demoted operator fails validation.
    store._accounts[account.user_id] = q.AccountRecord(
        user_id=account.user_id, is_active=True
    )
    token, _ = await q.mint_moonmind_session(identity, store, config)
    store._accounts[account.user_id] = q.AccountRecord(
        user_id=account.user_id, is_active=False
    )
    with pytest.raises(q.ForbiddenError):
        await q.validate_moonmind_session(token, store, revocation, config)


@pytest.mark.asyncio
async def test_logout_revocation_is_durable_across_validator_replicas():
    config = _config("accounts")
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    replica_a = q.SessionValidationCache(config)
    replica_b = q.SessionValidationCache(config)
    identity = _identity()
    _enrolled(store, identity)
    token, _ = await q.mint_moonmind_session(identity, store, config)
    await replica_a.validate(token, store, revocation)
    jti = jwt.decode(token, options={"verify_signature": False})["jti"]
    await revocation.revoke_session(jti)
    with pytest.raises(q.AuthInvalidError):
        await replica_b.validate(token, store, revocation)


@pytest.mark.asyncio
async def test_password_reset_generation_invalidates_sessions():
    config = _config("accounts")
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = _identity()
    account = _enrolled(store, identity)
    token, _ = await q.mint_moonmind_session(identity, store, config)
    generation = await revocation.generation_for_user(account.user_id)
    await q.validate_moonmind_session(
        token, store, revocation, config, expected_generation=generation
    )
    new_generation = await revocation.revoke_all_for_user(account.user_id)
    assert new_generation == generation + 1
    with pytest.raises(q.AuthInvalidError):
        await q.validate_moonmind_session(
            token, store, revocation, config, expected_generation=generation
        )


# ---------------------------------------------------------------------------
# 4. Cache, grant-derived, malformed, and unsupported-surface rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_cannot_bypass_revocation_or_status():
    config = _config("accounts")
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    cache = q.SessionValidationCache(config)
    identity = _identity()
    _enrolled(store, identity)
    token, _ = await q.mint_moonmind_session(identity, store, config)
    await cache.validate(token, store, revocation)
    assert cache.misses == 1
    jti = jwt.decode(token, options={"verify_signature": False})["jti"]
    await revocation.revoke_session(jti)
    with pytest.raises(q.AuthInvalidError):
        await cache.validate(token, store, revocation)


@pytest.mark.asyncio
async def test_grant_derived_token_rejected_despite_valid_signature():
    config = _config("accounts")
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = _identity()
    account = _enrolled(store, identity)
    now = int(time.time())
    grant_token = jwt.encode(
        {
            "sub": str(account.user_id),
            "iss": config.token_issuer,
            "aud": config.token_audience,
            "purpose": q.MOONMIND_SESSION_PURPOSE,
            "jti": secrets.token_hex(16),
            "iat": now,
            "exp": now + 3600,
            "grant_id": "grant-4118",
        },
        config.cookie_secret,
        algorithm="HS256",
    )
    with pytest.raises(q.UnsupportedSurfaceError):
        await q.validate_moonmind_session(grant_token, store, revocation, config)
    scoped = jwt.encode(
        {
            "sub": str(account.user_id),
            "iss": config.token_issuer,
            "aud": config.token_audience,
            "purpose": q.MOONMIND_SESSION_PURPOSE,
            "jti": secrets.token_hex(16),
            "iat": now,
            "exp": now + 3600,
            "scope": "sessions:read",
        },
        config.cookie_secret,
        algorithm="HS256",
    )
    with pytest.raises(q.UnsupportedSurfaceError):
        await q.validate_moonmind_session(scoped, store, revocation, config)


@pytest.mark.asyncio
async def test_malformed_and_wrong_key_tokens_rejected():
    config = _config("accounts")
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    with pytest.raises(q.AuthInvalidError):
        await q.validate_moonmind_session("malformed", store, revocation, config)
    other = _config("accounts")
    identity = _identity()
    _enrolled(store, identity)
    foreign, _ = await q.mint_moonmind_session(identity, store, other)
    with pytest.raises(q.AuthInvalidError):
        await q.validate_moonmind_session(foreign, store, revocation, config)


@pytest.mark.asyncio
async def test_missing_vs_invalid_vs_conflict_semantics():
    config = _config("accounts")
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    with pytest.raises(q.AuthRequiredError):
        await q.resolve_current_user(
            cookie_token=None,
            bearer_token=None,
            account_store=store,
            revocation=revocation,
            config=config,
        )
    assert (
        await q.resolve_current_user(
            cookie_token=None,
            bearer_token=None,
            account_store=store,
            revocation=revocation,
            config=config,
            optional=True,
        )
        is None
    )
    with pytest.raises(q.AuthInvalidError):
        await q.resolve_current_user(
            cookie_token="bogus",
            bearer_token=None,
            account_store=store,
            revocation=revocation,
            config=config,
        )
    id_a = _identity(subject="alice")
    id_b = _identity(subject="bob")
    acc_a = _enrolled(store, id_a)
    acc_b = _enrolled(store, id_b)
    assert acc_a.user_id != acc_b.user_id
    tok_a, _ = await q.mint_moonmind_session(id_a, store, config)
    tok_b, _ = await q.mint_moonmind_session(id_b, store, config)
    with pytest.raises(q.AuthConflictError):
        await q.resolve_current_user(
            cookie_token=tok_a,
            bearer_token=tok_b,
            account_store=store,
            revocation=revocation,
            config=config,
        )


def test_unsupported_surfaces_fail_closed():
    for surface in q.UNSUPPORTED_SURFACES:
        with pytest.raises(q.UnsupportedSurfaceError):
            q.assert_surface_not_used(surface)


# ---------------------------------------------------------------------------
# 5. Control-plane/runtime isolation including hostile ambient environment
# ---------------------------------------------------------------------------


def test_retired_and_unknown_selectors_fail_closed_with_guidance():
    for retired in ("keycloak", "default", "google", "KEYCLOAK", " Default "):
        with pytest.raises(q.AuthConfigError, match="(?i)removed|unknown|explicit"):
            q.validate_mode_selector(retired)
    with pytest.raises(q.AuthConfigError):
        q.validate_mode_selector("saml")


def test_hostile_ambient_omnigent_env_does_not_select_moonmind_behavior(monkeypatch):
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "accounts")
    monkeypatch.setenv("OMNIGENT_AUTH_ENABLED", "1")
    monkeypatch.setenv("OMNIGENT_LOCAL_SINGLE_USER", "1")
    monkeypatch.setenv("OMNIGENT_AUTH_HEADER", "X-Evil-Header")
    monkeypatch.setenv("OMNIGENT_ACCOUNTS_COOKIE_SECRET", "00" * 32)
    monkeypatch.setenv("OMNIGENT_OIDC_ISSUER", "https://evil.example.invalid")
    config = _config("disabled")
    assert config.mode == "disabled"
    assert config.cookie_name == q.MOONMIND_DEV_COOKIE
    assert "OMNIGENT" not in config.token_issuer


def test_control_plane_and_runtime_cookies_keys_purposes_are_distinct():
    prod = q.MoonmindAuthConfig(
        mode="accounts",
        cookie_name=q.MOONMIND_PROD_COOKIE,
        cookie_secret=secrets.token_bytes(32),
    )
    assert prod.cookie_name not in q.UPSTREAM_SESSION_COOKIES
    assert prod.token_issuer == q.MOONMIND_TOKEN_ISSUER
    assert prod.token_audience == q.MOONMIND_TOKEN_AUDIENCE
    with pytest.raises(q.AuthConfigError):
        q.MoonmindAuthConfig(
            mode="accounts",
            cookie_name="__Host-ap_session",
            cookie_secret=secrets.token_bytes(32),
        )
    with pytest.raises(q.AuthConfigError):
        q.MoonmindAuthConfig(
            mode="accounts",
            cookie_name=q.MOONMIND_DEV_COOKIE,
            cookie_secret=b"short",
        )


@pytest.mark.asyncio
async def test_upstream_runtime_tokens_rejected_at_moonmind_boundary():
    from omnigent.server.oidc import mint_session_token

    config = _config("accounts")
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    upstream_secret = secrets.token_bytes(32)
    upstream_token = mint_session_token("alice@example.invalid", upstream_secret, 3600, "accounts")
    with pytest.raises(q.AuthInvalidError):
        await q.validate_moonmind_session(upstream_token, store, revocation, config)


# ---------------------------------------------------------------------------
# 6. Pin, packaged artifact, and standalone-consumer evidence
# ---------------------------------------------------------------------------


def test_upstream_probes_pass_on_pinned_revision():
    evidence = q.collect_upstream_probe_evidence()
    assert evidence.commit == "f04b0354fb5344c1ea8b92795ceb6760a9ad7595", evidence.notes
    assert evidence.package == "omnigent==0.12.0", evidence.notes
    assert evidence.session_roundtrip, evidence.notes
    assert evidence.password_roundtrip, evidence.notes
    assert evidence.accounts_config_fail_closed, evidence.notes
    assert evidence.oidc_config_fail_closed, evidence.notes
    assert evidence.upstream_defaults_intact, evidence.notes


def test_production_packaging_excludes_irrelevant_runtime_modules():
    import sys

    for module in (
        "omnigent.server.app",
        "omnigent.server.permissions",
        "omnigent.stores.permission_store",
    ):
        assert module not in sys.modules, f"{module} must not be imported by qualification"


def test_no_ambient_auth_state_at_import():
    # NOTE: this check must not reload the qualification module in-process.
    # Re-executing it rebinds AuthConfigError/validators in the live module
    # namespace while consumers (e.g. moonmind.security.auth_modes_4120)
    # keep early-bound references, so retired-selector errors raised after
    # the reload escape `pytest.raises(m.AuthModeError)` for the rest of
    # the worker process. Verify import purity in an isolated subprocess.
    import subprocess
    import sys
    from pathlib import Path

    scrubbed = {
        var: os.environ.pop(var, None)
        for var in (
            "OMNIGENT_AUTH_PROVIDER",
            "OMNIGENT_AUTH_ENABLED",
            "OMNIGENT_LOCAL_SINGLE_USER",
        )
    }
    try:
        child_env = dict(os.environ)
        for var in scrubbed:
            child_env.pop(var, None)
        repo_root = Path(__file__).resolve().parents[3]
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from moonmind.security import "
                "omnigent_auth_qualification as q;"
                "assert q.SUPPORTED_MODES == "
                '("accounts", "oidc", "header", "disabled"), '
                "q.SUPPORTED_MODES",
            ],
            check=True,
            cwd=repo_root,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        for var, value in scrubbed.items():
            if value is not None:
                os.environ[var] = value
    assert q.SUPPORTED_MODES == ("accounts", "oidc", "header", "disabled")
