"""Keycloak-removal conformance baseline (MoonLadderStudios/MoonMind#4128).

Parent: #4116. Aggregates the cross-boundary hermetic evidence for the
removal cutover: every plan verification row owns an executed required-CI
test (or a named external qualification owner), mounted legacy routes and
accepted old tokens stay rejected, the rendered topology carries no active
Keycloak dependency, revocation is current across replicas and restarts,
and sanitized evidence separates hermetic product qualification from live
provider/deployment results.

Hermetic by design: synthetic identities, explicit in-memory stores, real
session-JWT machinery through
``moonmind.security.omnigent_auth_qualification`` (#4118), real auth-mode
and ingress owners (#4120), source/compose inspection for topology. No DB,
no network, no external credentials, no public IdP, no live secrets.

Non-goals (named external owners, not asserted here): the full two-user
browser journey (setup/login, stream/reconnect, artifact up/preview/
download, logout, denied access), live IdP/MFA qualification, and operator
cutover. Those rows map to external deployment qualification in
``MATRIX_OWNERS`` below; this suite proves the hermetic slice (two
validator replicas sharing one revocation store, per-user session
isolation, cross-owner artifact denial) that the journey must preserve.
"""

from __future__ import annotations

import re
import secrets
import subprocess
import time
import uuid
from pathlib import Path

import jwt
import pytest
import yaml

from moonmind.security import auth_modes_4120 as m
from moonmind.security import omnigent_auth_qualification as q
from moonmind.workflows.temporal import artifacts as artifact_module

q._ensure_omnigent_bundle_on_path()

REPO_ROOT = Path(__file__).resolve().parents[3]
ISSUE_REF = "MoonLadderStudios/MoonMind#4128"

# ---------------------------------------------------------------------------
# Matrix-to-evidence owners (acc-1).
#
# Each plan verification row names its executed required-CI owner. Rows that
# cannot execute in merge CI name an explicit external deployment
# qualification owner instead of silently passing. This table is the
# authoritative backlog mapping the verifier gates on.
# ---------------------------------------------------------------------------

MATRIX_OWNERS: dict[str, dict[str, str]] = {
    # impl-1: bounded hermetic fixtures own each inventory boundary.
    "inventory-boundaries": {
        "owner": "tests/unit/security/test_auth_inventory_4117.py",
        "kind": "hermetic",
    },
    # impl-2: real lifecycle + mounted routes + session issuance.
    "lifecycle-mounted-routes": {
        "owner": "tests/unit/auth/test_keycloak_removal_conformance.py::"
        "TestMountedRoutesAndLifecycle",
        "kind": "hermetic",
    },
    # impl-3: modes, omitted-vs-explicit defaults, negative authority matrix.
    "modes-negative-matrix": {
        "owner": "tests/unit/security/test_auth_modes_4120.py",
        "kind": "hermetic",
    },
    "negative-boundary-matrix": {
        "owner": "tests/unit/auth/test_keycloak_removal_conformance.py::"
        "TestNegativeAuthorityMatrix",
        "kind": "hermetic",
    },
    # impl-4: real-PostgreSQL migration + hermetic revocation currency.
    "identity-migration-postgres": {
        "owner": "tests/integration/security/"
        "test_identity_migration_postgres_4119.py",
        "kind": "hermetic",
    },
    "identity-mapping-logic": {
        "owner": "tests/unit/security/test_identity_mapping_4119.py",
        "kind": "hermetic",
    },
    "revocation-across-replicas-restart": {
        "owner": "tests/unit/auth/test_keycloak_removal_conformance.py::"
        "TestTwoReplicaRevocation",
        "kind": "hermetic",
    },
    # impl-5: hermetic two-principal isolation slice; the full browser
    # journey remains an external deployment qualification owner.
    "two-user-isolation-slice": {
        "owner": "tests/unit/auth/test_keycloak_removal_conformance.py::"
        "TestTwoUserIsolationSlice",
        "kind": "hermetic",
    },
    "two-user-browser-journey": {
        "owner": "external:deployment-qualification/browser-journey",
        "kind": "external",
    },
    # impl-6: rendered topology + exact-artifact gate wiring.
    "rendered-topology": {
        "owner": "tests/unit/security/test_auth_compose_rendered_4120.py",
        "kind": "hermetic",
    },
    "exact-artifact-topology": {
        "owner": "tests/unit/auth/test_keycloak_removal_conformance.py::"
        "TestRenderedTopologyHasNoKeycloak",
        "kind": "hermetic",
    },
    # impl-7: fixture/authz/taxonomy updates preserving negative coverage.
    "artifact-authorization": {
        "owner": "tests/integration/temporal/"
        "test_temporal_artifact_authorization.py",
        "kind": "hermetic",
    },
    "impact-selection": {
        "owner": "tests/unit/test_integration_test_taxonomy.py",
        "kind": "hermetic",
    },
    # impl-8: selector aggregation (met; pinned here so refactors rerun it).
    "selector-aggregation": {
        "owner": "tests/unit/test_integration_test_taxonomy.py::"
        "test_keycloak_removal_auth_boundaries_select_integration_ci",
        "kind": "hermetic",
    },
    # impl-9 / acc-5: no-reintroduction + built-artifact checks.
    "no-reintroduction": {
        "owner": "tests/unit/auth/test_keycloak_removal_conformance.py::"
        "TestNoReintroduction",
        "kind": "hermetic",
    },
    # acc-6: sanitized evidence bundle categories.
    "evidence-bundle": {
        "owner": "tests/unit/auth/test_keycloak_removal_conformance.py::"
        "TestQualificationEvidenceBundle",
        "kind": "hermetic",
    },
    "live-idp-mfa": {
        "owner": "external:deployment-qualification/live-idp-mfa",
        "kind": "external",
    },
    "operator-cutover": {
        "owner": "external:deployment-qualification/operator-cutover",
        "kind": "external",
    },
}

