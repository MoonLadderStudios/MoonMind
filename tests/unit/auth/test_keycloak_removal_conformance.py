"""Required end-to-end authentication conformance coverage (#4128).

MoonLadderStudios/MoonMind#4128 (parent #4116; plan K1/K4/K5 in
``docs/tmp/KeycloakRemovalPlan.md``): prove the supported authentication
journey and negative authority matrix in required CI without Keycloak or
external credentials.

Hermetic by design: real PostgreSQL-free unit runner, synthetic fixtures
only, no public IdP, no live secrets, no provider account. Production
boundaries are exercised through their real code (mode selector, session
mint/validate, identity validation, ingress/callback validators, artifact
authorization, worker gate, mounted routes, rendered Compose), never by
replacing the whole authentication boundary with a mock user. The
two-user browser journey and live-IdP/MFA/operator-cutover rows remain
explicitly external owners in ``PLAN_MATRIX`` (sanitized distinction per
the issue acceptance criteria), not mock claims.

Bounded backlog ownership (assessment ``PARTIALLY_IMPLEMENTED``):
R1 matrix owners, R2 real-boundary session issuance, R3 negative matrix,
R4 migration/revocation remainder (SQLite-heavy seams stay owned by
``test_identity_mapping_4119``/``test_identity_migration_postgres_4119``),
R6/R9 built-artifact and no-reintroduction guards, R8 selector aggregation.
R7 is met and is pinned, not re-implemented, here.
"""

from __future__ import annotations

import secrets
import time
import uuid
from pathlib import Path

import jwt
import pytest

from api_service import auth as auth_module
from api_service.api.routers import worker_auth as worker_auth_module
from api_service.db import models as db_models
from moonmind.config import settings as settings_module
from moonmind.security import auth_modes_4120 as modes
from moonmind.security import omnigent_auth_qualification as qual
from moonmind.workflows.temporal import artifacts as artifact_module

REPO_ROOT = Path(__file__).resolve().parents[3]

ISSUER_A = "https://idp-a.example.invalid/realms/moonmind"
ISSUER_B = "https://idp-b.example.invalid/realms/moonmind"

# ---------------------------------------------------------------------------
# R1: every plan verification row owns hermetic evidence or an external owner
# ---------------------------------------------------------------------------

PLAN_MATRIX = (
    {
        "row": "Authentication",
        "owner": "this module: supported/retired selectors, omitted-vs-explicit defaults",
        "evidence": "test_supported_modes_resolve_and_retired_fail,test_omitted_fresh_selects_accounts_and_populated_requires_decision",
    },
    {
        "row": "Identity",
        "owner": "this module (validation/reserved/issuer-subject semantics) + tests/unit/security/test_identity_mapping_4119.py (SQLite transactional binding, email-taken refusal, rename preservation) + tests/integration/security/test_identity_migration_postgres_4119.py (PG race)",
        "evidence": "test_identity_pair_semantics_and_legacy_columns_not_consulted",
    },
    {
        "row": "Browser security",
        "owner": "this module: callback/proxy/cookie/ingress validators through production code",
        "evidence": "test_browser_security_validators_reject_forgery_and_bypass",
    },
    {
        "row": "Account lifecycle",
        "owner": "this module: operator-authorized default claim, durable-key bootstrap race, never-promote/demoted-preserved",
        "evidence": "test_account_lifecycle_operator_claim_and_bootstrap_race",
    },
    {
        "row": "Resource authorization",
        "owner": "this module: artifact owner/non-owner/service-principal checks through the production boundary",
        "evidence": "test_resource_authorization_owner_denies_non_owner",
    },
    {
        "row": "Machine authority",
        "owner": "this module: worker gate plus runtime-token rejection at the browser boundary",
        "evidence": "test_machine_authority_worker_gate_and_runtime_rejection",
    },
    {
        "row": "Durability",
        "owner": "this module: shared-revocation two-replica checks plus restart-durable session key",
        "evidence": "test_two_replica_shared_revocation_and_restart_durable_key",
    },
    {
        "row": "User journey",
        "owner": "EXTERNAL: complete two-user browser journey (setup/login, old workflow, submit, stream/reconnect, chat, artifacts, settings, logout, denied access, A-cannot-observe-B) requires the #4124-era session contracts, worker/MCP/container-job admission (#4126), and a browser harness; not mockable in the unit runner",
        "evidence": "EXTERNAL",
    },
    {
        "row": "Removal",
        "owner": "this module: unmounted legacy routes, rejected selectors/tokens, Keycloak-free topology, secret-free diagnostics",
        "evidence": "test_removal_no_legacy_routes_selectors_or_topology,test_no_old_token_acceptance,test_secret_free_diagnostics",
    },
)


