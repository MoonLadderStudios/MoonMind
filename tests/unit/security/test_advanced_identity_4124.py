"""Advanced identity coverage: generic OIDC + trusted proxy (#4124).

MoonLadderStudios/MoonMind#4124 (parent #4116; depends on #4118, #4119,
#4120, #4121). Plan coverage: advanced modes, K3 and K4 in
``docs/tmp/KeycloakRemovalPlan.md``.

Hermetic by design: a local OIDC fixture (symmetric JWKS, fake transport)
and trusted-proxy fixtures exercise the production library
(``moonmind.security.advanced_identity_4124``) end to end without Keycloak,
network, database, or live secrets. Both advanced sources resolve through
the same #4119-shaped ``(issuer, subject)`` semantics and mint #4121-bound
sessions from the qualified #4118 primitives.
"""

from __future__ import annotations

import asyncio
import base64
import secrets
import time
import uuid
from pathlib import Path

import jwt
import pytest

from moonmind.security import advanced_identity_4124 as adv
from moonmind.security import auth_modes_4120 as modes
from moonmind.security import omnigent_auth_qualification as q

ISSUER = "https://idp.example.invalid/realms/moonmind"
OTHER_ISSUER = "https://other-idp.example.invalid/realms/moonmind"
BASE_URL = "https://app.example.invalid"

_HS_KEY = secrets.token_bytes(32)
_HS_JWK = {
    "kty": "oct",
    "kid": "hermetic-key-1",
    "k": base64.urlsafe_b64encode(_HS_KEY).rstrip(b"=").decode(),
}


def _config(**overrides):
    base = {
        "issuer": ISSUER,
        "client_id": "moonmind-test-client",
        "client_secret": "test-client-secret-value",
        "callback_base_url": BASE_URL,
        "allowed_algorithms": ("HS256",),
        "allow_unknown_users": True,
    }
    base.update(overrides)
    return adv.OIDCProviderConfig(**base)


def _mint_id_token(*, key=_HS_KEY, kid="hermetic-key-1", alg="HS256", **claims):
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": "moonmind-test-client",
        "sub": "user-ABC-123",
        "iat": now,
        "exp": now + 300,
        "nonce": "test-nonce",
    }
    payload.update(claims)
    headers = {"kid": kid} if kid is not None else {}
    return jwt.encode(payload, key, algorithm=alg, headers=headers or None)


def _with_header_alg(token: str, alg: str) -> str:
    """Re-header a token without re-signing (negative allowlist test)."""
    import json as _json

    header_b64, payload_b64, signature_b64 = token.split(".")
    header = {"alg": alg, "kid": "hermetic-key-1", "typ": "JWT"}
    raw = _json.dumps(header, separators=(",", ":")).encode()
    new_header = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return f"{new_header}.{payload_b64}.{signature_b64}"


class _FakeTransport:
    """Hermetic IdP: verified discovery/JWKS/token results, no network.

    The real IdP binds the ID-token nonce to the authorization request
    server-side. This fixture models that by minting the default token
    with ``nonce_override`` (set by the test to the transaction nonce
    after creating it); tests for negative nonce behavior mint their own
    tokens explicitly instead.
    """

    def __init__(
        self,
        *,
        metadata=None,
        jwks_keys=None,
        token_handler=None,
        fail_get=False,
        fail_post=False,
    ):
        self._metadata = metadata
        self._jwks_keys = jwks_keys if jwks_keys is not None else [_HS_JWK]
        self._token_handler = token_handler
        self._fail_get = fail_get
        self._fail_post = fail_post
        self.nonce_override: str | None = None
        self.get_urls: list[str] = []
        self.post_urls: list[str] = []
        self.last_form: dict | None = None

    async def get_json(self, url, *, timeout_seconds):
        self.get_urls.append(url)
        if self._fail_get:
            raise ConnectionError("idp down")
        if url.endswith("/.well-known/openid-configuration"):
            if self._metadata is not None:
                return self._metadata
            return {
                "issuer": ISSUER,
                "authorization_endpoint": "https://idp.example.invalid/authorize",
                "token_endpoint": "https://idp.example.invalid/token",
                "jwks_uri": "https://idp.example.invalid/jwks",
            }
        return {"keys": list(self._jwks_keys)}

    async def post_form(self, url, form, *, timeout_seconds):
        self.post_urls.append(url)
        self.last_form = dict(form)
        if self._fail_post:
            raise ConnectionError("idp down")
        if self._token_handler is not None:
            return self._token_handler(dict(form))
        return {"id_token": _mint_id_token(nonce=self.nonce_override or "test-nonce")}