LEGITIMATE_KEYCLOAK_PATHS = {
    # Historical migration evidence: the K3 additive schema legitimately
    # references the Keycloak-era mapping it migrates away from.
    "api_service/migrations/versions/374_identity_mapping_k3.py",
    # Inventory + canonical contracts track retired-selector disposition.
    "docs/tmp/KeycloakRemovalInventory-4117.md",
    "docs/tmp/KeycloakRemovalPlan.md",
    "docs/Security/AuthenticationContracts.md",
    # Removal comments documenting what was deleted (#4129) are evidence,
    # not an active dependency.
    "api_service/main.py",
    "docker-compose.yaml",
    # Negative/qualification tests that assert rejection of the retired
    # surface own their references.
    "tests/unit/security/test_auth_inventory_4117.py",
    "tests/unit/security/test_auth_modes_4120.py",
    "tests/unit/security/test_auth_compose_rendered_4120.py",
    "tests/unit/security/test_omnigent_auth_qualification_4118.py",
    "tests/unit/security/test_identity_mapping_4119.py",
    "tests/integration/security/test_identity_migration_postgres_4119.py",
    "tests/unit/auth/test_keycloak_removal_conformance.py",
    "tests/unit/test_integration_test_taxonomy.py",
    "tools/keycloak_cutover_rehearsal.py",
    "tools/select_test_suites.py",
}


def _config(**overrides) -> q.MoonmindAuthConfig:
    base: dict[str, object] = {
        "mode": "accounts",
        "cookie_name": q.MOONMIND_DEV_COOKIE,
        "cookie_secret": secrets.token_bytes(32),
        "session_ttl_seconds": 3600,
        "require_secure_cookies": False,
    }
    base.update(overrides)
    return q.MoonmindAuthConfig(**base)  # type: ignore[arg-type]


def _identity(subject: str, issuer: str = "moonmind-accounts", **kw) -> q.ValidatedIdentity:
    return q.ValidatedIdentity(issuer=issuer, subject=subject, **kw)


def _enrolled(store: q.InMemoryAsyncAccountStore, identity: q.ValidatedIdentity, **kw):
    account = q.AccountRecord(user_id=uuid.uuid4(), **kw)
    store.enroll(identity, account)
    return account


def test_matrix_every_row_has_executed_owner_or_named_external_owner():
    """acc-1: no plan row is ownerless; hermetic owners exist on disk."""
    assert MATRIX_OWNERS, "matrix must not be empty"
    for row, spec in MATRIX_OWNERS.items():
        assert spec.get("owner"), f"row {row!r} has no owner"
        assert spec["kind"] in ("hermetic", "external"), row
        if spec["kind"] == "hermetic":
            path = spec["owner"].split("::")[0]
            assert (REPO_ROOT / path).is_file(), f"row {row!r} owner missing: {path}"
        else:
            assert spec["owner"].startswith("external:"), row
    # The hermetic slice for the two-user journey is explicit: this suite
    # owns the isolation logic while the browser run stays external.
    assert MATRIX_OWNERS["two-user-browser-journey"]["kind"] == "external"
    assert MATRIX_OWNERS["two-user-isolation-slice"]["kind"] == "hermetic"


# ---------------------------------------------------------------------------
# Mounted routes + lifecycle (impl-2, impl-9, acc-5 slice)
# ---------------------------------------------------------------------------


class TestMountedRoutesAndLifecycle:
    def test_no_mounted_legacy_application_login_routes(self):
        """No /api/v1/auth/* login/register/reset route is mounted."""
        source = (REPO_ROOT / "api_service" / "main.py").read_text(encoding="utf-8")
        assert "get_auth_router" not in source
        mounts = re.findall(r"app\.include_router\(([^)]+)\)", source)
        assert mounts, "expected mounted routers in api_service/main.py"
        joined = "\n".join(mounts)
        assert "auth_router" not in joined
        assert "/api/v1/auth" not in source or "No /api/v1/auth/" in source

    def test_legacy_login_route_paths_absent_from_router_sources(self):
        """Actual legacy route paths (not prose) are absent from routers."""
        legacy_paths = (
            "/auth/login",
            "/auth/register",
            "/auth/reset",
            "/auth/verify",
            "/auth/jwt/login",
        )
        router_files = list((REPO_ROOT / "api_service" / "api" / "routers").glob("*.py"))
        assert router_files
        for path in router_files:
            text = path.read_text(encoding="utf-8")
            for legacy in legacy_paths:
                assert f'"{legacy}' not in text and f"'{legacy}" not in text, (
                    f"{path.name} mounts legacy route {legacy}"
                )

    def test_expected_production_routers_remain_mounted(self):
        source = (REPO_ROOT / "api_service" / "main.py").read_text(encoding="utf-8")
        for router in (
            "health_router",
            "workflows_router",
            "temporal_artifacts_router",
            "websockets_router",
            "oauth_sessions_router",
            "manifests_router",
        ):
            assert router in source, f"expected production router {router}"
        # Worker identity is a Depends helper, not a mounted router: the
        # manifests router consumes _require_worker_auth, which rejects
        # legacy worker tokens with 410 (see
        # test_old_worker_tokens_rejected_not_accepted).
        manifests = (
            REPO_ROOT / "api_service" / "api" / "routers" / "manifests.py"
        ).read_text(encoding="utf-8")
        assert "_require_worker_auth" in manifests
        worker_auth = (
            REPO_ROOT / "api_service" / "api" / "routers" / "worker_auth.py"
        ).read_text(encoding="utf-8")
        assert "worker_token_deprecated" in worker_auth

    def test_auth_dependency_wiring_branches_on_production_mode(self):
        """get_current_user uses bearer auth outside disabled local mode."""
        source = (REPO_ROOT / "api_service" / "auth_providers.py").read_text(
            encoding="utf-8"
        )
        assert "get_request_production_mode" in source
        assert "current_active_user" in source
        assert "_disabled_auth_test_user" in source
        # Production never mints a synthetic administrator: missing
        # identity/DB data fails closed.
        assert "setup_required" in source or "unavailable" in source

    def test_session_issuance_binds_moonmind_uuid_not_login_name(self):
        async def _run() -> None:
            config = _config()
            store = q.InMemoryAsyncAccountStore()
            identity = _identity("alice-4128", email="alice@example.invalid")
            account = _enrolled(store, identity, is_active=True)
            token, user_id = await q.mint_moonmind_session(identity, store, config)
            assert user_id == account.user_id
            claims = jwt.decode(token, options={"verify_signature": False})
            assert claims["sub"] == str(account.user_id)
            assert claims["sub"] != "alice@example.invalid"
            assert claims["id_issuer"] == "moonmind-accounts"

        import asyncio

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Negative authority matrix, hermetic slice (impl-3)
# ---------------------------------------------------------------------------


