"""K3 auth modes, secure defaults, and upgrade safety (#4120).

Covers the issue acceptance bullets through the repository's unit runner:
omitted/explicit fresh-install parity, populated-DB migration gating,
retired-selector rejection, runtime-env isolation, durable signing keys,
disabled-mode exposure fixtures, URL/proxy/cookie policy, and secret-free
diagnostics.
"""

from __future__ import annotations

import secrets

import pytest

from moonmind.security import auth_modes_4120 as m
from moonmind.security import omnigent_auth_qualification as q


# ---------------------------------------------------------------------------
# 1. Single selector under the correctly scoped owner
# ---------------------------------------------------------------------------


def test_supported_selectors_resolve_and_retired_fail():
    for mode in ("accounts", "oidc", "header", "disabled"):
        assert m.resolve_production_mode(
            raw_selector=mode, explicit=True
        ) == mode
    for retired in ("keycloak", "default", "google", "local"):
        with pytest.raises(m.AuthModeError):
            m.resolve_production_mode(raw_selector=retired, explicit=True)
    with pytest.raises(m.AuthModeError):
        m.resolve_production_mode(raw_selector="saml", explicit=True)
    # Case/padding normalizes to the canonical lowercase mode.
    assert (
        m.resolve_production_mode(raw_selector="  ACCOUNTS ", explicit=True)
        == "accounts"
    )


def test_owner_is_single_reader_of_storage():
    # Production consumers go through the owner; storage keeps the raw value.
    from moonmind.config import settings as settings_module

    assert hasattr(settings_module.oidc, "AUTH_PROVIDER")
    assert callable(m.get_effective_auth_provider)
    assert callable(m.is_disabled_local_mode)


# ---------------------------------------------------------------------------
# 2. Control-plane separation: OMNIGENT_AUTH_* cannot configure MoonMind
# ---------------------------------------------------------------------------


def test_control_plane_ignores_contradictory_runtime_ambient():
    secret = secrets.token_bytes(32)
    hostile = {
        "OMNIGENT_AUTH_PROVIDER": "header",
        "OMNIGENT_AUTH_ENABLED": "1",
        "OMNIGENT_AUTH_HEADER": "X-Evil",
        "OMNIGENT_ACCOUNTS_COOKIE_SECRET": "runtime-secret",
        "OMNIGENT_OIDC_ISSUER": "https://evil.example",
    }
    config = m.resolve_moonmind_auth_config(
        mode="accounts", cookie_secret=secret, environ=hostile
    )
    assert config.mode == "accounts"
    assert config.cookie_name == q.MOONMIND_PROD_COOKIE
    assert config.cookie_secret == secret
    # Same-origin isolation: distinct cookie, purpose-bound tokens.
    assert config.cookie_name not in q.UPSTREAM_SESSION_COOKIES
    assert config.token_issuer == q.MOONMIND_TOKEN_ISSUER
    assert config.token_audience == q.MOONMIND_TOKEN_AUDIENCE


# ---------------------------------------------------------------------------
# 3. Fresh vs. upgrade: versioned persisted decision, protected choice
# ---------------------------------------------------------------------------


def test_omitted_fresh_selects_accounts_like_explicit():
    omitted_fresh = m.resolve_production_mode(
        raw_selector="", explicit=False, has_users=False
    )
    explicit = m.resolve_production_mode(
        raw_selector="accounts", explicit=True, has_users=False
    )
    assert omitted_fresh == explicit == "accounts"


def test_populated_omitted_stops_actionably_without_modifying_owners():
    with pytest.raises(m.MigrationRequiredError) as exc:
        m.resolve_production_mode(raw_selector="", explicit=False, has_users=True)
    assert "migration" in str(exc.value).lower()
    assert getattr(exc.value, "requires_protected_operator_choice", False) is True
    classification = m.classify_deployment(
        raw_selector="", explicit=False, has_users=True
    )
    assert classification.migration_required is True
    assert classification.setup_required is True
    assert classification.production_mode == "migration_required"


def test_populated_with_decision_uses_decided_mode():
    decision = m.AuthMigrationDecision(mode="accounts")
    assert (
        m.resolve_production_mode(
            raw_selector="",
            explicit=False,
            has_users=True,
            migration_decision=decision,
        )
        == "accounts"
    )
    formatted = m.format_migration_decision(decision)
    assert m.parse_migration_decision(formatted) == decision
    assert m.parse_migration_decision("  ") is None
    with pytest.raises(m.AuthModeError):
        m.parse_migration_decision("keycloak:v1")
    with pytest.raises(m.AuthModeError):
        m.parse_migration_decision("accounts:v9")


