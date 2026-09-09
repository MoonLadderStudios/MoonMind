"""Adapter conformance fixtures for K2 (#4118).

Hermetic qualification of the portable Omnigent authentication boundary:
real upstream entrypoints composed in the MoonMind process with explicit
config and injected persistence. No second account database, no remote
runtime request, no whole-server import, no private monkey-patches.

Covers MoonLadderStudios/MoonMind#4118 acceptance dimensions with synthetic
fixtures only.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid

import pytest

from moonmind.omnigent_qualification import (
    SUPPORTED_ENTRYPOINTS,
    UPSTREAM_PIN,
    AccountRecord,
    AuthConfigError,
    EnrollmentRequiredError,
    InMemoryAsyncAccountStore,
    MoonmindQualifiedAuth,
    QualifiedAuthConfig,
    UnsupportedSurfaceError,
    build_conformance_fixtures,
    build_test_config,
    load_upstream_primitives,
    resolve_validated_identity,
    upstream_provenance,
)

ISSUER_A = "https://idp-a.example.invalid"
ISSUER_B = "https://idp-b.example.invalid"


def _enrolled(auth: MoonmindQualifiedAuth, store: InMemoryAsyncAccountStore, **kw):
    user_id = kw.pop("user_id", uuid.uuid4())
    identity = resolve_validated_identity(
        kw.pop("issuer", ISSUER_A),
        kw.pop("subject", "user-4118"),
        provider=kw.pop("provider", "accounts"),
        email=kw.pop("email", None),
    )
    record = AccountRecord(
        user_id=user_id,
        is_active=kw.pop("is_active", True),
        is_superuser=kw.pop("is_superuser", False),
    )
    store.enroll(identity, record)
    return identity, record


# ---------------------------------------------------------------------------
# Pin + supported entrypoints (no whole-server import)
# ---------------------------------------------------------------------------


def test_exact_pin_recorded_and_matches_submodule():
    prov = upstream_provenance()
    assert prov["expected_pin"] == UPSTREAM_PIN == "f04b0354fb5344c1ea8b92795ceb6760a9ad7595"
    assert prov["pin_match"] is True
    assert prov["upstream_commit"] == UPSTREAM_PIN
    assert "no PyPI indirection" in prov["package"]
    for excluded in (
        "omnigent.server.app",
        "omnigent.stores.permission_store",
        "omnigent.server.routes",
        "omnigent.server.device_grant_store",
        "SqlAlchemyAccountStore",
    ):
        assert any(excluded in e for e in prov["excluded"])
    assert "omnigent.server.auth.UnifiedAuthProvider" in prov["supported_entrypoints"]


def test_supported_upstream_entrypoints_importable_without_whole_app():
    prims = load_upstream_primitives()
    assert prims["pin"] == UPSTREAM_PIN
    assert hasattr(prims["auth"], "UnifiedAuthProvider")
    assert hasattr(prims["auth"], "delegated_path_allowed")
    assert callable(prims["oidc"].mint_session_token)
    assert callable(prims["oidc"].hmac_digest)
    assert callable(prims["passwords"].verify_password)
    assert callable(prims["passwords"].hash_password)
    for entry in SUPPORTED_ENTRYPOINTS:
        assert entry in prims["supported_entrypoints"]
    # Whole-server / permission-store / routes surface is never imported here.
    import sys

    for mod in ("omnigent.server.app", "omnigent.stores.permission_store"):
        assert mod not in sys.modules
    assert "omnigent.server.routes.accounts_auth" not in sys.modules
    assert "omnigent.server.routes.auth" not in sys.modules


def test_real_upstream_slice_mints_and_validates():
    """Small real accounts slice through supported upstream entrypoints."""
    prims = load_upstream_primitives()
    secret = bytes.fromhex("cd" * 32)
    cookie_shape = SimpleNamespaceForTest(secret)
    provider = prims["auth"].UnifiedAuthProvider(
        source="accounts",
        oidc_config=None,
        accounts_config=cookie_shape,
        local_single_user=False,
        header_name="X-MoonMind-Test",
        header_strip_prefix="",
    )
    token = prims["oidc"].mint_session_token("alice-4118", secret, 3600, "accounts")
    request = FakeConnection(cookies={cookie_shape.session_cookie_name: token}, path="/")
    assert provider.get_user_id(request) == "alice-4118"


class SimpleNamespaceForTest:
    def __init__(self, secret: bytes):
        self.cookie_secret = secret
        self.session_cookie_name = "ap_session"


class FakeConnection:
    def __init__(self, cookies=None, headers=None, path="/"):
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.url = type("U", (), {"path": path})()


# ---------------------------------------------------------------------------
# Minimal real flow in the MoonMind process (explicit config + injected store)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minimal_moonmind_flow_with_explicit_config():
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    store: InMemoryAsyncAccountStore = fixtures["store"]
    identity, record = _enrolled(auth, store)
    token = auth.mint_session(identity, record)
    result = await auth.validate_session(token)
    assert result.code == "ok"
    assert result.user_id == record.user_id
    # Injected persistence was consulted (no second DB, no remote request).
    assert any(c.startswith("is_revoked:") for c in store.calls)


@pytest.mark.asyncio
async def test_password_hash_supported_or_controlled_enrollment():
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    store: InMemoryAsyncAccountStore = fixtures["store"]
    prims = load_upstream_primitives()
    password_hash = prims["passwords"].hash_password("correct-horse-4118")
    identity = resolve_validated_identity(ISSUER_A, "alice-4118")
    record = AccountRecord(user_id=uuid.uuid4())
    store.enroll(identity, record, password_hash=password_hash, login="alice-4118")
    found_identity, found = await auth.authenticate_account(
        login="alice-4118",
        password="correct-horse-4118",
        issuer=ISSUER_A,
        subject="alice-4118",
    )
    assert found.user_id == record.user_id
    assert found_identity.subject == "alice-4118"
    with pytest.raises(AuthConfigError):
        await auth.authenticate_account(
            login="alice-4118",
            password="wrong-password",
            issuer=ISSUER_A,
            subject="alice-4118",
        )
    # Missing hash is a controlled enrollment/reset requirement, not a bypass.
    identity2 = resolve_validated_identity(ISSUER_A, "bob-4118")
    store.enroll(identity2, AccountRecord(user_id=uuid.uuid4()))
    with pytest.raises(EnrollmentRequiredError):
        await auth.authenticate_account(
            login="bob-4118", password="anything", issuer=ISSUER_A, subject="bob-4118"
        )


# ---------------------------------------------------------------------------
# Verified issuer/subject before minting; no email-only UUID mapping
# ---------------------------------------------------------------------------


def test_store_protocol_takes_validated_identity_not_bare_email():
    fixtures = build_conformance_fixtures()
    store: InMemoryAsyncAccountStore = fixtures["store"]
    sig = inspect.signature(store.resolve_account)
    assert "identity" in sig.parameters
    annotation = sig.parameters["identity"].annotation
    assert "ValidatedIdentity" in str(annotation)

@pytest.mark.asyncio
async def test_authenticate_hook_order_and_no_email_mapping():
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    store: InMemoryAsyncAccountStore = fixtures["store"]
    prims = load_upstream_primitives()
    password_hash = prims["passwords"].hash_password("hook-order-4118")
    identity = resolve_validated_identity(ISSUER_A, "hook-4118", email="same@example.invalid")
    record = AccountRecord(user_id=uuid.uuid4())
    store.enroll(identity, record, password_hash=password_hash, login="hook-4118")
    await auth.authenticate_account(
        login="hook-4118", password="hook-order-4118", issuer=ISSUER_A, subject="hook-4118"
    )
    order = auth.hook_order
    assert order.index("identity.resolve") < order.index("store.resolve_account")
    assert order.index("store.resolve_account") < order.index("password.verify")
    assert order.index("password.verify") < order.index("account.status")


@pytest.mark.asyncio
async def test_same_email_across_issuers_and_case_sensitive_subject():
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    store: InMemoryAsyncAccountStore = fixtures["store"]
    id_a = resolve_validated_identity(ISSUER_A, "Case-4118", email="same@example.invalid")
    id_b = resolve_validated_identity(ISSUER_B, "Case-4118", email="same@example.invalid")
    id_lower = resolve_validated_identity(ISSUER_A, "case-4118", email="same@example.invalid")
    rec_a = AccountRecord(user_id=uuid.uuid4())
    rec_b = AccountRecord(user_id=uuid.uuid4())
    rec_lower = AccountRecord(user_id=uuid.uuid4())
    store.enroll(id_a, rec_a)
    store.enroll(id_b, rec_b)
    store.enroll(id_lower, rec_lower)
    assert (await store.resolve_account(id_a)).user_id == rec_a.user_id
    assert (await store.resolve_account(id_b)).user_id == rec_b.user_id
    assert rec_a.user_id != rec_b.user_id  # no cross-issuer email linking
    assert (await store.resolve_account(id_lower)).user_id == rec_lower.user_id
    assert rec_lower.user_id != rec_a.user_id  # subject is case-sensitive
    # Email rename never changes ownership: mint for the UUID, not the email.
    token = auth.mint_session(id_a, rec_a)
    result = await auth.validate_session(token)
    assert result.user_id == rec_a.user_id


def test_reserved_identities_fail_closed():
    with pytest.raises(AuthConfigError):
        resolve_validated_identity(ISSUER_A, "local")
    with pytest.raises(AuthConfigError):
        resolve_validated_identity(ISSUER_A, "__public__")
    with pytest.raises(AuthConfigError):
        resolve_validated_identity("", "alice")
    with pytest.raises(AuthConfigError):
        resolve_validated_identity(ISSUER_A, "")


# ---------------------------------------------------------------------------
# Async-safe persistence, current user/admin policy, durable revocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inactive_user_rejected_after_credential_validation():
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    store: InMemoryAsyncAccountStore = fixtures["store"]
    identity, record = _enrolled(auth, store, is_active=False)
    token = None
    with pytest.raises(AuthConfigError):
        token = auth.mint_session(identity, record)
    assert token is None
    # Even a directly presented token for an inactive UUID cannot pass when
    # the record is inactive: mint is refused before issuance.


@pytest.mark.asyncio
async def test_durable_revocation_survives_cache_hit():
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    store: InMemoryAsyncAccountStore = fixtures["store"]
    identity, record = _enrolled(auth, store)
    token = auth.mint_session(identity, record)
    first = await auth.validate_session(token)
    assert first.code == "ok" and first.cache_hit is False
    second = await auth.validate_session(token)
    assert second.code == "ok" and second.cache_hit is True
    # Revoke the live session: the next validation (cache hit path) must fail.
    import jwt as pyjwt

    payload = pyjwt.decode(token, options={"verify_signature": False})
    await store.revoke(payload["jti"])
    third = await auth.validate_session(token)
    assert third.code == "auth_invalid" and third.user_id is None


@pytest.mark.asyncio
async def test_sync_store_work_offloaded_without_blocking_loop():
    # The async protocol never blocks the event loop with sync remote DB work:
    # password verification runs in a worker thread via asyncio.to_thread.
    # The heartbeat must tick *while* verification is still running; awaiting
    # verification first and then the heartbeat would pass even for a
    # synchronous blocking implementation.
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    prims = load_upstream_primitives()
    password_hash = prims["passwords"].hash_password("offload-4118")
    ticks: list[float] = []
    stop = asyncio.Event()

    async def heartbeat():
        import time as _time

        while not stop.is_set():
            ticks.append(_time.monotonic())
            await asyncio.sleep(0.01)

    async def verify():
        await auth.verify_password_hash("offload-4118", password_hash)
        stop.set()

    beat = asyncio.ensure_future(heartbeat())
    try:
        await asyncio.gather(verify(), beat)
    finally:
        stop.set()
        if not beat.done():
            beat.cancel()
    # Verification overlaps at least two heartbeat ticks only when it yields
    # the event loop via to_thread; a synchronous inline check would block
    # every tick until it completes.
    assert len(ticks) >= 2


@pytest.mark.asyncio
async def test_no_upstream_admin_roster_promotion():
    """MoonMind admin authority stays server-owned; no additive roster import."""
    fixtures = build_conformance_fixtures()
    store: InMemoryAsyncAccountStore = fixtures["store"]
    identity = resolve_validated_identity(ISSUER_A, "demoted-4118")
    store.enroll(identity, AccountRecord(user_id=uuid.uuid4(), is_superuser=False))
    rec = await store.resolve_account(identity)
    assert rec is not None and rec.is_superuser is False


# ---------------------------------------------------------------------------
# Cached / grant-derived / malformed tokens; unsupported surfaces rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_cannot_bypass_revocation_or_status():
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    store: InMemoryAsyncAccountStore = fixtures["store"]
    identity, record = _enrolled(auth, store)
    token = auth.mint_session(identity, record)
    assert (await auth.validate_session(token)).cache_hit is False
    assert (await auth.validate_session(token)).cache_hit is True
    # Malformed and wrong-key tokens never validate, cached or not.
    assert (await auth.validate_session("not-a-jwt")).code == "auth_invalid"
    assert (await auth.validate_session(token + "tampered")).code == "auth_invalid"
    # A genuinely distinct-key authority (distinct cookie secret, issuer,
    # and audience, mirroring test_control_plane_runtime_cookie_key_purpose
    # _isolation) must reject this token. A same-key instance would
    # correctly validate, so sharing fixtures here would assert wrongly.
    other = MoonmindQualifiedAuth(
        build_test_config(
            cookie_name="mm_runtime_4118",
            cookie_secret=bytes.fromhex("ef" * 32),
            issuer="moonmind-runtime-test",
            audience="moonmind-runtime-api-test",
        ),
        InMemoryAsyncAccountStore(),
    )
    assert (await other.validate_session(token)).code == "auth_invalid"


@pytest.mark.asyncio
async def test_grant_derived_and_foreign_tokens_rejected():
    import jwt as pyjwt

    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    store: InMemoryAsyncAccountStore = fixtures["store"]
    config = auth.config
    now = __import__("time").time()
    base = {
        "sub": str(uuid.uuid4()),
        "iss": config.issuer,
        "aud": config.audience,
        "iat": int(now),
        "exp": int(now) + 3600,
        "jti": "grant-4118",
        "token_use": "moonmind-session",
    }
    for extra in (
        {"grant_id": "g-1"},
        {"scope": "delegated"},
        {"token_type": "refresh"},
        {"token_type": "delegated"},
        {"token_type": "runner"},
        {"refresh_token": True},
    ):
        payload = dict(base, **extra)
        token = pyjwt.encode(payload, config.cookie_secret, algorithm="HS256")
        result = await auth.validate_session(token)
        assert result.code == "auth_invalid", extra
    # Upstream runtime tokens are rejected at the MoonMind boundary.
    prims = load_upstream_primitives()
    upstream_token = prims["oidc"].mint_session_token(
        "alice@example.invalid", config.cookie_secret, 3600, "accounts"
    )
    assert (await auth.validate_session(upstream_token)).code == "auth_invalid"


def test_unsupported_surfaces_explicitly_rejected():
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    with pytest.raises(UnsupportedSurfaceError):
        auth.issue_refresh_token(user_id="x")
    with pytest.raises(UnsupportedSurfaceError):
        auth.mint_runner_token("x", 60)
    assert auth.delegated_token_allowed("/v1/agents") is False


@pytest.mark.asyncio
async def test_conflicting_cookie_bearer_rejected():
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    store: InMemoryAsyncAccountStore = fixtures["store"]
    _, rec_a = _enrolled(auth, store, issuer=ISSUER_A, subject="a-4118")
    _, rec_b = _enrolled(auth, store, issuer=ISSUER_A, subject="b-4118")
    tok_a = auth.mint_session(resolve_validated_identity(ISSUER_A, "a-4118"), rec_a)
    tok_b = auth.mint_session(resolve_validated_identity(ISSUER_A, "b-4118"), rec_b)
    conflict = await auth.validate_request(cookie_token=tok_a, bearer_token=tok_b)
    assert conflict.code == "auth_conflict" and conflict.user_id is None
    missing = await auth.validate_request()
    assert missing.code == "auth_required"


# ---------------------------------------------------------------------------
# Isolation: cookies/keys/purpose/config + hostile ambient environment
# ---------------------------------------------------------------------------


def test_invalid_config_fails_closed():
    with pytest.raises(AuthConfigError):
        build_test_config(cookie_name="__Host-ap_session")
    with pytest.raises(AuthConfigError):
        build_test_config(cookie_name="ap_session")
    with pytest.raises(AuthConfigError):
        build_test_config(cookie_secret=b"short")
    with pytest.raises(AuthConfigError):
        build_test_config(mode="keycloak")
    with pytest.raises(AuthConfigError):
        build_test_config(issuer="  ")
    with pytest.raises(AuthConfigError):
        build_test_config(session_ttl_seconds=0)


def test_hostile_ambient_omnigent_env_does_not_select_behavior(monkeypatch):
    import sys

    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "oidc")
    monkeypatch.setenv("OMNIGENT_AUTH_ENABLED", "1")
    monkeypatch.setenv("OMNIGENT_OIDC_ISSUER", "https://evil.example.invalid")
    monkeypatch.setenv("OMNIGENT_ACCOUNTS_COOKIE_SECRET", "00" * 32)
    monkeypatch.setenv("OMNIGENT_LOCAL_SINGLE_USER", "1")
    monkeypatch.setenv("OMNIGENT_AUTH_HEADER", "X-Evil")
    # Earlier tests in this file already imported the pinned upstream modules,
    # so the hostile variables must be proven ineffective on a fresh import,
    # not against the cached modules.
    cleared = [mod for mod in list(sys.modules) if mod.startswith("omnigent.server.")]
    saved = {mod: sys.modules.pop(mod) for mod in cleared}
    try:
        fixtures = build_conformance_fixtures()
    finally:
        sys.modules.update(saved)
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    assert auth.config.mode == "accounts"
    assert auth.config.cookie_name == "mm_session_4118"
    # Header fallback stays disabled even with the single-user marker set.
    assert auth.upstream_provider._local_single_user is False


@pytest.mark.asyncio
async def test_control_plane_runtime_cookie_key_purpose_isolation():
    control = build_conformance_fixtures()["auth"]
    runtime = MoonmindQualifiedAuth(
        build_test_config(
            cookie_name="mm_runtime_4118",
            cookie_secret=bytes.fromhex("ef" * 32),
            issuer="moonmind-runtime-test",
            audience="moonmind-runtime-api-test",
        ),
        InMemoryAsyncAccountStore(),
    )
    store = control.store
    identity, record = _enrolled(control, store)
    token = control.mint_session(identity, record)
    assert (await runtime.validate_session(token)).code == "auth_invalid"
    assert (await control.validate_session(token)).code == "ok"


@pytest.mark.asyncio
async def test_oidc_and_header_modes_construct_and_validate():
    for mode in ("oidc", "header"):
        store = InMemoryAsyncAccountStore()
        config = build_test_config(mode=mode)
        auth = MoonmindQualifiedAuth(config, store)
        assert auth.config.mode == mode
        identity = resolve_validated_identity(
            ISSUER_A, f"{mode}-user-4118", provider=mode
        )
        record = AccountRecord(user_id=uuid.uuid4())
        store.enroll(identity, record)
        token = auth.mint_session(identity, record)
        result = await auth.validate_session(token)
        assert result.code == "ok", mode
        assert result.user_id == record.user_id, mode


@pytest.mark.asyncio
async def test_password_verification_bound_to_resolved_account():
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    store: InMemoryAsyncAccountStore = fixtures["store"]
    prims = load_upstream_primitives()
    password_hash = prims["passwords"].hash_password("owner-secret-4118")
    owner_identity = resolve_validated_identity(ISSUER_A, "owner-4118")
    owner_record = AccountRecord(user_id=uuid.uuid4())
    store.enroll(owner_identity, owner_record)
    other_identity = resolve_validated_identity(ISSUER_A, "other-4118")
    other_record = AccountRecord(user_id=uuid.uuid4())
    store.enroll(
        other_identity, other_record, password_hash=password_hash, login="other-4118"
    )
    # The owner's password must not authenticate as the other account.
    with pytest.raises(AuthConfigError):
        await auth.authenticate_account(
            login="other-4118",
            password="owner-secret-4118",
            issuer=ISSUER_A,
            subject="owner-4118",
        )


@pytest.mark.asyncio
async def test_session_without_expiry_rejected():
    import jwt as pyjwt
    import time as _time

    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    config = auth.config
    payload = {
        "sub": str(uuid.uuid4()),
        "iss": config.issuer,
        "aud": config.audience,
        "provider": "accounts",
        "token_use": "moonmind-session",
        "jti": "no-exp-4118",
        "iat": int(_time.time()),
    }
    token = pyjwt.encode(payload, config.cookie_secret, algorithm="HS256")
    assert (await auth.validate_session(token)).code == "auth_invalid"


@pytest.mark.asyncio
async def test_disabled_account_rechecked_on_full_validation():
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    store: InMemoryAsyncAccountStore = fixtures["store"]
    identity, record = _enrolled(auth, store)
    token = auth.mint_session(identity, record)
    assert (await auth.validate_session(token)).code == "ok"
    # Disable without revoking the JTI: full validation must still refuse.
    auth._cache.clear()
    stored = await store.get_account_by_id(record.user_id)
    assert stored is not None
    object.__setattr__(stored, "is_active", False)
    assert (await auth.validate_session(token)).code == "auth_invalid"


@pytest.mark.asyncio
async def test_store_outage_maps_to_unavailable():
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    store: InMemoryAsyncAccountStore = fixtures["store"]
    identity, record = _enrolled(auth, store)
    token = auth.mint_session(identity, record)

    async def _boom(_token_id: str) -> bool:
        raise ConnectionError("durable store unavailable")

    store.is_revoked = _boom  # type: ignore[method-assign]
    assert (await auth.validate_session(token)).code == "unavailable"
    assert (
        await auth.validate_request(cookie_token=token)
    ).code == "unavailable"


def test_session_cache_evicts_expired_entries():
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    import time as _time

    auth._cache["stale-4118"] = ("user", _time.monotonic() - 1.0, "jti-stale")
    auth._cache["fresh-4118"] = ("user", _time.monotonic() + 60.0, "jti-fresh")
    auth._evict_expired_cache()
    assert "stale-4118" not in auth._cache
    assert "fresh-4118" in auth._cache


def test_configured_cookie_name_reaches_upstream_provider():
    store = InMemoryAsyncAccountStore()
    config = build_test_config(cookie_name="mm_custom_4118")
    auth = MoonmindQualifiedAuth(config, store)
    provider = auth.upstream_provider
    names = set()
    for attr in ("session_cookie_name", "cookie_name", "session_cookie"):
        value = getattr(provider, attr, None)
        if isinstance(value, str):
            names.add(value)
    oidc_cfg = getattr(provider, "oidc_config", None)
    accounts_cfg = getattr(provider, "accounts_config", None)
    for cfg in (oidc_cfg, accounts_cfg):
        if cfg is not None:
            for attr in ("session_cookie_name", "cookie_name"):
                value = getattr(cfg, attr, None)
                if isinstance(value, str):
                    names.add(value)
    assert "mm_custom_4118" in names
    assert "__Host-ap_session" not in names
    assert "ap_session" not in names


@pytest.mark.asyncio
async def test_revocation_through_store_protocol():
    fixtures = build_conformance_fixtures()
    auth: MoonmindQualifiedAuth = fixtures["auth"]
    store: InMemoryAsyncAccountStore = fixtures["store"]
    identity, record = _enrolled(auth, store)
    token = auth.mint_session(identity, record)
    assert (await auth.validate_session(token)).code == "ok"
    import jwt as pyjwt

    payload = pyjwt.decode(token, options={"verify_signature": False})
    await store.revoke(payload["jti"])
    assert (await auth.validate_session(token)).code == "auth_invalid"


def test_conformance_fixture_factory_shape_for_later_issues():
    fixtures = build_conformance_fixtures()
    assert set(fixtures) == {"config", "store", "auth", "pin"}
    assert isinstance(fixtures["config"], QualifiedAuthConfig)
    assert isinstance(fixtures["store"], InMemoryAsyncAccountStore)
    assert isinstance(fixtures["auth"], MoonmindQualifiedAuth)
    assert fixtures["pin"] == UPSTREAM_PIN