def _metadata():
    return adv.OIDCMetadata(
        issuer=ISSUER,
        authorization_endpoint="https://idp.example.invalid/authorize",
        token_endpoint="https://idp.example.invalid/token",
        jwks_uri="https://idp.example.invalid/jwks",
        end_session_endpoint="https://idp.example.invalid/logout",
    )


def _enrolled_store():
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    identity = q.ValidatedIdentity(issuer=ISSUER, subject="user-ABC-123")
    account = q.AccountRecord(user_id=uuid.uuid4(), is_active=True)
    store.enroll(identity, account)
    return store, revocation, account, identity


def _control_plane_config():
    return q.MoonmindAuthConfig(
        mode="oidc",
        cookie_name=q.MOONMIND_DEV_COOKIE,
        cookie_secret=secrets.token_bytes(32),
        session_ttl_seconds=3600,
        require_secure_cookies=False,
    )


# ---------------------------------------------------------------------------
# Configuration: explicit MoonMind inputs, retired google never translated
# ---------------------------------------------------------------------------


def test_oidc_config_requires_explicit_moonmind_inputs():
    config = adv.resolve_oidc_provider_config(
        {
            "OIDC_ISSUER_URL": ISSUER,
            "OIDC_CLIENT_ID": "cid",
            "OIDC_CLIENT_SECRET": "secret",
            "MOONMIND_PUBLIC_BASE_URL": BASE_URL,
        }
    )
    assert config.issuer == ISSUER
    assert config.callback_url == f"{BASE_URL}/api/v1/oidc/callback"
    # Ambient runtime variables cannot select MoonMind behavior.
    hostile = {
        "OIDC_ISSUER_URL": ISSUER,
        "OIDC_CLIENT_ID": "cid",
        "OIDC_CLIENT_SECRET": "secret",
        "MOONMIND_PUBLIC_BASE_URL": BASE_URL,
        "OMNIGENT_OIDC_ISSUER": "https://evil.example.invalid",
        "OMNIGENT_OIDC_CLIENT_ID": "evil",
    }
    assert adv.resolve_oidc_provider_config(hostile).issuer == ISSUER
    with pytest.raises(adv.OIDCConfigError):
        adv.resolve_oidc_provider_config({})
    # A base URL that cannot own a redirect destination fails closed.
    with pytest.raises(adv.OIDCConfigError):
        adv.OIDCProviderConfig(
            issuer=ISSUER,
            client_id="cid",
            client_secret="secret",
            callback_base_url="not-a-url",
        )


def test_retired_google_selector_never_becomes_generic_oidc():
    with pytest.raises(modes.AuthModeError):
        modes.validate_mode_selector("google")
    # The generic contract is selected explicitly as `oidc`, never by
    # translating a retired provider branch.
    assert modes.validate_mode_selector("oidc") == "oidc"