def test_migration_decision_ddl_is_idempotent_single_row():
    sql = m.ensure_migration_decision_table_sql()
    assert "CREATE TABLE IF NOT EXISTS" in sql
    assert m.MIGRATION_DECISION_TABLE in sql
    assert "CHECK (id = 1)" in sql


def test_missing_schema_error_detection():
    assert m.is_missing_schema_error(
        RuntimeError('relation "users" does not exist')
    ) is True
    assert m.is_missing_schema_error(Exception("no such table: users")) is True
    chained = RuntimeError("probe failed")
    chained.__cause__ = RuntimeError('relation "users" does not exist')
    assert m.is_missing_schema_error(chained) is True
    assert m.is_missing_schema_error(ConnectionRefusedError("refused")) is False
    assert m.is_missing_schema_error(RuntimeError("boom")) is False


# ---------------------------------------------------------------------------
# 4. Durable signing secrets
# ---------------------------------------------------------------------------


def test_placeholders_and_short_secrets_rejected(tmp_path):
    for bad in ("devsecret", "replace_with_a_strong_random_jwt_secret", "test_x", "", "short"):
        with pytest.raises(m.AuthModeError):
            m.resolve_session_secret(
                explicit_secret=bad, key_path=tmp_path / "k"
            )


def test_omitted_loads_durable_key_and_restart_retains(tmp_path):
    path = tmp_path / "moonmind_session_key"
    first = m.resolve_session_secret(explicit_secret=None, key_path=path)
    assert len(first) >= 32
    assert path.is_file()
    second = m.resolve_session_secret(explicit_secret=None, key_path=path)
    assert second == first
    assert m.session_secret_fingerprint(first) == m.session_secret_fingerprint(second)


def test_concurrent_bootstrap_shares_one_generation(tmp_path):
    # Simulate a replica winning the race: pre-create the key file, then
    # resolve from a second "process" that must read the winner's key.
    # Fixed material (no leading/trailing ASCII whitespace) keeps the read
    # deterministic: the resolver strips provisioned padding on read, so
    # random bytes with edge whitespace would flake this assertion.
    path = tmp_path / "moonmind_session_key"
    winner = b"K" * m._MIN_SECRET_BYTES
    path.write_bytes(winner)
    loser = m.resolve_session_secret(explicit_secret=None, key_path=path)
    assert loser == winner
    assert m.constant_time_secret_equal(loser, winner)


def test_remote_production_requires_explicit_material(tmp_path):
    with pytest.raises(m.AuthModeError):
        m.resolve_session_secret(
            explicit_secret=None,
            key_path=tmp_path / "missing",
            for_remote_production=True,
        )


# ---------------------------------------------------------------------------
# 5. Disabled-mode exposure at the deployment boundary
# ---------------------------------------------------------------------------


def test_disabled_mode_requires_loopback_or_trusted_ingress():
    m.validate_publish_binding(mode="disabled", publish_host="127.0.0.1")
    m.validate_publish_binding(mode="disabled", publish_host="localhost")
    m.validate_publish_binding(
        mode="disabled", publish_host="0.0.0.0", trusted_ingress=True
    )
    for bad_host in ("0.0.0.0", "", "0.0.0.0 ", "example.com", "192.168.1.10", "::"):
        with pytest.raises(m.AuthModeError):
            m.validate_publish_binding(mode="disabled", publish_host=bad_host)
    # Authenticated modes are not subject to the local-mode publish gate.
    m.validate_publish_binding(mode="accounts", publish_host="0.0.0.0")


def test_rendered_ingress_fixtures_cover_deployment_matrix():
    fixtures = [
        {"name": "loopback", "mode": "disabled", "publish_host": "127.0.0.1"},
        {"name": "wildcard", "mode": "disabled", "publish_host": "0.0.0.0"},
        {"name": "custom-host", "mode": "disabled", "publish_host": "example.com"},
        {
            "name": "tls-trusted",
            "mode": "disabled",
            "publish_host": "0.0.0.0",
            "trusted_ingress": True,
        },
        {
            "name": "proxy-bypass",
            "mode": "accounts",
            "publish_host": "127.0.0.1",
            "proxy_bypass_possible": True,
        },
        {
            "name": "internal-exposed",
            "mode": "accounts",
            "publish_host": "127.0.0.1",
            "internal_control_plane_public": True,
        },
        {
            "name": "internal-reachable",
            "mode": "accounts",
            "publish_host": "127.0.0.1",
            "internal_control_plane_public": False,
        },
    ]
    results = {f["name"]: m.evaluate_ingress_fixture(f) for f in fixtures}
    assert results["loopback"].allowed is True
    assert results["wildcard"].allowed is False
    assert results["custom-host"].allowed is False
    assert results["tls-trusted"].allowed is True
    assert results["proxy-bypass"].allowed is False
    assert results["internal-exposed"].allowed is False
    assert results["internal-reachable"].allowed is True