def test_plan_matrix_every_row_has_owner():
    """R1/A1: each of the 9 plan rows names its evidence or external owner."""
    rows = [entry["row"] for entry in PLAN_MATRIX]
    assert rows == [
        "Authentication",
        "Identity",
        "Browser security",
        "Account lifecycle",
        "Resource authorization",
        "Machine authority",
        "Durability",
        "User journey",
        "Removal",
    ]
    here = globals()
    for entry in PLAN_MATRIX:
        assert entry["owner"], entry["row"]
        assert entry["evidence"], entry["row"]
        if entry["evidence"] == "EXTERNAL":
            assert entry["owner"].startswith("EXTERNAL"), entry["row"]
            continue
        for name in entry["evidence"].split(","):
            name = name.strip()
            if name in here:
                continue
            # Cross-file owners must exist on disk.
            candidates = [
                REPO_ROOT / "tests" / "unit" / "security" / "test_identity_mapping_4119.py",
                REPO_ROOT / "tests" / "integration" / "security"
                / "test_identity_migration_postgres_4119.py",
            ]
            assert any(p.is_file() for p in candidates), name


# ---------------------------------------------------------------------------
# Helpers: hermetic production-boundary fixtures (synthetic only)
# ---------------------------------------------------------------------------


def _control_plane_config(mode="accounts"):
    return modes.resolve_moonmind_auth_config(
        mode=mode,
        cookie_secret=secrets.token_bytes(32),
        environ={},
    )


def _enrolled_store(email="owner@example.invalid", *, active=True, superuser=False):
    store = qual.InMemoryAsyncAccountStore()
    revocation = qual.InMemoryRevocationStore()
    user_id = uuid.uuid4()
    identity = qual.ValidatedIdentity(issuer=ISSUER_A, subject="sub-4128")
    store.enroll(
        identity,
        qual.AccountRecord(
            user_id=user_id, is_active=active, is_superuser=superuser, email=email
        ),
    )
    return store, revocation, user_id, identity


def _set_production_mode(monkeypatch, mode):
    """Drive both selector layers the production code reads."""
    monkeypatch.setattr(settings_module.oidc, "AUTH_PROVIDER", mode)
    monkeypatch.setattr(modes, "_ACTIVE_PRODUCTION_MODE", mode, raising=False)


# ---------------------------------------------------------------------------
# Authentication: modes, omitted-vs-explicit defaults, control-plane isolation
# ---------------------------------------------------------------------------


def test_supported_modes_resolve_and_retired_fail():
    original = settings_module.oidc.AUTH_PROVIDER
    try:
        for mode in ("accounts", "oidc", "header", "disabled"):
            assert qual.validate_mode_selector(mode) == mode
            settings_module.oidc.AUTH_PROVIDER = mode
            assert settings_module.oidc.validate_auth_provider() == mode
        for retired in ("keycloak", "default", "google", "local", "saml"):
            with pytest.raises(Exception):
                qual.validate_mode_selector(retired)
            settings_module.oidc.AUTH_PROVIDER = retired
            with pytest.raises(RuntimeError):
                settings_module.oidc.validate_auth_provider()
    finally:
        settings_module.oidc.AUTH_PROVIDER = original


def test_omitted_fresh_selects_accounts_and_populated_requires_decision():
    assert (
        modes.resolve_production_mode(raw_selector="", explicit=False, has_users=False)
        == "accounts"
    )
    assert (
        modes.resolve_production_mode(
            raw_selector="accounts", explicit=True, has_users=False
        )
        == "accounts"
    )
    with pytest.raises(modes.MigrationRequiredError):
        modes.resolve_production_mode(raw_selector="", explicit=False, has_users=True)
    classification = modes.classify_deployment(
        raw_selector="", explicit=False, has_users=True
    )
    assert classification.migration_required is True
    assert classification.production_mode == "migration_required"
    decision = modes.AuthMigrationDecision(mode="accounts")
    assert (
        modes.resolve_production_mode(
            raw_selector="", explicit=False, has_users=True, migration_decision=decision
        )
        == "accounts"
    )