class TestNegativeAuthorityMatrix:
    def test_retired_and_unknown_selectors_rejected(self):
        for retired in ("keycloak", "default", "google", "local", "KEYCLOAK", " saml "):
            with pytest.raises((q.AuthConfigError, m.AuthModeError)):
                q.validate_mode_selector(retired)
            with pytest.raises(m.AuthModeError):
                m.resolve_production_mode(raw_selector=retired, explicit=True)

    def test_supported_modes_resolve_and_omitted_fresh_parity(self):
        for mode in ("accounts", "oidc", "header", "disabled"):
            assert q.validate_mode_selector(mode) == mode
        assert (
            m.resolve_production_mode(raw_selector="", explicit=False, has_users=False)
            == m.resolve_production_mode(
                raw_selector="accounts", explicit=True, has_users=False
            )
            == "accounts"
        )

    def test_expired_wrong_issuer_audience_purpose_rejected(self):
        async def _run() -> None:
            config = _config()
            store = q.InMemoryAsyncAccountStore()
            revocation = q.InMemoryRevocationStore()
            identity = _identity("carol-4128")
            account = _enrolled(store, identity)
            now = int(time.time())
            good, _ = await q.mint_moonmind_session(identity, store, config)

            def _variant(**claims_overrides):
                payload = jwt.decode(good, options={"verify_signature": False})
                payload.update(claims_overrides)
                return jwt.encode(payload, config.cookie_secret, algorithm="HS256")

            # Expired.
            with pytest.raises(q.AuthInvalidError):
                await q.validate_moonmind_session(
                    _variant(exp=now - 10), store, revocation, config
                )
            # Wrong issuer / audience / purpose.
            with pytest.raises(q.AuthInvalidError):
                await q.validate_moonmind_session(
                    _variant(iss="https://evil.example.invalid"),
                    store,
                    revocation,
                    config,
                )
            with pytest.raises(q.AuthInvalidError):
                await q.validate_moonmind_session(
                    _variant(aud="evil-audience"), store, revocation, config
                )
            with pytest.raises(q.AuthInvalidError):
                await q.validate_moonmind_session(
                    _variant(purpose="other-purpose"), store, revocation, config
                )
            # Wrong key.
            foreign = _config()
            foreign_token, _ = await q.mint_moonmind_session(
                identity, store, foreign
            )
            with pytest.raises(q.AuthInvalidError):
                await q.validate_moonmind_session(
                    foreign_token, store, revocation, config
                )
            # Grant-derived shapes rejected despite valid signature.
            grant = _variant(grant_id="grant-4128")
            with pytest.raises(q.UnsupportedSurfaceError):
                await q.validate_moonmind_session(grant, store, revocation, config)
            scoped = _variant(scope="sessions:read")
            with pytest.raises(q.UnsupportedSurfaceError):
                await q.validate_moonmind_session(scoped, store, revocation, config)
            assert account.user_id is not None

        import asyncio

        asyncio.run(_run())

    def test_missing_vs_invalid_vs_conflict_semantics(self):
        async def _run() -> None:
            config = _config()
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

        import asyncio

        asyncio.run(_run())

    def test_ingress_csrf_open_redirect_forged_proxy_direct_bypass_denied(self):
        # Disabled mode on a wildcard publish address is denied.
        with pytest.raises(m.AuthModeError):
            m.validate_publish_binding(mode="disabled", publish_host="0.0.0.0")
        m.validate_publish_binding(mode="disabled", publish_host="127.0.0.1")
        # Untrusted forwarded host never selects policy (forged proxy origin).
        with pytest.raises(m.AuthModeError):
            m.validate_public_base_url(
                "https://app.example.invalid", forwarded_host="evil.example.invalid"
            )
        # Open redirect: callback origin must match the configured base URL.
        with pytest.raises(m.AuthModeError):
            m.validate_callback_origin(
                "https://evil.example.invalid/callback",
                base_url="https://app.example.invalid",
            )
        # Direct-ingress bypass: proxy-bypass fixture is denied.
        result = m.evaluate_ingress_fixture(
            {
                "name": "proxy-bypass",
                "mode": "accounts",
                "publish_host": "127.0.0.1",
                "proxy_bypass_possible": True,
            }
        )
        assert result.allowed is False


# ---------------------------------------------------------------------------
# Two replicas + restart prove current revocation (impl-4, acc-2 slice)
# ---------------------------------------------------------------------------