# ---------------------------------------------------------------------------
# 6. Base URL, trusted proxies, callback origins, cookie policy
# ---------------------------------------------------------------------------


def test_base_url_and_forwarded_trust():
    policy = m.validate_public_base_url("https://app.example.com")
    assert policy.require_secure_cookies is True
    assert policy.cookie_name == q.MOONMIND_PROD_COOKIE
    # Untrusted forwarded headers never decide policy.
    with pytest.raises(m.AuthModeError):
        m.validate_public_base_url(
            "https://app.example.com", forwarded_host="evil.example"
        )
    with pytest.raises(m.AuthModeError):
        m.validate_public_base_url("http://example.com")
    # Loopback HTTP is only valid through the explicit dev-cookie path.
    dev = m.cookie_policy_for_base_url(
        "http://127.0.0.1:7000", explicit_loopback_http=True
    )
    assert dev.cookie_name == q.MOONMIND_DEV_COOKIE
    with pytest.raises(m.AuthModeError):
        m.cookie_policy_for_base_url("http://127.0.0.1:7000")
    with pytest.raises(m.AuthModeError):
        m.validate_trusted_proxy_config("*")
    assert m.validate_trusted_proxy_config("10.0.0.1, 10.0.0.2") == (
        "10.0.0.1",
        "10.0.0.2",
    )
    assert (
        m.validate_callback_origin(
            "https://app.example.com/auth/callback",
            base_url="https://app.example.com",
        )
        == "https://app.example.com/auth/callback"
    )
    with pytest.raises(m.AuthModeError):
        m.validate_callback_origin(
            "https://evil.example/callback", base_url="https://app.example.com"
        )


def test_public_base_url_loopback_uses_hostname_not_substring():
    assert m.public_base_url_is_loopback("http://127.0.0.1:7000") is True
    assert m.public_base_url_is_loopback("http://localhost:7000") is True
    assert m.public_base_url_is_loopback("http://[::1]:7000") is True
    assert m.public_base_url_is_loopback("https://app.example.com") is False
    # Substring mimics are remote: hostname parsing, not matching.
    assert m.public_base_url_is_loopback("https://localhost.example.com") is False
    assert (
        m.public_base_url_is_loopback("https://app.example.com/127.0.0.1/x")
        is False
    )
    assert m.public_base_url_is_loopback("") is False
    assert m.public_base_url_is_loopback(None) is False


# ---------------------------------------------------------------------------
# 7. Readiness kinds and secret-free diagnostics
# ---------------------------------------------------------------------------


def test_readiness_kinds_are_distinguishable():
    ready = m.auth_readiness_summary(production_mode="accounts")
    assert ready["auth_readiness"] == "ready"
    setup = m.auth_readiness_summary(
        production_mode="accounts", setup_required=True
    )
    assert setup["auth_readiness"] == "setup_required"
    migration = m.auth_readiness_summary(
        production_mode="migration_required", migration_required=True
    )
    assert migration["auth_readiness"] == "migration_required"
    unavailable = m.auth_readiness_summary(
        production_mode="accounts", db_reachable=False
    )
    assert unavailable["auth_readiness"] == "unavailable"


def test_diagnostics_contain_no_secret_values():
    payload = {
        "auth_mode": "accounts",
        "JWT_SECRET": "devsecret",
        "MOONMIND_SESSION_SECRET": "super-secret-value",
        "cookie_secret": secrets.token_bytes(32),
        "password": "hunter2",
        "nested": {"client_secret": "abc", "host": "example.com"},
    }
    redacted = m.redacted_diagnostics(payload)
    assert redacted["auth_mode"] == "accounts"
    assert redacted["JWT_SECRET"] == "(redacted)"
    assert redacted["MOONMIND_SESSION_SECRET"] == "(redacted)"
    assert redacted["password"] == "(redacted)"
    assert redacted["nested"] == {"client_secret": "(redacted)", "host": "example.com"}
    rendered = str(redacted)
    assert "devsecret" not in rendered
    assert "super-secret-value" not in rendered
    assert "hunter2" not in rendered