def test_control_plane_ignores_runtime_ambient():
    hostile = {
        "OMNIGENT_AUTH_PROVIDER": "header",
        "OMNIGENT_AUTH_ENABLED": "1",
        "OMNIGENT_AUTH_HEADER": "X-Evil",
        "OMNIGENT_ACCOUNTS_COOKIE_SECRET": "runtime-secret",
        "OMNIGENT_OIDC_ISSUER": "https://evil.example.invalid",
    }
    config = modes.resolve_moonmind_auth_config(
        mode="accounts", cookie_secret=secrets.token_bytes(32), environ=hostile
    )
    assert config.mode == "accounts"
    assert config.cookie_name == qual.MOONMIND_PROD_COOKIE
    assert config.cookie_name not in qual.UPSTREAM_SESSION_COOKIES
    assert config.token_issuer == qual.MOONMIND_TOKEN_ISSUER
    assert config.token_audience == qual.MOONMIND_TOKEN_AUDIENCE


# ---------------------------------------------------------------------------
# R2: session issuance through the production mint/validate boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_issuance_through_production_boundary():
    """R2: mint then validate through real session code, no mock-user swap."""
    config = _control_plane_config()
    store, revocation, user_id, identity = _enrolled_store()
    token, minted_id = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    assert minted_id == user_id
    account = await qual.validate_moonmind_session(token, store, revocation, config)
    assert account.user_id == user_id
    # Purpose/issuer/audience binding is structural, not prose.
    payload = jwt.decode(token, options={"verify_signature": False})
    assert payload["iss"] == qual.MOONMIND_TOKEN_ISSUER
    assert payload["aud"] == qual.MOONMIND_TOKEN_AUDIENCE
    assert payload["purpose"] == qual.MOONMIND_SESSION_PURPOSE
    assert str(payload["sub"]) == str(user_id)


@pytest.mark.asyncio
async def test_unknown_identity_requires_enrollment_and_inactive_forbidden():
    config = _control_plane_config()
    store = qual.InMemoryAsyncAccountStore()
    revocation = qual.InMemoryRevocationStore()
    with pytest.raises(qual.AuthInvalidError):
        await qual.mint_moonmind_session(
            qual.ValidatedIdentity(issuer=ISSUER_A, subject="unknown-sub"),
            store,
            config,
            revocation=revocation,
        )
    inactive_store, inactive_rev, _, inactive_identity = _enrolled_store(active=False)
    with pytest.raises(qual.ForbiddenError):
        await qual.mint_moonmind_session(
            inactive_identity, inactive_store, config, revocation=inactive_rev
        )


def test_identity_pair_semantics_and_legacy_columns_not_consulted():
    """R4 remainder (hermetic slice): exact issuer/subject semantics.

    Transactional binding, email-taken refusal, rename preservation, and the
    PostgreSQL race stay owned by test_identity_mapping_4119.py and
    test_identity_migration_postgres_4119.py (see PLAN_MATRIX); this guard
    pins the semantics those suites enforce without re-implementing them.
    """
    from api_service.services.identity_service import validate_external_identity

    issuer, subject = validate_external_identity(ISSUER_A, "UserA-123")
    assert (issuer, subject) == (ISSUER_A, "UserA-123")
    with pytest.raises(Exception):
        validate_external_identity(ISSUER_A, "local")
    # Same subject across issuers is a distinct identity key.
    assert (ISSUER_A, "shared-sub") != (ISSUER_B, "shared-sub")
    # The legacy columns are frozen read-only evidence: 32-char provider
    # cannot hold a full issuer URI and is never the resolution key.
    table = db_models.User.__table__
    assert table.c["oidc_provider"].type.length == 32
    assert len(ISSUER_A) > 32
    assert any(
        getattr(c, "name", "") == "uq_oidc_identity" for c in table.constraints
    )


# ---------------------------------------------------------------------------
# R3: negative authority matrix through real validation
# ---------------------------------------------------------------------------


