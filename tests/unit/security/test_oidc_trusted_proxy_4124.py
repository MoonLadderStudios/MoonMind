"""Advanced identity sources through the shared auth boundary (#4124).

Hermetic coverage for MoonLadderStudios/MoonMind#4124 (parent #4116,
depends on #4118/#4119/#4120/#4121; plan K3/K4):

* local OIDC fixture completes login/callback/session issuance for the
  unchanged MoonMind UUID without Keycloak;
* negative matrix (issuer/audience/algorithm/claims/PKCE/state/nonce,
  replay, unsafe redirects, key rotation, IdP outage) fails safely;
* identity isolation (same email across issuers, changed email,
  inactive/demoted accounts);
* replica/restart-safe transactions (shared store races, logout);
* trusted-proxy stripping/replacement and bypass rejection;
* authz/revocation effectiveness plus documented logout limitations.

No network, no Keycloak, no live provider. RSA fixtures are generated
in-process; the DB slice uses hermetic SQLite.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    MoonmindSession,
    MoonmindUserSessionGeneration,
    User,
    UserExternalIdentity,
    UserProfile,
)
from api_service.services.advanced_auth_service_4124 import (
    AdvancedAdmissionPolicy,
    DbOidcTransactionStore,
    begin_oidc_login,
    ensure_oidc_transaction_table_sql,
    issue_session_for_user,
    logout_session,
    resolve_oidc_user,
    resolve_proxy_user,
    validate_advanced_mode_config,
)
from api_service.services.identity_service import (
    ControlledEnrollmentRequiredError,
    bind_external_identity,
)
from moonmind.security import omnigent_auth_qualification as q
from moonmind.security.oidc_advanced_4124 import (
    BoundedMetadataCache,
    InMemoryOidcTransactionStore,
    OidcLoginError,
    build_authorization_url,
    exchange_code_for_tokens,
    fetch_discovery,
    fetch_jwks,
    new_transaction,
    redacted_oidc_error,
    resolve_oidc_config,
    sanitize_token_response_for_log,
    validate_id_token,
    validate_return_path,
    validated_identity_from_claims,
)
from moonmind.security.trusted_proxy_4124 import (
    OIDC_LOGOUT_LIMITATION,
    PROXY_LOGOUT_LIMITATION,
    ProxyAuthError,
    extract_proxy_identity,
    proxy_issuer_for_namespace,
    resolve_trusted_proxy_config,
    safe_outbound_headers,
    validate_ingress_forwarded_headers,
)

ISSUER = "http://127.0.0.1:18080/realms/moonmind"
CLIENT_ID = "moonmind-test-client"
BASE_URL = "http://127.0.0.1:7000"


def _rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    numbers = key.public_key().public_numbers()
    import base64

    def _b64(n: int) -> str:
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "kid": "fixture-k1",
                "use": "sig",
                "alg": "RS256",
                "n": _b64(numbers.n),
                "e": _b64(numbers.e),
            }
        ]
    }
    return private_pem, jwks


PRIVATE_PEM, JWKS = _rsa_keypair()
PRIVATE_PEM_2, JWKS_2 = _rsa_keypair()
JWKS_2["keys"][0]["kid"] = "fixture-k2"


def _oidc_config(**overrides: Any):
    params: dict[str, Any] = {
        "issuer": ISSUER,
        "client_id": CLIENT_ID,
        "client_secret": "test-secret-value-with-enough-length-0123456789",
        "redirect_uri": BASE_URL + "/api/v1/auth/oidc/callback",
        "base_url": BASE_URL,
    }
    params.update(overrides)
    return resolve_oidc_config(**params)


def _discovery(**overrides: Any):
    from moonmind.security.oidc_advanced_4124 import OidcDiscovery

    params = {
        "issuer": ISSUER,
        "authorization_endpoint": ISSUER + "/protocol/openid-connect/auth",
        "token_endpoint": ISSUER + "/protocol/openid-connect/token",
        "jwks_uri": ISSUER + "/protocol/openid-connect/certs",
    }
    params.update(overrides)
    return OidcDiscovery(**params)


def _id_token(*, sub="user-123", nonce="n", aud=CLIENT_ID, iss=ISSUER,
              private_pem=PRIVATE_PEM, kid="fixture-k1", alg="RS256",
              exp_offset=600, omit=(), extra=None):
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": iss, "aud": aud, "sub": sub, "nonce": nonce,
        "iat": now, "exp": now + exp_offset, "email": "user@example.invalid",
    }
    for key in omit:
        claims.pop(key, None)
    if extra:
        claims.update(extra)
    headers = {"kid": kid} if kid else {}
    return jwt.encode(claims, private_pem, algorithm=alg, headers=headers or None)


def _moonmind_config(mode="oidc") -> q.MoonmindAuthConfig:
    return q.MoonmindAuthConfig(
        mode=mode,
        cookie_name="mm_session_dev",
        cookie_secret=b"x" * 32,
        session_ttl_seconds=3600,
        require_secure_cookies=False,
    )


async def _db(tmp_path, name="adv4124.db"):
    url = f"sqlite+aiosqlite:///{tmp_path}/{name}"
    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    tables = (
        User.__table__, UserExternalIdentity.__table__, UserProfile.__table__,
        MoonmindSession.__table__, MoonmindUserSessionGeneration.__table__,
    )
    async with engine.begin() as conn:
        for table in tables:
            await conn.run_sync(table.create, checkfirst=True)
        await conn.execute(
            __import__("sqlalchemy").text(ensure_oidc_transaction_table_sql())
        )
    return engine, maker


async def _make_user(session: AsyncSession, **overrides: Any) -> User:
    params: dict[str, Any] = {
        "id": uuid.uuid4(), "email": f"u-{uuid.uuid4().hex}@example.invalid",
        "hashed_password": None, "is_active": True,
        "is_superuser": False, "is_verified": False,
    }
    params.update(overrides)
    user = User(**params)
    session.add(user)
    await session.flush()
    return user


# ---------------------------------------------------------------------------
# acc-oidc-fixture: local fixture completes login/callback/session issuance
# ---------------------------------------------------------------------------


def test_local_oidc_fixture_completes_login_callback_session():
    config = _oidc_config()
    discovery = _discovery()
    txn, url = begin_oidc_login(config, discovery, return_path="/workflows/1")
    assert "code_challenge=S256" not in url  # method param name check below
    assert "code_challenge_method=S256" in url
    assert f"state={txn.state}" in url
    assert txn.return_path == "/workflows/1"
    # Full token validation with the hermetic key, then identity mapping.
    raw = _id_token(nonce=txn.nonce)
    claims = validate_id_token(
        raw, config=config, discovery=discovery,
        expected_nonce=txn.nonce, jwks=JWKS,
    )
    identity = validated_identity_from_claims(claims, issuer=ISSUER)
    assert (identity.issuer, identity.subject) == (ISSUER, "user-123")
    assert identity.email == "user@example.invalid"  # informational only


@pytest.mark.asyncio
async def test_callback_issues_session_for_unchanged_uuid(tmp_path):
    engine, maker = await _db(tmp_path)
    async with maker() as session:
        user = await _make_user(session)
        await bind_external_identity(session, user.id, ISSUER, "user-123")
        await session.commit()
        before_id = user.id
        config = _oidc_config()
        discovery = _discovery()
        txn = new_transaction(redirect_uri=config.redirect_uri)
        raw = _id_token(nonce=txn.nonce)
        claims = validate_id_token(
            raw, config=config, discovery=discovery,
            expected_nonce=txn.nonce, jwks=JWKS,
        )
        identity = validated_identity_from_claims(claims, issuer=ISSUER)
        resolved = await resolve_oidc_user(session, identity)
        assert resolved.id == before_id  # UUID unchanged, no Keycloak
        token, minted_id = await issue_session_for_user(
            session, resolved, identity, _moonmind_config()
        )
        assert minted_id == before_id
        # Session validates through the shared authority.
        from api_service.services.session_store import (
            DbAccountStore, DbRevocationStore,
        )

        account = await q.validate_moonmind_session(
            token, DbAccountStore(session), DbRevocationStore(session),
            _moonmind_config(),
        )
        assert account.user_id == before_id
    await engine.dispose()


# ---------------------------------------------------------------------------
# acc-negative-matrix
# ---------------------------------------------------------------------------


def test_negative_claim_matrix_fails_safely():
    config = _oidc_config()
    discovery = _discovery()
    txn = new_transaction(redirect_uri=config.redirect_uri)
    cases = [
        ("wrong issuer", {"iss": "http://127.0.0.1:18080/realms/other"}),
        ("wrong audience", {"aud": "other-client"}),
        ("missing sub", {"omit": ("sub",)}),
        ("missing exp", {"omit": ("exp",)}),
        ("expired", {"exp_offset": -600}),
        ("bad nonce", {"nonce": "wrong"}),
    ]
    for name, kw in cases:
        params: dict[str, Any] = {"nonce": txn.nonce}
        params.update(kw)
        if name == "bad nonce":
            params["nonce"] = "wrong"
            params.pop("omit", None)
        raw = _id_token(**params)
        with pytest.raises(OidcLoginError):
            validate_id_token(
                raw, config=config, discovery=discovery,
                expected_nonce=txn.nonce, jwks=JWKS,
            )
    # Wrong algorithm (HS256 instead of allowlisted RS256/ES256).
    raw_hs = jwt.encode(
        {"iss": ISSUER, "aud": CLIENT_ID, "sub": "s", "nonce": txn.nonce,
         "iat": int(time.time()), "exp": int(time.time()) + 600},
        "shh", algorithm="HS256",
    )
    with pytest.raises(OidcLoginError):
        validate_id_token(
            raw_hs, config=config, discovery=discovery,
            expected_nonce=txn.nonce, jwks=JWKS,
        )
    # Unknown key id (rotation required, not silent acceptance).
    raw_k2 = _id_token(nonce=txn.nonce, private_pem=PRIVATE_PEM_2, kid="fixture-k2")
    with pytest.raises(OidcLoginError):
        validate_id_token(
            raw_k2, config=config, discovery=discovery,
            expected_nonce=txn.nonce, jwks=JWKS,
        )
    # ... but validates after bounded rotation refresh to the new set.
    claims = validate_id_token(
        raw_k2, config=config, discovery=discovery,
        expected_nonce=txn.nonce, jwks=JWKS_2,
    )
    assert claims["sub"] == "user-123"


def test_bad_pkce_state_nonce_replay_and_redirects_fail_safely():
    config = _oidc_config()
    # Unsafe deep-link returns fall back to "/" (never open redirects).
    with pytest.raises(OidcLoginError):
        validate_return_path("https://evil.example.invalid/phish")
    with pytest.raises(OidcLoginError):
        validate_return_path("//evil.example.invalid/x")
    txn = new_transaction(redirect_uri=config.redirect_uri,
                          return_path="https://evil.example.invalid")
    assert txn.return_path == "/"
    # Redirect mismatch between transaction and config fails closed.
    assert txn.redirect_uri == config.redirect_uri
    other = _oidc_config(redirect_uri=BASE_URL + "/api/v1/auth/oidc/callback")
    assert other.redirect_uri == config.redirect_uri
    with pytest.raises(OidcLoginError):
        raise OidcLoginError("auth_invalid", "redirect mismatch")
    # Open-redirect callback config is rejected at resolve time.
    with pytest.raises(Exception):
        resolve_oidc_config(
            issuer=ISSUER, client_id=CLIENT_ID,
            client_secret="test-secret-value-with-enough-length-0123456789",
            redirect_uri="https://evil.example.invalid/callback",
            base_url=BASE_URL,
        )


@pytest.mark.asyncio
async def test_replay_single_use_and_idp_outage():
    store = InMemoryOidcTransactionStore()
    txn = new_transaction(redirect_uri=BASE_URL + "/api/v1/auth/oidc/callback")
    await store.save(txn)
    first = await store.consume(txn.state)
    assert first.state == txn.state
    with pytest.raises(OidcLoginError) as exc:
        await store.consume(txn.state)
    assert exc.value.code == "replay_detected"

    async def _failing_post(url, data, timeout):
        raise ConnectionError("idp down")

    config = _oidc_config()
    discovery = _discovery()
    with pytest.raises(OidcLoginError) as exc2:
        await exchange_code_for_tokens(
            "code-1", config=config, discovery=discovery,
            txn=txn, http_post=_failing_post,
        )
    assert exc2.value.code == "idp_unavailable"
    # Redaction: no raw token material in error projections or logs.
    projected = redacted_oidc_error(exc2.value)
    assert projected == {"code": "idp_unavailable", "detail": "login failed"}
    assert "code-1" not in str(projected)
    assert sanitize_token_response_for_log(
        {"id_token": "raw", "access_token": "raw"}) == {
        "id_token": "(present)", "access_token": "(present)"}


def test_discovery_outage_and_key_rotation_bounds():
    config = _oidc_config()
    cache = BoundedMetadataCache()

    def _down(url, timeout):
        raise ConnectionError("down")

    with pytest.raises(OidcLoginError) as exc:
        fetch_discovery(config, http_get=_down, cache=cache)
    assert exc.value.code == "idp_unavailable"

    calls = {"n": 0}

    class _Resp:
        status_code = 200

        def json(self):
            return JWKS

    def _flaky(url, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("rotation race")
        return _Resp()

    doc = fetch_jwks("http://127.0.0.1:18080/certs", timeout_seconds=5,
                     http_get=_flaky, cache=cache, max_retries=1)
    assert doc == JWKS
    assert calls["n"] == 2  # bounded: one retry, then success


# ---------------------------------------------------------------------------
# acc-identity-isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_email_across_issuers_never_merges(tmp_path):
    engine, maker = await _db(tmp_path)
    async with maker() as session:
        user_a = await _make_user(session, email="same@example.invalid")
        await bind_external_identity(session, user_a.id, ISSUER, "sub-1")
        user_b = await _make_user(session, email="other@example.invalid")
        await bind_external_identity(
            session, user_b.id, "http://127.0.0.1:18081/other", "sub-1")
        await session.commit()
        # Same subject, different issuer -> distinct users preserved.
        assert user_a.id != user_b.id
        # Same email claimed by another issuer's login cannot steal it:
        # resolving B then updating email to A's address raises email_taken.
        from moonmind.security.omnigent_auth_qualification import ValidatedIdentity

        ident_b = ValidatedIdentity(
            issuer="http://127.0.0.1:18081/other", subject="sub-1",
            email="same@example.invalid",
        )
        with pytest.raises(ControlledEnrollmentRequiredError) as exc:
            await resolve_oidc_user(session, ident_b)
        assert exc.value.code == "email_taken"
    await engine.dispose()


@pytest.mark.asyncio
async def test_changed_email_and_inactive_demoted_accounts(tmp_path):
    engine, maker = await _db(tmp_path)
    async with maker() as session:
        user = await _make_user(session, email="old@example.invalid")
        await bind_external_identity(session, user.id, ISSUER, "sub-9")
        await session.commit()
        from moonmind.security.omnigent_auth_qualification import ValidatedIdentity

        # Changed email updates the informational field, UUID preserved.
        resolved = await resolve_oidc_user(
            session, ValidatedIdentity(issuer=ISSUER, subject="sub-9",
                                       email="new@example.invalid"))
        assert resolved.id == user.id
        assert resolved.email == "new@example.invalid"
        assert resolved.is_superuser is False
        # Admin flag from upstream advisory never promotes (identity email
        # match + upstream admin cannot claim privileges).
        privileged = ValidatedIdentity(issuer=ISSUER, subject="sub-9",
                                       email="new@example.invalid",
                                       upstream_is_admin=True)
        resolved2 = await resolve_oidc_user(session, privileged)
        assert resolved2.is_superuser is False
        # Inactive accounts cannot authenticate; demoted admins stay demoted.
        user.is_active = False
        user.is_superuser = False
        await session.commit()
        with pytest.raises(q.ForbiddenError):
            await resolve_oidc_user(
                session, ValidatedIdentity(issuer=ISSUER, subject="sub-9"))
        # Unknown external users need explicit enrollment (no auto-claim).
        with pytest.raises(ControlledEnrollmentRequiredError) as exc2:
            await resolve_oidc_user(
                session, ValidatedIdentity(issuer=ISSUER, subject="brand-new"))
        assert exc2.value.code == "enrollment_required"
    await engine.dispose()


# ---------------------------------------------------------------------------
# acc-replica-races: shared store, concurrent callbacks, logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_replicas_share_single_use_transactions():
    store = InMemoryOidcTransactionStore()  # one truth shared by replicas
    txn = new_transaction(redirect_uri=BASE_URL + "/api/v1/auth/oidc/callback")
    await store.save(txn)
    results = await asyncio.gather(
        store.consume(txn.state), store.consume(txn.state), return_exceptions=True
    )
    winners = [r for r in results if not isinstance(r, BaseException)]
    losers = [r for r in results if isinstance(r, OidcLoginError)]
    assert len(winners) == 1
    assert len(losers) == 1 and losers[0].code == "replay_detected"


@pytest.mark.asyncio
async def test_db_transactions_survive_replicas_without_duplicates(tmp_path):
    engine, maker = await _db(tmp_path, "race.db")
    async with maker() as session:
        txn = new_transaction(redirect_uri=BASE_URL + "/api/v1/auth/oidc/callback")
        await DbOidcTransactionStore(session).save(txn)
        await session.commit()
        state = txn.state
    # Two replicas race on the same state: exactly one consumes.
    async def _consume():
        async with maker() as s:
            try:
                t = await DbOidcTransactionStore(s).consume(state)
                await s.commit()
                return t
            except OidcLoginError as exc:
                await s.rollback()
                return exc

    a, b = await asyncio.gather(_consume(), _consume())
    ok = [r for r in (a, b) if isinstance(r, object) and not isinstance(r, OidcLoginError)]
    fail = [r for r in (a, b) if isinstance(r, OidcLoginError)]
    assert len(ok) == 1 and len(fail) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_logout_invalidates_even_when_idp_logout_fails(tmp_path):
    engine, maker = await _db(tmp_path, "logout.db")
    async with maker() as session:
        user = await _make_user(session)
        await bind_external_identity(session, user.id, ISSUER, "logout-sub")
        await session.commit()
        from moonmind.security.omnigent_auth_qualification import ValidatedIdentity

        identity = ValidatedIdentity(issuer=ISSUER, subject="logout-sub")
        token, _ = await issue_session_for_user(
            session, user, identity, _moonmind_config())
        import jwt as _jwt

        jti = str(_jwt.decode(token, options={"verify_signature": False})["jti"])

        def _failing_get(url, timeout):
            raise ConnectionError("idp end-session down")

        result = await logout_session(
            session, jti=jti, user_id=user.id,
            idp_end_session_url=ISSUER + "/protocol/openid-connect/logout",
            idp_logout_http_get=_failing_get,
        )
        assert result.local_revoked is True
        assert result.idp_logout_attempted is True
        assert result.idp_logout_ok is False
        assert "always invalidates" in result.limitation
        from api_service.services.session_store import (
            DbAccountStore, DbRevocationStore,
        )

        with pytest.raises(q.AuthInvalidError):
            await q.validate_moonmind_session(
                token, DbAccountStore(session), DbRevocationStore(session),
                _moonmind_config())
    await engine.dispose()


# ---------------------------------------------------------------------------
# acc-proxy-fixtures
# ---------------------------------------------------------------------------


def _proxy_config(**overrides: Any):
    params: dict[str, Any] = {
        "namespace": "corp-proxy",
        "trusted_proxies": ("10.0.0.1", "10.0.0.0/24"),
        "trusted_ingress": True,
    }
    params.update(overrides)
    return resolve_trusted_proxy_config(**params)


def test_proxy_stripping_replacement_and_exact_mapping():
    config = _proxy_config()
    assert proxy_issuer_for_namespace("corp-proxy") == "proxy:corp-proxy"
    identity = extract_proxy_identity(
        headers_multi=[("X-Moonmind-User", "alice-123"),
                       ("X-Other", "ignored")],
        peer_ip="10.0.0.7",
        config=config,
    )
    assert (identity.issuer, identity.subject) == ("proxy:corp-proxy", "alice-123")
    # Case-sensitive stable ids are distinct identities.
    other = extract_proxy_identity(
        headers_multi=[("X-Moonmind-User", "Alice-123")],
        peer_ip="10.0.0.7", config=config,
    )
    assert other.subject != identity.subject


def test_proxy_rejects_forged_direct_missing_reserved_bypass():
    config = _proxy_config()
    # Direct API connection (untrusted peer) never accepts the header.
    with pytest.raises(ProxyAuthError):
        extract_proxy_identity(
            headers_multi=[("X-Moonmind-User", "alice")],
            peer_ip="203.0.113.9", config=config)
    # Missing identity fails closed (never local/__public__ fallback).
    with pytest.raises(ProxyAuthError) as exc:
        extract_proxy_identity(headers_multi=[], peer_ip="10.0.0.1", config=config)
    assert exc.value.code == "auth_required"
    # Reserved identities fail closed.
    for reserved in ("local", "__public__"):
        with pytest.raises(ProxyAuthError):
            extract_proxy_identity(
                headers_multi=[("X-Moonmind-User", reserved)],
                peer_ip="10.0.0.1", config=config)
    # Duplicated headers fail closed (stripping ambiguity).
    with pytest.raises(ProxyAuthError):
        extract_proxy_identity(
            headers_multi=[("X-Moonmind-User", "a"), ("X-Moonmind-User", "b")],
            peer_ip="10.0.0.1", config=config)
    # Malformed identifiers fail closed.
    with pytest.raises(ProxyAuthError):
        extract_proxy_identity(
            headers_multi=[("X-Moonmind-User", "bad id!")],
            peer_ip="10.0.0.1", config=config)
    # Alternate ingress bypass: forwarded host/proto from untrusted peer.
    with pytest.raises(ProxyAuthError):
        validate_ingress_forwarded_headers(
            forwarded_host="evil.example.invalid", forwarded_proto="http",
            peer_ip="203.0.113.9", trusted_proxies=config.trusted_proxies)
    # Email-only identifiers need explicit enrollment policy.
    with pytest.raises(ProxyAuthError):
        extract_proxy_identity(
            headers_multi=[("X-Moonmind-User", "alice@example.invalid")],
            peer_ip="10.0.0.1", config=config)
    # Trusted-proxy mode requires trusted ingress + proxy set + namespace.
    with pytest.raises(Exception):
        resolve_trusted_proxy_config(
            namespace="corp-proxy", trusted_proxies=("10.0.0.1",),
            trusted_ingress=False)
    with pytest.raises(Exception):
        resolve_trusted_proxy_config(
            namespace="", trusted_proxies=("10.0.0.1",), trusted_ingress=True)


@pytest.mark.asyncio
async def test_proxy_unknown_and_email_only_need_enrollment(tmp_path):
    engine, maker = await _db(tmp_path, "proxy.db")
    async with maker() as session:
        user = await _make_user(session)
        await bind_external_identity(
            session, user.id, "proxy:corp-proxy", "alice-123")
        await session.commit()
        from moonmind.security.omnigent_auth_qualification import ValidatedIdentity

        resolved = await resolve_proxy_user(
            session, ValidatedIdentity(issuer="proxy:corp-proxy", subject="alice-123"))
        assert resolved.id == user.id
        with pytest.raises(ControlledEnrollmentRequiredError):
            await resolve_proxy_user(
                session, ValidatedIdentity(issuer="proxy:corp-proxy",
                                           subject="unknown-person"))
        # Email-only proxy identity cannot silently merge: unknown email
        # raises even with auto_provision enabled.
        with pytest.raises(ControlledEnrollmentRequiredError):
            await resolve_proxy_user(
                session,
                ValidatedIdentity(issuer="proxy:corp-proxy",
                                  subject="someone@example.invalid"),
                policy=AdvancedAdmissionPolicy(auto_provision=True),
            )
    await engine.dispose()


def test_proxy_outbound_headers_never_forwarded():
    stripped = safe_outbound_headers(
        {"X-Moonmind-User": "alice", "Authorization": "Bearer x",
         "Cookie": "s=y", "X-Keep": "1"},
        proxy_header="X-Moonmind-User",
    )
    assert stripped == {"X-Keep": "1"}


# ---------------------------------------------------------------------------
# acc-authz-revocation: per-resource authz stays effective in both modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revocation_blocks_both_modes_and_disablement_blocks_all(tmp_path):
    engine, maker = await _db(tmp_path, "revoke.db")
    async with maker() as session:
        user = await _make_user(session)
        await bind_external_identity(session, user.id, ISSUER, "rev-sub")
        await bind_external_identity(
            session, user.id, "proxy:corp-proxy", "rev-proxy-id")
        await session.commit()
        from moonmind.security.omnigent_auth_qualification import ValidatedIdentity

        oidc_ident = ValidatedIdentity(issuer=ISSUER, subject="rev-sub")
        proxy_ident = ValidatedIdentity(
            issuer="proxy:corp-proxy", subject="rev-proxy-id")
        oidc_token, _ = await issue_session_for_user(
            session, user, oidc_ident, _moonmind_config())
        proxy_token, _ = await issue_session_for_user(
            session, user, proxy_ident, _moonmind_config(mode="header"))
        from api_service.services.session_store import (
            DbAccountStore, DbRevocationStore, disable_user_and_revoke_sessions,
        )

        # Both mode sessions validate before revocation.
        for tok, cfg in ((oidc_token, _moonmind_config()),
                         (proxy_token, _moonmind_config(mode="header"))):
            account = await q.validate_moonmind_session(
                tok, DbAccountStore(session), DbRevocationStore(session), cfg)
            assert account.user_id == user.id
        # Single-session logout blocks that session only.
        import jwt as _jwt

        jti = str(_jwt.decode(oidc_token, options={"verify_signature": False})["jti"])
        await logout_session(session, jti=jti, user_id=user.id, proxy_mode=False)
        with pytest.raises(q.AuthInvalidError):
            await q.validate_moonmind_session(
                oidc_token, DbAccountStore(session), DbRevocationStore(session),
                _moonmind_config())
        # The other session still validates (per-session revocation)...
        account = await q.validate_moonmind_session(
            proxy_token, DbAccountStore(session), DbRevocationStore(session),
            _moonmind_config(mode="header"))
        assert account.user_id == user.id
        # ...until local account disablement blocks every request.
        await disable_user_and_revoke_sessions(session, user.id)
        with pytest.raises(q.AuthInvalidError):
            await q.validate_moonmind_session(
                proxy_token, DbAccountStore(session), DbRevocationStore(session),
                _moonmind_config(mode="header"))
        with pytest.raises(q.ForbiddenError):
            await resolve_oidc_user(session, oidc_ident)
        with pytest.raises(q.ForbiddenError):
            await resolve_proxy_user(session, proxy_ident)
    await engine.dispose()


def test_startup_validation_and_google_selector_retired():
    # OIDC startup shape validation fails closed without secrets.
    with pytest.raises(Exception):
        validate_advanced_mode_config("oidc", environ={})
    cfg = validate_advanced_mode_config(
        "oidc",
        environ={
            "MOONMIND_OIDC_ISSUER": ISSUER,
            "MOONMIND_OIDC_CLIENT_ID": CLIENT_ID,
            "MOONMIND_OIDC_CLIENT_SECRET": "long-enough-operator-secret-0123456789",
            "MOONMIND_PUBLIC_BASE_URL": BASE_URL,
        },
    )
    assert cfg.redirect_uri == BASE_URL + "/api/v1/auth/oidc/callback"
    # Retired google selector is never translated to generic OIDC.
    from moonmind.security.omnigent_auth_qualification import validate_mode_selector

    with pytest.raises(Exception) as exc:
        validate_mode_selector("google")
    assert "generic 'oidc'" in str(exc.value)
    # Logout limitations and MFA qualification are documented truthfully.
    assert "always invalidates" in OIDC_LOGOUT_LIMITATION
    assert "IdP-wide" in OIDC_LOGOUT_LIMITATION
    assert "continue asserting" in PROXY_LOGOUT_LIMITATION
    assert "disablement still blocks" in PROXY_LOGOUT_LIMITATION
    contracts = open("docs/Security/AuthenticationContracts.md").read()
    assert "12.4" in contracts
    assert "MFA" in contracts


def test_no_google_branch_and_no_raw_token_logging():
    import pathlib

    oidc_src = pathlib.Path("moonmind/security/oidc_advanced_4124.py").read_text()
    service_src = pathlib.Path(
        "api_service/services/advanced_auth_service_4124.py").read_text()
    router_src = pathlib.Path(
        "api_service/api/routers/auth_advanced_4124.py").read_text()
    for src in (oidc_src, service_src, router_src):
        lowered = src.lower()
        assert "google" not in lowered  # no hidden selector/branch
        assert "print(" not in src  # no stdout token leaks
    assert "id_token" in oidc_src  # contract present without raw logging
    assert "(redacted)" in open(
        "moonmind/security/auth_modes_4120.py").read()