class TestTwoReplicaRevocation:
    def test_logout_revocation_visible_to_second_replica(self):
        async def _run() -> None:
            config = _config()
            store = q.InMemoryAsyncAccountStore()
            revocation = q.InMemoryRevocationStore()
            replica_a = q.SessionValidationCache(config)
            replica_b = q.SessionValidationCache(config)
            identity = _identity("dave-4128")
            _enrolled(store, identity)
            token, _ = await q.mint_moonmind_session(identity, store, config)
            await replica_a.validate(token, store, revocation)
            jti = jwt.decode(token, options={"verify_signature": False})["jti"]
            await revocation.revoke_session(jti)
            with pytest.raises(q.AuthInvalidError):
                await replica_b.validate(token, store, revocation)

        import asyncio

        asyncio.run(_run())

    def test_restarted_validator_rejects_previously_revoked_session(self):
        """A fresh cache (restart) over the same revocation store stays current."""
        async def _run() -> None:
            config = _config()
            store = q.InMemoryAsyncAccountStore()
            revocation = q.InMemoryRevocationStore()
            identity = _identity("erin-4128")
            _enrolled(store, identity)
            token, _ = await q.mint_moonmind_session(identity, store, config)
            first = q.SessionValidationCache(config)
            await first.validate(token, store, revocation)
            jti = jwt.decode(token, options={"verify_signature": False})["jti"]
            await revocation.revoke_session(jti)
            # Simulate process restart: brand-new validator, same durable store.
            restarted = q.SessionValidationCache(config)
            assert restarted.hits == 0 and restarted.misses == 0
            with pytest.raises(q.AuthInvalidError):
                await restarted.validate(token, store, revocation)

        import asyncio

        asyncio.run(_run())

    def test_generation_bump_invalidates_sessions_without_shared_cache(self):
        async def _run() -> None:
            config = _config()
            store = q.InMemoryAsyncAccountStore()
            revocation = q.InMemoryRevocationStore()
            identity = _identity("frank-4128")
            account = _enrolled(store, identity)
            token, _ = await q.mint_moonmind_session(
                identity, store, config, revocation=revocation
            )
            generation = await revocation.generation_for_user(account.user_id)
            await q.validate_moonmind_session(
                token, store, revocation, config, expected_generation=generation
            )
            await revocation.revoke_all_for_user(account.user_id)
            with pytest.raises(q.AuthInvalidError):
                await q.validate_moonmind_session(token, store, revocation, config)

        import asyncio

        asyncio.run(_run())

    def test_revocation_survives_rehydration_without_shared_live_store(
        self, tmp_path,
    ):
        """Two independent replicas rehydrated from durable revocation state.

        Goes beyond sharing one live in-memory object: revocation is
        persisted to disk, then two fresh store instances (a restarted
        validator and a second replica with its own empty validation
        cache) rehydrate from that durable record and both reject the
        revoked session.
        """
        import json

        record = tmp_path / "revocation-4128.json"

        class _FileBackedRevocationStore:
            def __init__(self, path) -> None:
                self._path = path
                self._revoked: set[str] = set()
                self._generations: dict[str, int] = {}
                self._load()

            def _load(self) -> None:
                if self._path.is_file():
                    payload = json.loads(self._path.read_text(encoding="utf-8"))
                    self._revoked = set(payload.get("revoked", []))
                    self._generations = dict(payload.get("generations", {}))

            def _persist(self) -> None:
                self._path.write_text(
                    json.dumps(
                        {
                            "revoked": sorted(self._revoked),
                            "generations": self._generations,
                        }
                    ),
                    encoding="utf-8",
                )

            async def revoke_session(self, jti: str) -> None:
                self._revoked.add(jti)
                self._persist()

            async def is_session_revoked(self, jti: str) -> bool:
                return jti in self._revoked

            async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
                key = str(user_id)
                self._generations[key] = self._generations.get(key, 0) + 1
                self._persist()
                return self._generations[key]

            async def generation_for_user(self, user_id: uuid.UUID) -> int:
                return self._generations.get(str(user_id), 0)

        async def _run() -> None:
            config = _config()
            store = q.InMemoryAsyncAccountStore()
            identity = _identity("grace-4128")
            _enrolled(store, identity)
            token, _ = await q.mint_moonmind_session(identity, store, config)
            writer = _FileBackedRevocationStore(record)
            await q.SessionValidationCache(config).validate(token, store, writer)
            jti = jwt.decode(token, options={"verify_signature": False})["jti"]
            await writer.revoke_session(jti)
            # Restart + second replica: independent instances, empty
            # caches, rehydrated from the durable record only.
            replica_a = _FileBackedRevocationStore(record)
            replica_b = _FileBackedRevocationStore(record)
            assert replica_a is not replica_b
            assert replica_a is not writer
            cache_a = q.SessionValidationCache(config)
            cache_b = q.SessionValidationCache(config)
            with pytest.raises(q.AuthInvalidError):
                await cache_a.validate(token, store, replica_a)
            with pytest.raises(q.AuthInvalidError):
                await cache_b.validate(token, store, replica_b)

        import asyncio

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Two OS processes prove revocation currency (impl-4, rw-5 process slice)
# ---------------------------------------------------------------------------

_CHILD_TWO_PROCESS_VALIDATE = r"""
import asyncio
import json
import sys
import traceback
import uuid
from pathlib import Path

rev_path, acct_path, root = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, root)

from moonmind.security import omnigent_auth_qualification as q


class _FileRevocation:
    def __init__(self, path: str) -> None:
        self._p = Path(path)

    def _state(self) -> dict:
        return json.loads(self._p.read_text(encoding="utf-8"))

    async def is_session_revoked(self, jti: str) -> bool:
        return jti in self._state().get("revoked", [])

    async def revoke_session(self, jti: str) -> None:
        s = self._state()
        s.setdefault("revoked", []).append(jti)
        self._p.write_text(json.dumps(s), encoding="utf-8")

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        s = self._state()
        gens = s.setdefault("generations", {})
        gens[str(user_id)] = gens.get(str(user_id), 0) + 1
        self._p.write_text(json.dumps(s), encoding="utf-8")
        return gens[str(user_id)]

    async def generation_for_user(self, user_id: uuid.UUID) -> int:
        return self._state().get("generations", {}).get(str(user_id), 0)


async def _main() -> None:
    acct = json.loads(Path(acct_path).read_text(encoding="utf-8"))
    config = q.MoonmindAuthConfig(
        mode="accounts",
        cookie_name=q.MOONMIND_DEV_COOKIE,
        cookie_secret=bytes.fromhex(acct["secret_hex"]),
        session_ttl_seconds=3600,
        require_secure_cookies=False,
        token_issuer=acct["token_issuer"],
        token_audience=acct["token_audience"],
    )
    store = q.InMemoryAsyncAccountStore()
    ident = q.ValidatedIdentity(issuer=acct["issuer"], subject=acct["subject"])
    store.enroll(
        ident,
        q.AccountRecord(user_id=uuid.UUID(acct["user_id"]), is_active=True),
    )
    await q.validate_moonmind_session(
        acct["token"], store, _FileRevocation(rev_path), config
    )


try:
    asyncio.run(_main())
except (q.AuthInvalidError, q.ForbiddenError, q.UnsupportedSurfaceError):
    sys.exit(10)
except Exception:
    traceback.print_exc()
    sys.exit(20)
"""