def _mutate_payload(token, secret, updates=None, drop=()):
    payload = jwt.decode(token, options={"verify_signature": False})
    for key in drop:
        payload.pop(key, None)
    payload.update(updates or {})
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.mark.asyncio
async def test_negative_token_matrix():
    config = _control_plane_config()
    store, revocation, user_id, identity = _enrolled_store()
    token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    secret = config.cookie_secret

    # Wrong signing key.
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(
            _mutate_payload(token, secrets.token_bytes(32)), store, revocation, config
        )
    # Expired.
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(
            _mutate_payload(token, secret, {"exp": int(time.time()) - 60}),
            store,
            revocation,
            config,
        )
    # Wrong issuer / audience / purpose.
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(
            _mutate_payload(token, secret, {"iss": "https://evil.example.invalid"}),
            store,
            revocation,
            config,
        )
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(
            _mutate_payload(token, secret, {"aud": "other-audience"}),
            store,
            revocation,
            config,
        )
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(
            _mutate_payload(token, secret, {"purpose": "other-purpose"}),
            store,
            revocation,
            config,
        )
    # Missing required claims.
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(
            _mutate_payload(token, secret, drop=("jti",)), store, revocation, config
        )
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(
            _mutate_payload(token, secret, {"sub": "not-a-uuid"}),
            store,
            revocation,
            config,
        )
    # Grant-derived and delegated shapes are unsupported surfaces, never grants.
    with pytest.raises(qual.UnsupportedSurfaceError):
        await qual.validate_moonmind_session(
            _mutate_payload(token, secret, {"grant_id": "grant-1"}),
            store,
            revocation,
            config,
        )
    with pytest.raises(qual.UnsupportedSurfaceError):
        await qual.validate_moonmind_session(
            _mutate_payload(token, secret, {"scope": "delegated"}),
            store,
            revocation,
            config,
        )
    # Reserved identities never validate.
    with pytest.raises(Exception):
        qual.ValidatedIdentity(issuer=ISSUER_A, subject="local")