# ---------------------------------------------------------------------------
# acc-1: local fixture completes login/callback/session for the same UUID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_oidc_fixture_issues_session_for_unchanged_uuid():
    config = _config()
    transport = _FakeTransport()
    metadata_cache, jwks_cache = adv.OIDCMetadataCache(), adv.JWKSCache()
    store_tx = adv.InMemoryAuthTransactionStore()

    metadata = await metadata_cache.get(config, transport)
    assert metadata.token_endpoint == "https://idp.example.invalid/token"

    transaction = adv.new_authorization_transaction(config, return_path="/workflows/1")
    await store_tx.create(transaction)
    transport.nonce_override = transaction.nonce
    auth_url = adv.build_authorization_url(config, metadata, transaction)
    assert f"redirect_uri={config.callback_path}" not in auth_url
    assert "code_challenge_method=S256" in auth_url
    # The exact approved redirect destination is embedded, not browser-chosen.
    assert "redirect_uri=https%3A%2F%2Fapp.example.invalid" in auth_url

    consumed = await store_tx.consume(transaction.state)
    assert consumed.nonce == transaction.nonce
    tokens = await adv.exchange_code_for_tokens(
        code="auth-code-1",
        transaction=consumed,
        config=config,
        metadata=metadata,
        transport=transport,
    )
    # PKCE verifier travels to the verified token endpoint only.
    assert transport.last_form is not None
    assert transport.last_form["code_verifier"] == transaction.code_verifier
    assert transport.post_urls == ["https://idp.example.invalid/token"]

    jwk = await jwks_cache.get_key(
        "hermetic-key-1", metadata, transport, timeout_seconds=10
    )
    claims = adv.validate_id_token(
        tokens["id_token"], jwk=jwk, config=config, expected_nonce=consumed.nonce
    )
    identity = adv.claims_to_validated_identity(claims, issuer=config.issuer)
    # Case-sensitive subject preserved; issuer boundary preserved.
    assert identity.subject == "user-ABC-123"
    assert identity.issuer == ISSUER

    control_plane = _control_plane_config()
    account_store, revocation, _, _ = _enrolled_store_with_identity(identity)
    decision = adv.evaluate_oidc_enrollment(
        existing_user_id=account_store._identity_map[(identity.issuer, identity.subject)],
        account=await account_store.get_account(
            account_store._identity_map[(identity.issuer, identity.subject)]
        ),
        email_taken_by_other=False,
        allow_unknown_users=True,
    )
    assert decision.allowed
    token, user_id = await q.mint_moonmind_session(
        identity, account_store, control_plane, revocation=revocation
    )
    resolved = await q.validate_moonmind_session(
        token, account_store, revocation, control_plane
    )
    assert resolved.user_id == user_id

    # Second login maps to the unchanged UUID (no duplicate user).
    transaction2 = adv.new_authorization_transaction(config)
    await store_tx.create(transaction2)
    transport.nonce_override = transaction2.nonce
    consumed2 = await store_tx.consume(transaction2.state)
    tokens2 = await adv.exchange_code_for_tokens(
        code="auth-code-2",
        transaction=consumed2,
        config=config,
        metadata=metadata,
        transport=transport,
    )
    claims2 = adv.validate_id_token(
        tokens2["id_token"], jwk=jwk, config=config, expected_nonce=consumed2.nonce
    )
    identity2 = adv.claims_to_validated_identity(claims2, issuer=config.issuer)
    token2, user_id2 = await q.mint_moonmind_session(
        identity2, account_store, control_plane, revocation=revocation
    )
    assert user_id2 == user_id
    assert (await q.validate_moonmind_session(token2, account_store, revocation, control_plane)).user_id == user_id


def _enrolled_store_with_identity(identity):
    store = q.InMemoryAsyncAccountStore()
    revocation = q.InMemoryRevocationStore()
    account = q.AccountRecord(user_id=uuid.uuid4(), is_active=True)
    store.enroll(identity, account)
    return store, revocation, account, identity