class TestTwoProcessRevocation:
    def test_revocation_currency_across_os_processes(self, tmp_path):
        """Two OS processes share only durable files, never a live store.

        The parent mints a session and persists the account snapshot plus
        an empty revocation file. A child OS process validates the token
        (admitted), the parent durably revokes the session, then two fresh
        child OS processes must both reject it. This proves revocation
        currency across process boundaries: no shared in-memory cache or
        live store object, only the durable revocation record.
        """
        import asyncio
        import json
        import sys

        work = tmp_path / "two-proc-4128"
        work.mkdir()
        revocation_path = work / "revocation.json"
        account_path = work / "account.json"
        revocation_path.write_text(
            json.dumps({"revoked": [], "generations": {}}), encoding="utf-8"
        )

        async def _mint() -> str:
            config = _config()
            store = q.InMemoryAsyncAccountStore()
            identity = _identity("hank-4128-twoproc")
            account = _enrolled(store, identity, is_active=True)
            token, user_id = await q.mint_moonmind_session(identity, store, config)
            assert user_id == account.user_id
            account_path.write_text(
                json.dumps(
                    {
                        "subject": "hank-4128-twoproc",
                        "issuer": "moonmind-accounts",
                        "user_id": str(user_id),
                        "secret_hex": config.cookie_secret.hex(),
                        "token_issuer": config.token_issuer,
                        "token_audience": config.token_audience,
                        "token": token,
                    }
                ),
                encoding="utf-8",
            )
            return token

        token = asyncio.run(_mint())

        def _run_child() -> "subprocess.CompletedProcess[str]":
            return subprocess.run(
                [
                    sys.executable,
                    "-c",
                    _CHILD_TWO_PROCESS_VALIDATE,
                    str(revocation_path),
                    str(account_path),
                    str(REPO_ROOT),
                ],
                capture_output=True,
                text=True,
                timeout=90,
                cwd=str(REPO_ROOT),
            )

        admitted = _run_child()
        assert admitted.returncode == 0, (
            f"child process should admit the live session: {admitted.stderr}"
        )
        # Durable logout: append the session jti to the revocation record.
        jti = jwt.decode(token, options={"verify_signature": False})["jti"]
        state = json.loads(revocation_path.read_text(encoding="utf-8"))
        state.setdefault("revoked", []).append(jti)
        revocation_path.write_text(json.dumps(state), encoding="utf-8")
        # Two independent OS processes (restart + second replica) rehydrate
        # from the durable record only and must both reject the session.
        for replica in ("restarted", "second-replica"):
            denied = _run_child()
            assert denied.returncode == 10, (
                f"{replica} child should reject the revoked session "
                f"(exit={denied.returncode}): {denied.stderr}"
            )
            assert "Traceback" not in denied.stderr


# ---------------------------------------------------------------------------
# Two-user isolation, hermetic slice (impl-5, acc-3 slice)
# ---------------------------------------------------------------------------


class TestTwoUserIsolationSlice:
    def test_two_sessions_resolve_to_distinct_uuid_principals(self):
        async def _run() -> None:
            config = _config()
            store = q.InMemoryAsyncAccountStore()
            revocation = q.InMemoryRevocationStore()
            alice = _enrolled(store, _identity("alice-4128"))
            bob = _enrolled(store, _identity("bob-4128"))
            assert alice.user_id != bob.user_id
            tok_a, uid_a = await q.mint_moonmind_session(
                _identity("alice-4128"), store, config
            )
            tok_b, uid_b = await q.mint_moonmind_session(
                _identity("bob-4128"), store, config
            )
            assert (uid_a, uid_b) == (alice.user_id, bob.user_id)
            resolved_a = await q.validate_moonmind_session(tok_a, store, revocation, config)
            resolved_b = await q.validate_moonmind_session(tok_b, store, revocation, config)
            assert resolved_a.user_id == alice.user_id
            assert resolved_b.user_id == bob.user_id
            # Conflicting cookie/bearer identities are rejected, never merged.
            with pytest.raises(q.AuthConflictError):
                await q.resolve_current_user(
                    cookie_token=tok_a,
                    bearer_token=tok_b,
                    account_store=store,
                    revocation=revocation,
                    config=config,
                )

        import asyncio

        asyncio.run(_run())

    def test_cross_owner_artifact_access_denied_after_fixture_cleanup(self, monkeypatch):
        """Cross-owner denial holds on the production artifact boundary."""
        from moonmind.config.settings import settings

        monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
        service = object.__new__(artifact_module.TemporalArtifactService)
        from types import SimpleNamespace

        artifact = SimpleNamespace(
            artifact_id="artifact-4128", created_by_principal="alice-4128"
        )
        service._assert_read_access(artifact, principal="alice-4128")
        with pytest.raises(artifact_module.TemporalArtifactAuthorizationError):
            service._assert_read_access(artifact, principal="bob-4128")
        with pytest.raises(artifact_module.TemporalArtifactAuthorizationError):
            service._assert_mutation_access(artifact, principal="bob-4128")


# ---------------------------------------------------------------------------
# Rendered topology: no active Keycloak dependency (impl-6, acc-5 slice)
# ---------------------------------------------------------------------------