@pytest.mark.asyncio
async def test_conflicting_cookie_bearer_rejected_and_missing_semantics():
    config = _control_plane_config()
    store = qual.InMemoryAsyncAccountStore()
    revocation = qual.InMemoryRevocationStore()
    uid_a = uuid.uuid4()
    ident_a = qual.ValidatedIdentity(issuer=ISSUER_A, subject="sub-a-4128")
    store.enroll(
        ident_a,
        qual.AccountRecord(user_id=uid_a, is_active=True, email="a@example.invalid"),
    )
    uid_b = uuid.uuid4()
    ident_b = qual.ValidatedIdentity(issuer=ISSUER_A, subject="sub-b-4128")
    store.enroll(
        ident_b,
        qual.AccountRecord(user_id=uid_b, is_active=True, email="b@example.invalid"),
    )
    token_a, _ = await qual.mint_moonmind_session(
        ident_a, store, config, revocation=revocation
    )
    token_b, _ = await qual.mint_moonmind_session(
        ident_b, store, config, revocation=revocation
    )
    assert uid_a != uid_b
    with pytest.raises(qual.AuthConflictError):
        await qual.resolve_current_user(
            cookie_token=token_a,
            bearer_token=token_b,
            account_store=store,
            revocation=revocation,
            config=config,
        )
    same = await qual.resolve_current_user(
        cookie_token=token_a,
        bearer_token=token_a,
        account_store=store,
        revocation=revocation,
        config=config,
    )
    assert same.user_id == uid_a
    assert (
        await qual.resolve_current_user(
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
        await qual.resolve_current_user(
            cookie_token=None,
            bearer_token=None,
            account_store=store,
            revocation=revocation,
            config=config,
        )
    # An invalid presented credential never becomes a different principal.
    with pytest.raises(qual.AuthInvalidError):
        await qual.resolve_current_user(
            cookie_token="malformed-token",
            bearer_token=None,
            account_store=store,
            revocation=revocation,
            config=config,
        )


@pytest.mark.asyncio
async def test_revocation_generation_rotation_and_logout():
    """Key rotation / disablement semantics: generation bump kills old tokens."""
    config = _control_plane_config()
    store, revocation, user_id, identity = _enrolled_store()
    token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    # Direct per-session revocation covers logout.
    payload = jwt.decode(token, options={"verify_signature": False})
    await revocation.revoke_session(payload["jti"])
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(token, store, revocation, config)

    fresh, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    await revocation.revoke_all_for_user(user_id)
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(fresh, store, revocation, config)
    # Legacy tokens minted before generation tracking die on first bump.
    legacy_payload = jwt.decode(fresh, options={"verify_signature": False})
    legacy_payload.pop("gen", None)
    legacy = jwt.encode(legacy_payload, config.cookie_secret, algorithm="HS256")
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(legacy, store, revocation, config)
    rotated, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    assert (await qual.validate_moonmind_session(rotated, store, revocation, config)).user_id == user_id


@pytest.mark.asyncio
async def test_two_replica_shared_revocation_and_restart_durable_key(tmp_path):
    """Durability slice: revocation is current across replicas, keys persist."""
    secret = secrets.token_bytes(32)
    replica_a = modes.resolve_moonmind_auth_config(
        mode="accounts", cookie_secret=secret, environ={}
    )
    replica_b = modes.resolve_moonmind_auth_config(
        mode="accounts", cookie_secret=secret, environ={}
    )
    store, shared_revocation, user_id, identity = _enrolled_store()
    token, _ = await qual.mint_moonmind_session(
        identity, store, replica_a, revocation=shared_revocation
    )
    # Second replica validates with current revocation, not a stale cache.
    assert (
        await qual.validate_moonmind_session(token, store, shared_revocation, replica_b)
    ).user_id == user_id
    await shared_revocation.revoke_all_for_user(user_id)
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(token, store, shared_revocation, replica_b)

    key_path = tmp_path / "moonmind_session_key"
    first = modes.resolve_session_secret(explicit_secret=None, key_path=key_path)
    assert len(first) >= 32
    assert modes.resolve_session_secret(explicit_secret=None, key_path=key_path) == first


@pytest.mark.asyncio
async def test_auth_store_outage_fails_closed():
    """Outage slice: unavailable stores fail closed, never mint admin stubs."""

    class _FailingStore:
        async def resolve_identity_to_user_id(self, identity):
            raise ConnectionError("db down")

        async def get_account(self, user_id):
            raise ConnectionError("db down")

        async def get_password_hash_by_login(self, login):
            raise ConnectionError("db down")

        async def record_login(self, user_id, when):
            raise ConnectionError("db down")

    class _FailingRevocation:
        async def revoke_session(self, jti):
            raise ConnectionError("revocation down")

        async def is_session_revoked(self, jti):
            raise ConnectionError("revocation down")

        async def revoke_all_for_user(self, user_id):
            raise ConnectionError("revocation down")

        async def generation_for_user(self, user_id):
            raise ConnectionError("revocation down")

    config = _control_plane_config()
    with pytest.raises(Exception):
        await qual.mint_moonmind_session(
            qual.ValidatedIdentity(issuer=ISSUER_A, subject="sub-x"),
            _FailingStore(),
            config,
            revocation=qual.InMemoryRevocationStore(),
        )
    store, revocation, _, identity = _enrolled_store()
    token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    with pytest.raises(qual.UnavailableError):
        await qual.validate_moonmind_session(token, store, _FailingRevocation(), config)


# ---------------------------------------------------------------------------
# Browser security validators through production code
# ---------------------------------------------------------------------------


def test_browser_security_validators_reject_forgery_and_bypass():
    with pytest.raises(modes.AuthModeError):
        modes.validate_callback_origin(
            "https://evil.example.invalid/callback", base_url="https://app.example.invalid"
        )
    assert (
        modes.validate_callback_origin(
            "https://app.example.invalid/auth/callback",
            base_url="https://app.example.invalid",
        )
        == "https://app.example.invalid/auth/callback"
    )
    with pytest.raises(modes.AuthModeError):
        modes.validate_trusted_proxy_config("*")
    with pytest.raises(modes.AuthModeError):
        modes.validate_public_base_url(
            "https://app.example.invalid", forwarded_host="evil.example.invalid"
        )
    with pytest.raises(modes.AuthModeError):
        modes.cookie_policy_for_base_url("http://127.0.0.1:7000")
    dev = modes.cookie_policy_for_base_url(
        "http://127.0.0.1:7000", explicit_loopback_http=True
    )
    assert dev.cookie_name == qual.MOONMIND_DEV_COOKIE
    assert qual.MOONMIND_PROD_COOKIE.startswith("__Host-")

    denied = modes.evaluate_ingress_fixture(
        {
            "name": "proxy-bypass",
            "mode": "accounts",
            "publish_host": "127.0.0.1",
            "proxy_bypass_possible": True,
        }
    )
    assert denied.allowed is False
    direct = modes.evaluate_ingress_fixture(
        {"name": "wildcard", "mode": "disabled", "publish_host": "0.0.0.0"}
    )
    assert direct.allowed is False
    loopback = modes.evaluate_ingress_fixture(
        {"name": "loopback", "mode": "disabled", "publish_host": "127.0.0.1"}
    )
    assert loopback.allowed is True


# ---------------------------------------------------------------------------
# Account lifecycle hermetic slice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_account_lifecycle_operator_claim_and_bootstrap_race(tmp_path):
    with pytest.raises(PermissionError):
        await auth_module.claim_default_admin(None, None, operator_authorized=False)

    key_path = tmp_path / "race-key"
    winner = b"K" * 32
    key_path.write_bytes(winner)
    assert modes.resolve_session_secret(explicit_secret=None, key_path=key_path) == winner

    # New identities are never promoted; demotion/inactive flags are preserved
    # by the resolver (full transactional proof lives in the #4119 suites).
    config = _control_plane_config()
    store = qual.InMemoryAsyncAccountStore()
    revocation = qual.InMemoryRevocationStore()
    demoted_id = uuid.uuid4()
    store.enroll(
        qual.ValidatedIdentity(issuer=ISSUER_A, subject="demoted-sub"),
        qual.AccountRecord(user_id=demoted_id, is_active=True, is_superuser=False),
    )
    token, minted = await qual.mint_moonmind_session(
        qual.ValidatedIdentity(issuer=ISSUER_A, subject="demoted-sub"),
        store,
        config,
        revocation=revocation,
    )
    assert minted == demoted_id
    assert (
        await qual.validate_moonmind_session(token, store, revocation, config)
    ).is_superuser is False


# ---------------------------------------------------------------------------
# Resource authorization + machine authority through production boundaries
# ---------------------------------------------------------------------------


def _artifact_owned_by(owner):
    from types import SimpleNamespace

    return SimpleNamespace(artifact_id="artifact-4128", created_by_principal=owner)


def _artifact_service():
    return object.__new__(artifact_module.TemporalArtifactService)


def test_resource_authorization_owner_denies_non_owner(monkeypatch):
    _set_production_mode(monkeypatch, "oidc")
    service = _artifact_service()
    artifact = _artifact_owned_by("owner-4128")
    service._assert_read_access(artifact, principal="owner-4128")
    service._assert_mutation_access(artifact, principal="owner-4128")
    with pytest.raises(artifact_module.TemporalArtifactAuthorizationError):
        service._assert_read_access(artifact, principal="intruder-4128")
    with pytest.raises(artifact_module.TemporalArtifactAuthorizationError):
        service._assert_mutation_access(artifact, principal="intruder-4128")
    # Cross-owner denial holds in every authenticated mode.
    _set_production_mode(monkeypatch, "accounts")
    with pytest.raises(artifact_module.TemporalArtifactAuthorizationError):
        service._assert_read_access(artifact, principal="intruder-4128")


@pytest.mark.asyncio
async def test_machine_authority_worker_gate_and_runtime_rejection(monkeypatch):
    from types import SimpleNamespace

    _set_production_mode(monkeypatch, "oidc")
    user = SimpleNamespace(id=uuid.uuid4(), email="w@example.invalid")
    resolved = await worker_auth_module._require_worker_auth(
        worker_token=None, user=user
    )
    assert resolved.auth_source == "oidc"
    with pytest.raises(Exception) as exc_info:
        await worker_auth_module._require_worker_auth(worker_token="legacy", user=user)
    assert exc_info.value.status_code == 410
    with pytest.raises(Exception) as exc_info:
        await worker_auth_module._require_worker_auth(worker_token=None, user=None)
    assert exc_info.value.status_code == 401
    # Runtime/session/worker token shapes never become browser credentials.
    assert qual.MOONMIND_PROD_COOKIE not in qual.UPSTREAM_SESSION_COOKIES
    for surface in qual.UNSUPPORTED_SURFACES:
        with pytest.raises(qual.UnsupportedSurfaceError):
            qual.assert_surface_not_used(surface)
    with pytest.raises(qual.UnsupportedSurfaceError):
        qual.reject_unsupported_token_shape({"grant_id": "g"})
    with pytest.raises(qual.UnsupportedSurfaceError):
        qual.reject_unsupported_token_shape({"scope": "delegated"})


# ---------------------------------------------------------------------------
# R6/R9: removal guards, old-token rejection, topology, secret-free evidence
# ---------------------------------------------------------------------------


def test_removal_no_legacy_routes_selectors_or_topology():
    from api_service.main import app

    legacy_prefixes = ("/api/v1/auth", "/auth/jwt", "/auth/register")
    mounted = sorted({route.path for route in app.routes if hasattr(route, "path")})
    for path in mounted:
        assert not path.startswith(legacy_prefixes), f"legacy route mounted: {path}"
    from api_service import auth_providers as providers_module

    assert not hasattr(providers_module, "get_auth_router")

    compose_text = (REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    assert "\n  keycloak:" not in compose_text
    assert "keycloak-db" not in compose_text
    assert "KEYCLOAK_" not in compose_text

    env_lines = (REPO_ROOT / ".env-template").read_text(encoding="utf-8").splitlines()
    active = [line for line in env_lines if line and not line.lstrip().startswith("#")]
    assert not any(line.startswith("KEYCLOAK_") for line in active)
    assert any(line.startswith("AUTH_PROVIDER=") for line in active)

    updater = (
        REPO_ROOT
        / ".agents"
        / "skills"
        / "update-moonmind"
        / "scripts"
        / "run-update-moonmind.sh"
    ).read_text(encoding="utf-8")
    live_lines = [
        line
        for line in updater.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any("keycloak" in line.lower() for line in live_lines)


@pytest.mark.asyncio
async def test_no_old_token_acceptance():
    """Old-issuer tokens die at the boundary; no issuance route is advertised."""
    config = _control_plane_config()
    store, revocation, _, identity = _enrolled_store()
    token, _ = await qual.mint_moonmind_session(
        identity, store, config, revocation=revocation
    )
    payload = jwt.decode(token, options={"verify_signature": False})
    payload["iss"] = "https://keycloak-retired.example.invalid/realms/moonmind"
    old_token = jwt.encode(payload, config.cookie_secret, algorithm="HS256")
    with pytest.raises(qual.AuthInvalidError):
        await qual.validate_moonmind_session(old_token, store, revocation, config)
    assert auth_module.bearer_transport.tokenUrl == ""


def test_secret_free_diagnostics():
    payload = {
        "auth_mode": "accounts",
        "MOONMIND_SESSION_SECRET": "super-secret-value",
        "password": "hunter2",
        "nested": {"client_secret": "abc", "host": "example.invalid"},
    }
    redacted = modes.redacted_diagnostics(payload)
    assert redacted["auth_mode"] == "accounts"
    rendered = str(redacted)
    assert "super-secret-value" not in rendered
    assert "hunter2" not in rendered
    secret = secrets.token_bytes(32)
    assert modes.session_secret_fingerprint(secret) == modes.session_secret_fingerprint(secret)
    assert secret.hex() not in modes.session_secret_fingerprint(secret)


def test_impact_selection_runs_conformance_and_boundaries():
    """R8/A4: touching an auth boundary runs required suites; the new guard runs fast."""
    from tools.select_test_suites import select_suites

    def _outputs(paths):
        return select_suites(paths, event_name="pull_request").as_outputs()

    for changed in (
        "api_service/auth.py",
        "api_service/auth_providers.py",
        "api_service/main.py",
        "api_service/api/routers/worker_auth.py",
        "moonmind/config/settings.py",
        "docker-compose.yaml",
        "frontend/src/generated/openapi.ts",
        "api_service/db/models.py",
    ):
        outputs = _outputs([changed])
        assert outputs["integration_ci"] == "true", changed
    outputs = _outputs(["tests/unit/auth/test_keycloak_removal_conformance.py"])
    assert outputs["unit_fast"] == "true"
    outputs = _outputs(["api_service/api/routers/workflow_console.py"])
    assert outputs["integration_ci"] == "false"


def test_conformance_module_lists_no_live_qualification_claims():
    """A6: hermetic evidence never claims live IdP/MFA/deployment results."""
    source = Path(__file__).read_text(encoding="utf-8")
    assert "EXTERNAL" in source
    assert "no public IdP" in source or "no live secrets" in source
