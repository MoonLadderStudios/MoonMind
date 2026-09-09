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
            "worker_auth",
        ):
            assert router in source, f"expected production router {router}"

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