# ---------------------------------------------------------------------------
# acc-2: negative claim/transaction matrix fails safely
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oidc_negative_matrix_fails_closed():
    config = _config()
    transport = _FakeTransport()
    metadata = _metadata()
    transaction = adv.new_authorization_transaction(config)
    store_tx = adv.InMemoryAuthTransactionStore()
    await store_tx.create(transaction)
    consumed = await store_tx.consume(transaction.state)
    jwks = adv.JWKSCache()

    async def _claims_for(token):
        jwk = await jwks.get_key(
            "hermetic-key-1", metadata, transport, timeout_seconds=10
        )
        return adv.validate_id_token(
            token, jwk=jwk, config=config, expected_nonce=consumed.nonce
        )

    # Wrong issuer.
    with pytest.raises(adv.OIDCClaimError):
        await _claims_for(_mint_id_token(iss="https://evil.example.invalid", nonce=consumed.nonce))
    # Wrong audience.
    with pytest.raises(adv.OIDCClaimError):
        await _claims_for(_mint_id_token(aud="someone-else", nonce=consumed.nonce))
    # Wrong algorithm: re-header a valid token to claim RS256 while only
    # HS256 is allowed. Rejected by the allowlist before any crypto.
    valid_token = _mint_id_token(nonce=consumed.nonce)
    forged = _with_header_alg(valid_token, "RS256")
    with pytest.raises(adv.OIDCClaimError):
        await _claims_for(forged)
    # Missing subject.
    payload = {"iss": ISSUER, "aud": config.client_id, "iat": int(time.time()),
               "exp": int(time.time()) + 300, "nonce": consumed.nonce}
    missing_sub = jwt.encode(payload, _HS_KEY, algorithm="HS256", headers={"kid": "hermetic-key-1"})
    with pytest.raises(adv.OIDCClaimError):
        await _claims_for(missing_sub)
    # Expired.
    with pytest.raises(adv.OIDCClaimError):
        await _claims_for(
            _mint_id_token(nonce=consumed.nonce, exp=int(time.time()) - 600, iat=int(time.time()) - 900)
        )
    # Bad nonce.
    with pytest.raises(adv.OIDCTransactionError):
        await _claims_for(_mint_id_token(nonce="attacker-nonce"))
    # Missing code.
    with pytest.raises(adv.OIDCTransactionError):
        await adv.exchange_code_for_tokens(
            code=" ", transaction=consumed, config=config, metadata=metadata,
            transport=transport,
        )
    # Unknown/foreign redirect destinations.
    with pytest.raises(adv.OIDCTransactionError):
        adv.new_authorization_transaction(config, return_path="https://evil.example.invalid/x")
    with pytest.raises(adv.OIDCTransactionError):
        adv.new_authorization_transaction(config, return_path="//evil.example.invalid/x")
    # Replay: the transaction is already consumed.
    with pytest.raises(adv.OIDCTransactionError):
        await store_tx.consume(transaction.state)
    # Unknown state.
    with pytest.raises(adv.OIDCTransactionError):
        await store_tx.consume("no-such-state")


@pytest.mark.asyncio
async def test_oidc_key_rotation_and_outage():
    config = _config()
    metadata = _metadata()
    rotated_key = secrets.token_bytes(32)
    rotated_jwk = {
        "kty": "oct",
        "kid": "rotated-key-2",
        "k": base64.urlsafe_b64encode(rotated_key).rstrip(b"=").decode(),
    }
    transport = _FakeTransport(jwks_keys=[rotated_jwk])
    jwks = adv.JWKSCache()
    transaction = adv.new_authorization_transaction(config)
    # Rotation converges after one bounded refresh: the stale cache misses
    # kid-2, refreshes once, then validates.
    stale = adv.JWKSCache()
    stale._entries[metadata.jwks_uri] = (time.time() + 300, {"hermetic-key-1": _HS_JWK})
    jwk = await stale.get_key("rotated-key-2", metadata, transport, timeout_seconds=10)
    assert jwk["kid"] == "rotated-key-2"
    assert stale.refreshes == 1
    token = _mint_id_token(key=rotated_key, kid="rotated-key-2", nonce=transaction.nonce)
    claims = adv.validate_id_token(
        token, jwk=jwk, config=config, expected_nonce=transaction.nonce
    )
    assert claims["sub"] == "user-ABC-123"
    # Unknown keys fail closed even after the rotation retry.
    with pytest.raises(adv.OIDCClaimError):
        await jwks.get_key("no-such-kid", metadata, transport, timeout_seconds=10)
    # IdP outage returns an actionable failure without changing mode.
    outage = _FakeTransport(fail_get=True, fail_post=True)
    with pytest.raises(adv.OIDCUnavailableError):
        await adv.OIDCMetadataCache().get(config, outage)
    with pytest.raises(adv.OIDCUnavailableError):
        await adv.exchange_code_for_tokens(
            code="c", transaction=transaction, config=config, metadata=metadata,
            transport=outage,
        )


# ---------------------------------------------------------------------------
# acc-3: issuer/email/status boundaries never merge or promote
# ---------------------------------------------------------------------------