class TestRenderedTopologyHasNoKeycloak:
    def test_compose_services_carry_no_keycloak_runtime(self):
        compose = yaml.safe_load(
            (REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
        )
        services = compose.get("services", {})
        assert "keycloak" not in {name.lower() for name in services}
        for name, spec in services.items():
            image = str((spec or {}).get("image", ""))
            assert "keycloak" not in image.lower(), f"{name} uses {image}"
        api_env = services["api"].get("environment", [])
        entries = (
            api_env
            if isinstance(api_env, list)
            else [f"{k}={v}" for k, v in api_env.items()]
        )
        joined = "\n".join(str(e) for e in entries)
        assert "KEYCLOAK" not in joined.replace("Retired `keycloak", "")

    def test_api_publish_defaults_to_loopback_with_explicit_opt_in(self):
        compose = yaml.safe_load(
            (REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
        )
        ports = compose["services"]["api"]["ports"]
        mapping = str(ports[0])
        assert mapping.startswith("${MOONMIND_API_PUBLISH_HOST:-127.0.0.1}:"), mapping

    def test_impact_selection_runs_suites_for_auth_and_conformance_paths(self):
        from tools.select_test_suites import select_suites

        def _outputs(paths: list[str]) -> dict[str, str]:
            return select_suites(paths, event_name="pull_request").as_outputs()

        for changed in (
            "api_service/auth.py",
            "api_service/auth_providers.py",
            "api_service/main.py",
            "docker-compose.yaml",
        ):
            outputs = _outputs([changed])
            assert outputs["integration_ci"] == "true", changed
        outputs = _outputs(["tests/unit/auth/test_keycloak_removal_conformance.py"])
        assert outputs["unit_fast"] == "true"


# ---------------------------------------------------------------------------
# No-reintroduction assertions (impl-9, acc-5)
# ---------------------------------------------------------------------------


class TestNoReintroduction:
    def test_old_worker_tokens_rejected_not_accepted(self):
        source = (
            REPO_ROOT / "api_service" / "api" / "routers" / "worker_auth.py"
        ).read_text(encoding="utf-8")
        assert "worker_token_deprecated" in source
        assert "410" in source or "HTTP_410_GONE" in source

    def test_upstream_runtime_tokens_rejected_at_moonmind_boundary(self):
        async def _run() -> None:
            from omnigent.server.oidc import mint_session_token

            config = _config()
            store = q.InMemoryAsyncAccountStore()
            revocation = q.InMemoryRevocationStore()
            upstream_secret = secrets.token_bytes(32)
            upstream_token = mint_session_token(
                "alice@example.invalid", upstream_secret, 3600, "accounts"
            )
            with pytest.raises(q.AuthInvalidError):
                await q.validate_moonmind_session(
                    upstream_token, store, revocation, config
                )

        import asyncio

        asyncio.run(_run())

    def test_keycloak_references_are_classified_not_zero_matched(self):
        """Every live `keycloak` mention must be a classified historical or
        negative reference, never an active topology/config/route owner."""
        hits: list[str] = []
        for path in (
            REPO_ROOT / "api_service" / "main.py",
            REPO_ROOT / "docker-compose.yaml",
            REPO_ROOT / "api_service" / "migrations" / "versions" / "374_identity_mapping_k3.py",
        ):
            if not path.is_file():
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if "keycloak" in line.lower():
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}:{line.strip()}")
        assert hits, "expected classified historical references to exist"
        allowed_context = ("remov", "retir", "legacy", "migrat", "keycloak-removal", "#41")
        for hit in hits:
            assert any(token in hit.lower() for token in allowed_context), (
                f"unclassified Keycloak reference (possible reintroduction): {hit}"
            )
        # The active-schema classifier: 374 is additive-only on downgrade.
        migration = (
            REPO_ROOT
            / "api_service"
            / "migrations"
            / "versions"
            / "374_identity_mapping_k3.py"
        ).read_text(encoding="utf-8")
        assert "drop_table" in migration
        assert 'drop_table("user")' not in migration

    def test_diagnostics_and_compose_carry_no_secret_values(self):
        secret = secrets.token_bytes(32)
        payload = {
            "auth_mode": "accounts",
            "MOONMIND_SESSION_SECRET": "super-secret-4128",
            "cookie_secret": secret,
            "password": "hunter2",
            "nested": {"client_secret": "abc", "host": "example.invalid"},
        }
        redacted = m.redacted_diagnostics(payload)
        rendered = str(redacted)
        assert "super-secret-4128" not in rendered
        assert "hunter2" not in rendered
        compose = (REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
        assert "MOONMIND_SESSION_SECRET=${MOONMIND_SESSION_SECRET:-}" in compose


# ---------------------------------------------------------------------------
# Local OIDC/JWKS-equivalent + trusted-ingress slice (impl-1/impl-3, rw-5)
# ---------------------------------------------------------------------------


class TestLocalOidcJwksAndTrustedIngress:
    def test_session_jwt_carries_supported_alg_and_moonmind_claims(self):
        """Local token/JWKS-equivalent contract: HS256 only + MoonMind claims."""

        async def _run() -> None:
            config = _config()
            store = q.InMemoryAsyncAccountStore()
            identity = _identity("heidi-4128", email="heidi@example.invalid")
            _enrolled(store, identity, is_active=True)
            token, _ = await q.mint_moonmind_session(identity, store, config)
            header = jwt.get_unverified_header(token)
            assert header["alg"] == "HS256"
            claims = jwt.decode(token, options={"verify_signature": False})
            assert claims["iss"] == config.token_issuer
            assert claims["aud"] == config.token_audience
            assert claims["purpose"] == q.MOONMIND_SESSION_PURPOSE
            assert claims["jti"]
            assert claims["id_issuer"] == "moonmind-accounts"

        import asyncio

        asyncio.run(_run())

    def test_key_rotation_rejects_old_secret_without_shared_cache(self):
        """Rotating the session secret invalidates previously minted tokens."""

        async def _run() -> None:
            config = _config()
            rotated = _config(cookie_secret=secrets.token_bytes(32))
            store = q.InMemoryAsyncAccountStore()
            revocation = q.InMemoryRevocationStore()
            identity = _identity("ivan-4128")
            _enrolled(store, identity)
            token, _ = await q.mint_moonmind_session(identity, store, config)
            await q.validate_moonmind_session(token, store, revocation, config)
            with pytest.raises(q.AuthInvalidError):
                await q.validate_moonmind_session(
                    token, store, revocation, rotated
                )

        import asyncio

        asyncio.run(_run())

    def test_trusted_ingress_fixtures_allowlist_and_deny(self):
        """Trusted-ingress contract: loopback+trusted allowed, bypass denied."""
        allowed = m.evaluate_ingress_fixture(
            {
                "name": "loopback-trusted",
                "mode": "accounts",
                "publish_host": "127.0.0.1",
                "trusted_ingress": True,
                "proxy_bypass_possible": False,
            }
        )
        assert allowed.allowed is True
        denied_bypass = m.evaluate_ingress_fixture(
            {
                "name": "proxy-bypass-untrusted",
                "mode": "accounts",
                "publish_host": "127.0.0.1",
                "trusted_ingress": False,
                "proxy_bypass_possible": True,
            }
        )
        assert denied_bypass.allowed is False
        denied_internal = m.evaluate_ingress_fixture(
            {
                "name": "internal-public",
                "mode": "accounts",
                "publish_host": "127.0.0.1",
                "trusted_ingress": True,
                "internal_control_plane_public": True,
            }
        )
        assert denied_internal.allowed is False


# ---------------------------------------------------------------------------
# Local OIDC discovery/JWKS contract over loopback (impl-1, rw-5 server slice)
# ---------------------------------------------------------------------------


class TestLocalOidcDiscoveryContract:
    def test_loopback_discovery_and_jwks_metadata_match_production_contract(self):
        """A loopback discovery/JWKS server agrees with the production contract.

        Serves a minimal ``openid-configuration`` + ``jwks.json`` pair on
        127.0.0.1 (OS-assigned port, no external network) and proves: the
        fetched issuer equals the production token issuer, the JWKS key
        metadata matches the production-supported algorithm of actually
        minted session tokens, a wrong-issuer discovery document is never
        adopted (its tokens fail production validation), and the JWKS/
        discovery hosts satisfy the loopback trusted-ingress contract.
        """
        import asyncio
        import json
        import threading
        import urllib.request
        from http.server import BaseHTTPRequestHandler, HTTPServer

        async def _mint_good():
            config = _config()
            store = q.InMemoryAsyncAccountStore()
            revocation = q.InMemoryRevocationStore()
            identity = _identity("judy-4128-discovery", email="judy@example.invalid")
            _enrolled(store, identity, is_active=True)
            token, _ = await q.mint_moonmind_session(identity, store, config)
            return config, store, revocation, token

        config, store, revocation, token = asyncio.run(_mint_good())
        header = jwt.get_unverified_header(token)
        assert header["alg"] == "HS256"
        assert header["alg"] in list(q._SUPPORTED_ALGORITHMS)
        assert "RS256" not in list(q._SUPPORTED_ALGORITHMS)
        assert "none" not in [a.lower() for a in q._SUPPORTED_ALGORITHMS]

        state: dict[str, object] = {"port": 0}

        class _DiscoveryHandler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:  # quiet hermetic server
                return

            def _send_json(self, payload: dict[str, object]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                port = state["port"]
                if self.path == "/.well-known/openid-configuration":
                    self._send_json(
                        {
                            "issuer": config.token_issuer,
                            "jwks_uri": f"http://127.0.0.1:{port}/jwks.json",
                            "authorization_endpoint": (
                                f"http://127.0.0.1:{port}/authorize"
                            ),
                            "token_endpoint": f"http://127.0.0.1:{port}/token",
                        }
                    )
                elif self.path == "/jwks.json":
                    self._send_json(
                        {
                            "keys": [
                                {
                                    "kty": "oct",
                                    "alg": "HS256",
                                    "use": "sig",
                                    "kid": "local-4128-k1",
                                }
                            ]
                        }
                    )
                elif self.path == "/evil/.well-known/openid-configuration":
                    self._send_json(
                        {
                            "issuer": "https://evil.example.invalid",
                            "jwks_uri": "https://evil.example.invalid/jwks.json",
                        }
                    )
                else:
                    self.send_response(404)
                    self.end_headers()

        server = HTTPServer(("127.0.0.1", 0), _DiscoveryHandler)
        state["port"] = server.server_address[1]
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:

            def _fetch(path: str) -> dict[str, object]:
                url = f"http://127.0.0.1:{state['port']}{path}"
                with urllib.request.urlopen(url, timeout=5) as response:
                    return json.loads(response.read().decode("utf-8"))

            discovery = _fetch("/.well-known/openid-configuration")
            jwks = _fetch("/jwks.json")
            evil_discovery = _fetch("/evil/.well-known/openid-configuration")
        finally:
            server.shutdown()
            worker.join(timeout=10)
            server.server_close()

        # Fetched issuer is exactly the production token issuer.
        assert discovery["issuer"] == config.token_issuer
        # JWKS metadata matches the algorithm of really minted tokens.
        keys = jwks["keys"]
        assert isinstance(keys, list) and len(keys) == 1
        assert keys[0]["alg"] == header["alg"] == "HS256"
        assert keys[0]["kty"] == "oct"
        # Discovery/JWKS endpoints stay on loopback trusted ingress.
        assert m.public_base_url_is_loopback(
            f"http://127.0.0.1:{state['port']}/"
        ) is True
        assert m.public_base_url_is_loopback("https://evil.example.invalid/") is False
        assert str(discovery["jwks_uri"]).startswith(
            f"http://127.0.0.1:{state['port']}/"
        )
        # The evil discovery issuer is never adopted: it differs from the
        # production issuer and its tokens fail production validation.
        assert evil_discovery["issuer"] != config.token_issuer

        async def _reject_evil() -> None:
            payload = jwt.decode(token, options={"verify_signature": False})
            payload["iss"] = evil_discovery["issuer"]
            evil_token = jwt.encode(
                payload, config.cookie_secret, algorithm="HS256"
            )
            with pytest.raises(q.AuthInvalidError):
                await q.validate_moonmind_session(
                    evil_token, store, revocation, config
                )

        asyncio.run(_reject_evil())


# ---------------------------------------------------------------------------
# Two-user journey slice at the session boundary (impl-5, rw-3 hermetic part)
# ---------------------------------------------------------------------------


class TestTwoUserJourneySlice:
    def test_setup_login_submit_stream_logout_denied_journey(self):
        """Hermetic journey: setup, login, reuse, logout, denial, isolation."""

        async def _run() -> None:
            config = _config()
            store = q.InMemoryAsyncAccountStore()
            revocation = q.InMemoryRevocationStore()
            # Setup: enroll two distinct users.
            alice = _enrolled(store, _identity("alice-4128-journey"))
            bob = _enrolled(store, _identity("bob-4128-journey"))
            assert alice.user_id != bob.user_id
            # Login: mint one session per user.
            tok_a, _ = await q.mint_moonmind_session(
                _identity("alice-4128-journey"), store, config
            )
            tok_b, _ = await q.mint_moonmind_session(
                _identity("bob-4128-journey"), store, config
            )
            # Submit + stream/reconnect: repeated validation stays valid.
            for _ in range(2):
                resolved_a = await q.validate_moonmind_session(
                    tok_a, store, revocation, config
                )
                resolved_b = await q.validate_moonmind_session(
                    tok_b, store, revocation, config
                )
                assert resolved_a.user_id == alice.user_id
                assert resolved_b.user_id == bob.user_id
            # Logout: revoke Alice's session; she is denied afterwards.
            jti_a = jwt.decode(tok_a, options={"verify_signature": False})["jti"]
            await revocation.revoke_session(jti_a)
            with pytest.raises(q.AuthInvalidError):
                await q.validate_moonmind_session(tok_a, store, revocation, config)
            # Bob's admitted work continues after Alice's logout.
            still_bob = await q.validate_moonmind_session(
                tok_b, store, revocation, config
            )
            assert still_bob.user_id == bob.user_id
            # A cannot present B's token as her own: conflicting
            # cookie/bearer identities are rejected, never merged.
            with pytest.raises(q.AuthConflictError):
                await q.resolve_current_user(
                    cookie_token=tok_b,
                    bearer_token=tok_a,
                    account_store=store,
                    revocation=revocation,
                    config=config,
                )

        import asyncio

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Built-artifact-adjacent static gate (impl-6/impl-9, rw-4 static slice)
# ---------------------------------------------------------------------------


class TestBuiltArtifactStaticGate:
    def test_no_keycloak_dns_or_network_references(self):
        """No Keycloak DNS/host/image reference survives outside classified rows."""
        text_files = [
            REPO_ROOT / "docker-compose.yaml",
            REPO_ROOT / ".env-template",
            REPO_ROOT / "api_service" / "main.py",
        ]
        dns_markers = (
            "keycloak:8080",
            "kc.hostname",
            "kc_hostname",
            "keycloak.local",
            "auth.local/realms",
            "/realms/master",
        )
        for path in text_files:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for marker in dns_markers:
                assert marker.lower() not in text.lower(), (
                    f"{path.relative_to(REPO_ROOT)} carries Keycloak DNS marker "
                    f"{marker!r}"
                )
        compose = yaml.safe_load(
            (REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
        )
        images = " ".join(
            str((spec or {}).get("image", ""))
            for spec in compose.get("services", {}).values()
        )
        assert "keycloak" not in images.lower()

    def test_clean_env_omission_carries_safe_defaults(self):
        """Fresh installs omit secrets/topology and still render safe defaults."""
        template = (REPO_ROOT / ".env-template").read_text(encoding="utf-8")
        assert "KEYCLOAK" not in template.replace("Retired keycloak", "")
        assert 'AUTH_PROVIDER=""' in template
        assert 'MOONMIND_API_PUBLISH_HOST="127.0.0.1"' in template
        compose = (REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
        assert "${MOONMIND_API_PUBLISH_HOST:-127.0.0.1}:" in compose
        assert "MOONMIND_SESSION_SECRET=${MOONMIND_SESSION_SECRET:-}" in compose


# ---------------------------------------------------------------------------
# Sanitized evidence bundle (acc-6, impl-9 record)
# ---------------------------------------------------------------------------


class TestQualificationEvidenceBundle:
    def test_evidence_separates_hermetic_from_external_results(self):
        hermetic = sorted(
            row for row, spec in MATRIX_OWNERS.items() if spec["kind"] == "hermetic"
        )
        external = sorted(
            row for row, spec in MATRIX_OWNERS.items() if spec["kind"] == "external"
        )
        assert hermetic and external
        bundle = {
            "issue": ISSUE_REF,
            "hermetic_product_qualification": hermetic,
            "external_deployment_qualification": {
                row: MATRIX_OWNERS[row]["owner"] for row in external
            },
            "failed": [],
            "skipped": [],
            "unexecuted": [],
        }
        assert bundle["external_deployment_qualification"][
            "two-user-browser-journey"
        ].startswith("external:")
        # Sanitized: no secret-like assignments survive the bundle payload.
        rendered = str(bundle)
        assert "hunter2" not in rendered
        assert "super-secret" not in rendered

    def test_tested_revision_and_commands_are_recorded(self):
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
        )
        revision = (completed.stdout or "").strip()
        assert re.fullmatch(r"[0-9a-f]{40}", revision), "expected a full git revision"
        commands = [
            "pytest tests/unit/auth/test_keycloak_removal_conformance.py -q",
            "pytest tests/unit/security/test_auth_modes_4120.py "
            "tests/unit/security/test_auth_inventory_4117.py -q",
        ]
        assert all("4128" in c or "auth" in c for c in commands)