def test_oidc_identity_boundaries_and_enrollment_policy():
    # Same email across issuers stays distinct (issuer is part of identity).
    claims_a = {"sub": "Same-Subject", "email": "same@example.com"}
    id_a = adv.claims_to_validated_identity(claims_a, issuer=ISSUER)
    id_b = adv.claims_to_validated_identity(claims_a, issuer=OTHER_ISSUER)
    assert (id_a.issuer, id_a.subject) != (id_b.issuer, id_b.subject)
    # Case-sensitive subjects are preserved exactly.
    assert adv.claims_to_validated_identity({"sub": "User-ABC"}, issuer=ISSUER).subject == "User-ABC"
    # Upstream admin roster is advisory only: never promoted.
    assert id_a.upstream_is_admin is False
    # Changed email on a returning identity is allowed only when untaken.
    returning = adv.evaluate_oidc_enrollment(
        existing_user_id=uuid.uuid4(),
        account=q.AccountRecord(user_id=uuid.uuid4(), is_active=True, is_superuser=False),
        email_taken_by_other=False,
        allow_unknown_users=False,
    )
    assert returning.allowed and returning.code == "returning"
    # Same email owned by another user never merges.
    taken = adv.evaluate_oidc_enrollment(
        existing_user_id=uuid.uuid4(),
        account=q.AccountRecord(user_id=uuid.uuid4(), is_active=True),
        email_taken_by_other=True,
        allow_unknown_users=True,
    )
    assert not taken.allowed and taken.code == "enrollment_required"
    # Unknown users need the explicit admission policy.
    denied = adv.evaluate_oidc_enrollment(
        existing_user_id=None, account=None, email_taken_by_other=False,
        allow_unknown_users=False,
    )
    assert not denied.allowed
    allowed_new = adv.evaluate_oidc_enrollment(
        existing_user_id=None, account=None, email_taken_by_other=False,
        allow_unknown_users=True,
    )
    assert allowed_new.allowed and allowed_new.code == "new_enrollment"
    # Inactive and demoted accounts cannot acquire privileges.
    inactive = adv.evaluate_oidc_enrollment(
        existing_user_id=uuid.uuid4(),
        account=q.AccountRecord(user_id=uuid.uuid4(), is_active=False, is_superuser=True),
        email_taken_by_other=False,
        allow_unknown_users=True,
    )
    assert not inactive.allowed and inactive.code == "inactive"


def test_oidc_mfa_cutover_gate():
    config = _config(require_mfa=True)
    no_mfa = _mint_id_token(nonce="n")
    with pytest.raises(adv.OIDCMFACutoverBlockedError):
        adv.validate_id_token(no_mfa, jwk=_HS_JWK, config=config, expected_nonce="n")
    with_mfa = _mint_id_token(nonce="n", acr="urn:mace:incommon:iap:silver", amr=["pwd", "otp"])
    claims = adv.validate_id_token(with_mfa, jwk=_HS_JWK, config=config, expected_nonce="n")
    assert claims["sub"] == "user-ABC-123"


# ---------------------------------------------------------------------------
# acc-4: two instances share single-use transactions, no duplicates/reuse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_instances_share_single_use_transactions(tmp_path):
    config = _config()
    path = tmp_path / "oidc-transactions.json"
    replica_a = adv.FileBackedAuthTransactionStore(path)
    replica_b = adv.FileBackedAuthTransactionStore(path)
    transaction = adv.new_authorization_transaction(config, return_path="/workflows/9")
    await replica_a.create(transaction)
    # Either replica observes the transaction, but only once.
    consumed = await replica_b.consume(transaction.state)
    assert consumed.return_path == "/workflows/9"
    with pytest.raises(adv.OIDCTransactionError):
        await replica_a.consume(transaction.state)
    with pytest.raises(adv.OIDCTransactionError):
        await replica_b.consume(transaction.state)


@pytest.mark.asyncio
async def test_concurrent_callback_race_has_one_winner(tmp_path):
    config = _config()
    path = tmp_path / "oidc-race.json"
    replica_a = adv.FileBackedAuthTransactionStore(path)
    replica_b = adv.FileBackedAuthTransactionStore(path)
    transaction = adv.new_authorization_transaction(config)
    await replica_a.create(transaction)
    results = await asyncio.gather(
        replica_a.consume(transaction.state),
        replica_b.consume(transaction.state),
        return_exceptions=True,
    )
    winners = [r for r in results if isinstance(r, adv.AuthorizationTransaction)]
    losers = [r for r in results if isinstance(r, adv.OIDCTransactionError)]
    assert len(winners) == 1 and len(losers) == 1


@pytest.mark.asyncio
async def test_in_memory_transaction_expiry_and_logout_survives_idp_failure():
    config = _config()
    store = adv.InMemoryAuthTransactionStore(ttl_seconds=600)
    transaction = adv.new_authorization_transaction(config)
    await store.create(transaction)
    with pytest.raises(adv.OIDCTransactionError):
        await store.consume(transaction.state, now=transaction.created_at + 3600)
    # Logout always has a plan without IdP metadata: the MoonMind session
    # dies locally even when the optional IdP logout fails or is absent.
    plan = adv.build_logout_plan(config, None)
    assert plan.idp_logout_url is None and not plan.idp_logout_configured
    assert "best-effort" in plan.note
    plan2 = adv.build_logout_plan(config, _metadata())
    assert plan2.idp_logout_configured and plan2.idp_logout_url is not None

    control_plane = _control_plane_config()
    account_store, revocation, account, identity = _enrolled_store()
    token, user_id = await q.mint_moonmind_session(
        identity, account_store, control_plane, revocation=revocation
    )
    payload = jwt.decode(token, options={"verify_signature": False})
    await revocation.revoke_session(payload["jti"])
    with pytest.raises(q.AuthInvalidError):
        await q.validate_moonmind_session(token, account_store, revocation, control_plane)
    assert user_id == account.user_id


# ---------------------------------------------------------------------------
# acc-5: trusted-proxy stripping/replacement and bypass rejection
# ---------------------------------------------------------------------------


def _proxy_config(**overrides):
    base = {
        "namespace": "corp-sso",
        "trusted_ingress": True,
        "trusted_proxies": ("10.0.0.1",),
    }
    base.update(overrides)
    return adv.TrustedProxyConfig(**base)


def test_trusted_proxy_requires_explicit_ingress():
    with pytest.raises(adv.OIDCConfigError):
        adv.TrustedProxyConfig(namespace="corp-sso", trusted_ingress=False)
    config = adv.resolve_trusted_proxy_config(
        {
            "MOONMIND_TRUSTED_INGRESS": "1",
            "MOONMIND_TRUSTED_PROXIES": "10.0.0.1",
            "MOONMIND_TRUSTED_PROXY_NAMESPACE": "corp-sso",
        }
    )
    assert config.namespace == "corp-sso"
    assert adv.proxy_issuer_for_namespace("corp-sso") == "proxy:corp-sso"


def test_trusted_proxy_extraction_and_bypass_matrix():
    config = _proxy_config()
    issuer, subject = adv.extract_trusted_proxy_identity(
        {"X-MoonMind-Proxy-User": "alice-stable-id"}, config, trusted_ingress=True
    )
    assert issuer == "proxy:corp-sso" and subject == "alice-stable-id"
    # Direct API connections never accept identity headers.
    with pytest.raises(adv.TrustedProxyError):
        adv.extract_trusted_proxy_identity(
            {"X-MoonMind-Proxy-User": "alice"}, config, trusted_ingress=False
        )
    # Duplicated headers (ingress failed to strip/replace) are rejected.
    with pytest.raises(adv.TrustedProxyError):
        adv.extract_trusted_proxy_identity(
            {"X-MoonMind-Proxy-User": ["alice", "bob"]}, config, trusted_ingress=True
        )
    # Missing, reserved, and malformed identities fail closed.
    with pytest.raises(adv.TrustedProxyError):
        adv.extract_trusted_proxy_identity({}, config, trusted_ingress=True)
    for reserved in ("local", "__public__"):
        with pytest.raises(adv.TrustedProxyError):
            adv.extract_trusted_proxy_identity(
                {"X-MoonMind-Proxy-User": reserved}, config, trusted_ingress=True
            )
    for malformed in ("a:b", "a/b", "x\ninjected", "", "y" * 2000):
        with pytest.raises(adv.TrustedProxyError):
            adv.extract_trusted_proxy_identity(
                {"X-MoonMind-Proxy-User": malformed}, config, trusted_ingress=True
            )
    # Email-only identifiers need the explicit enrollment policy.
    with pytest.raises(adv.TrustedProxyError):
        adv.extract_trusted_proxy_identity(
            {"X-MoonMind-Proxy-User": "alice@example.com"}, config, trusted_ingress=True
        )
    email_config = _proxy_config(allow_email_only=True)
    issuer2, subject2 = adv.extract_trusted_proxy_identity(
        {"X-MoonMind-Proxy-User": "alice@example.com"}, email_config, trusted_ingress=True
    )
    assert (issuer2, subject2) == ("proxy:corp-sso", "alice@example.com")


def test_trusted_proxy_enrollment_never_merges_or_reenables():
    config = _proxy_config(allow_unknown_users=True)
    returning = adv.evaluate_trusted_proxy_enrollment(
        existing_user_id=uuid.uuid4(),
        account=q.AccountRecord(user_id=uuid.uuid4(), is_active=True),
        email_taken_by_other=False,
        config=config,
    )
    assert returning.allowed
    unknown_denied = adv.evaluate_trusted_proxy_enrollment(
        existing_user_id=None, account=None, email_taken_by_other=False,
        config=_proxy_config(allow_unknown_users=False),
    )
    assert not unknown_denied.allowed and unknown_denied.code == "enrollment_required"
    taken = adv.evaluate_trusted_proxy_enrollment(
        existing_user_id=None, account=None, email_taken_by_other=True, config=config
    )
    assert not taken.allowed
    disabled = adv.evaluate_trusted_proxy_enrollment(
        existing_user_id=uuid.uuid4(),
        account=q.AccountRecord(user_id=uuid.uuid4(), is_active=False),
        email_taken_by_other=False,
        config=config,
    )
    assert not disabled.allowed and disabled.code == "inactive"
    # Honest revocation semantics: proxy assertions continue upstream.
    assert "disablement" in adv.trusted_proxy_logout_note()
    # Asserted headers and runtime credentials are never forwarded.
    forwarded = adv.strip_asserted_identity_headers(
        {
            "X-MoonMind-Proxy-User": "alice",
            "Authorization": "Bearer worker-secret",
            "Content-Type": "application/json",
        },
        header_names=("X-MoonMind-Proxy-User", "Authorization"),
    )
    assert forwarded == {"Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# acc-6: revocation effective in both modes; no token material in evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revocation_effective_for_oidc_and_proxy_identities():
    control_plane = _control_plane_config()
    for identity in (
        q.ValidatedIdentity(issuer=ISSUER, subject="oidc-user"),
        q.ValidatedIdentity(issuer="proxy:corp-sso", subject="proxy-user"),
    ):
        store = q.InMemoryAsyncAccountStore()
        revocation = q.InMemoryRevocationStore()
        account = q.AccountRecord(user_id=uuid.uuid4(), is_active=True)
        store.enroll(identity, account)
        token, _ = await q.mint_moonmind_session(
            identity, store, control_plane, revocation=revocation
        )
        await revocation.revoke_all_for_user(account.user_id)
        with pytest.raises(q.AuthInvalidError):
            await q.validate_moonmind_session(token, store, revocation, control_plane)


def test_no_raw_token_material_in_diagnostics():
    raw = "eyJhbGciOiJIUzI1NiJ9.payload.signature"
    with pytest.raises(adv.OIDCConfigError):
        adv.assert_no_raw_token({"id_token": raw}, context="test")
    redacted = adv.redacted_oidc_diagnostics(
        {"issuer": ISSUER, "reason": "login_started", "client_secret": "shh"}
    )
    assert redacted["issuer"] == ISSUER
    assert "shh" not in str(redacted)
    assert raw not in str(redacted)


def test_oidc_routes_avoid_legacy_prefix_and_table_ddl_is_idempotent():
    from api_service.api.routers.advanced_auth_4124 import router

    paths = sorted({route.path for route in router.routes if hasattr(route, "path")})
    assert "/api/v1/oidc/login" in paths
    assert "/api/v1/oidc/callback" in paths
    assert "/api/v1/oidc/logout" in paths
    assert not any(p.startswith("/api/v1/auth") for p in paths)
    ddl = adv.ensure_oidc_transaction_table_sql()
    assert "CREATE TABLE IF NOT EXISTS" in ddl
    assert "moonmind_oidc_transactions" in ddl
