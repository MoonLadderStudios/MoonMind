"""K3 executable contract tests for MoonLadderStudios/MoonMind#4120.

Covers the bounded backlog from the implement assessment (R1 partially-met,
R2 partially-met, R3-R7 unmet): one canonical AUTH_PROVIDER selector,
explicit control-plane isolation, versioned migration decisions, durable
signing secrets, disabled-exposure guards, proxy/cookie security, and
redacted distinguishable readiness/diagnostics. All hermetic: no DB, no
network, no upstream import.
"""

from __future__ import annotations

import json
import os
import threading

import pytest

from moonmind.security import auth_modes as m


# ---------------------------------------------------------------------------
# R1: one selector, no aliases, no fallback, actionable migration errors
# ---------------------------------------------------------------------------


def test_supported_selectors_normalize():
    assert m.validate_auth_provider("accounts") == "accounts"
    assert m.validate_auth_provider(" ACCOUNTS ") == "accounts"
    assert m.validate_auth_provider("DISABLED") == "disabled"


def test_retired_selectors_fail_with_guidance():
    for retired in ("keycloak", "default", "google", "local"):
        with pytest.raises(m.AuthConfigError, match="removed|retired"):
            m.validate_auth_provider(retired)


def test_unknown_selectors_fail_closed():
    with pytest.raises(m.AuthConfigError, match="Unknown AUTH_PROVIDER"):
        m.validate_auth_provider("saml")
    with pytest.raises(m.AuthConfigError, match="Unknown AUTH_PROVIDER"):
        m.validate_auth_provider("")


def test_oidc_settings_delegates_to_canonical_owner(monkeypatch):
    from moonmind.config.settings import OIDCSettings

    assert OIDCSettings.SUPPORTED_AUTH_PROVIDERS == m.SUPPORTED_AUTH_MODES
    assert set(OIDCSettings.RETIRED_AUTH_PROVIDERS) == set(m.RETIRED_AUTH_SELECTORS)
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    holder = OIDCSettings()
    assert holder.validate_auth_provider() == "accounts"
    holder.AUTH_PROVIDER = "keycloak"
    with pytest.raises(Exception, match="removed|retired"):
        holder.validate_auth_provider()


def test_qualification_adapter_shares_canonical_contract():
    from moonmind.security import omnigent_auth_qualification as q

    assert set(q.RETIRED_SELECTORS) == set(m.RETIRED_AUTH_SELECTORS)
    assert q.validate_mode_selector("header") == "header"
    for retired in ("keycloak", "default", "google", "local"):
        with pytest.raises(Exception, match="removed|retired"):
            q.validate_mode_selector(retired)


def test_env_template_documents_moonmind_auth_contract():
    template = open(".env-template", encoding="utf-8").read()
    assert "AUTH_PROVIDER=" in template
    assert "OIDC_ISSUER_URL=" in template
    assert "MOONMIND_SESSION_SECRET" in template
    assert "MOONMIND_AUTH_MIGRATION_DECISION_FILE" in template
    # Runtime-server values stay documented as runtime-owned, not MoonMind selectors.
    assert "OMNIGENT_AUTH_PROVIDER=" in template


# ---------------------------------------------------------------------------
# R2: control-plane isolation (contradictory ambient + same-origin)
# ---------------------------------------------------------------------------


def test_resolve_auth_mode_ignores_contradictory_runtime_ambient(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "accounts")
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "oidc")
    monkeypatch.setenv("OMNIGENT_AUTH_ENABLED", "1")
    assert m.resolve_auth_mode() == "accounts"
    # Explicit input wins over ambient MoonMind env too, but runtime ambient
    # never participates either way.
    assert m.resolve_auth_mode(explicit="header") == "header"


def test_control_plane_config_never_reads_runtime_env(monkeypatch):
    monkeypatch.setenv("OMNIGENT_AUTH_PROVIDER", "oidc")
    monkeypatch.setenv("OMNIGENT_ACCOUNTS_COOKIE_SECRET", "runtime-secret-value-xyz")
    secret = b"x" * 32
    config = m.resolve_control_plane_config(mode="accounts", cookie_secret=secret)
    assert config.mode == "accounts"
    assert config.cookie_secret == secret
    assert config.cookie_name == m.MOONMIND_PROD_COOKIE
    kwargs = config.to_qualification_kwargs()
    assert kwargs["mode"] == "accounts"
    assert "OMNIGENT" not in json.dumps({k: str(v) for k, v in kwargs.items() if k != "cookie_secret"})


def test_two_authorities_same_origin_stay_isolated():
    moonmind = m.resolve_control_plane_config(mode="accounts", cookie_secret=b"m" * 32)
    runtime_cookie, runtime_secret = "ap_session", b"r" * 32
    assert moonmind.cookie_name != runtime_cookie
    assert moonmind.cookie_secret != runtime_secret
    assert moonmind.token_issuer == m.MOONMIND_TOKEN_ISSUER
    assert moonmind.token_audience == m.MOONMIND_TOKEN_AUDIENCE


# ---------------------------------------------------------------------------
# R3: fresh vs pre-cutover via versioned persisted decision
# ---------------------------------------------------------------------------


def test_migration_decision_roundtrip(tmp_path):
    path = tmp_path / "decision.json"
    decision = m.AuthMigrationDecision(decision="accounts", decided_by="operator")
    m.save_migration_decision(path, decision)
    loaded = m.load_migration_decision(path)
    assert loaded is not None and loaded.decision == "accounts"
    assert loaded.version == m.MIGRATION_DECISION_VERSION
    assert m.load_migration_decision(tmp_path / "missing.json") is None
    with pytest.raises(m.AuthConfigError):
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        m.load_migration_decision(tmp_path / "bad.json")


def test_fresh_install_selects_accounts_like_explicit(tmp_path):
    # Omitted selector on a fresh DB selects accounts through the same
    # production code as explicit accounts mode (no mandatory .env, no
    # public first-owner claim here — mode selection only).
    fresh_omitted = m.decide_auth_bootstrap(
        mode="disabled", mode_explicitly_set=False, db_has_users=False,
        migration_decision=None,
    )
    fresh_explicit = m.decide_auth_bootstrap(
        mode="accounts", mode_explicitly_set=True, db_has_users=False,
        migration_decision=None,
    )
    assert fresh_omitted.action == "proceed"
    assert fresh_omitted.mode == "accounts" == fresh_explicit.mode
    assert fresh_omitted.fresh_install is True


def test_populated_db_requires_explicit_matching_decision():
    matching = m.AuthMigrationDecision(decision="accounts")
    ok = m.decide_auth_bootstrap(
        mode="accounts", mode_explicitly_set=True, db_has_users=True,
        migration_decision=matching,
    )
    assert ok.action == "proceed" and ok.fresh_install is False
    # Omitted variables on existing data: stop actionably, modify no owners.
    for mode, explicit, decision in [
        ("disabled", False, None),
        ("disabled", False, m.AuthMigrationDecision(decision="pending")),
        ("accounts", True, None),
        ("accounts", True, m.AuthMigrationDecision(decision="pending")),
        ("accounts", True, m.AuthMigrationDecision(decision="oidc")),
        ("accounts", False, m.AuthMigrationDecision(decision="accounts")),
    ]:
        blocked = m.decide_auth_bootstrap(
            mode=mode, mode_explicitly_set=explicit, db_has_users=True,
            migration_decision=decision,
        )
        assert blocked.action == "require-operator-choice", (mode, explicit, decision)
        assert "migration decision" in blocked.reason


# ---------------------------------------------------------------------------
# R4: durable signing secrets
# ---------------------------------------------------------------------------


def test_placeholder_and_short_secrets_rejected():
    for bad in ("devsecret", "test_jwt_secret_key", "replace_with_a_strong_random",
                "default_password_please_change", "short", ""):
        with pytest.raises(m.AuthConfigError):
            m.check_explicit_secret_strength(bad)
    m.check_explicit_secret_strength("a-strong-32-byte-minimum-secret!!")


def test_explicit_secret_wins_over_file(tmp_path):
    secret_file = tmp_path / "session_secret"
    secret_file.write_bytes(b"f" * 32)
    out = m.resolve_session_secret("e" * 40, secret_file=secret_file)
    assert out == b"e" * 40


def test_restart_retains_generated_secret(tmp_path):
    secret_file = tmp_path / "session_secret"
    first = m.resolve_session_secret(secret_file=secret_file, env={})
    assert len(first) >= 32
    second = m.resolve_session_secret(secret_file=secret_file, env={})
    assert second == first
    import stat as _stat

    assert _stat.S_IMODE(os.stat(secret_file).st_mode) == 0o600


def test_concurrent_bootstrap_converges_on_one_generation(tmp_path):
    secret_file = tmp_path / "session_secret"
    results: list[bytes] = []

    def _bootstrap():
        results.append(m.resolve_session_secret(secret_file=secret_file, env={}))

    threads = [threading.Thread(target=_bootstrap) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 8
    assert all(value == results[0] for value in results)


def test_rotation_keeps_previous_without_regeneration(tmp_path):
    secret_file = tmp_path / "session_secret"
    m.resolve_session_secret(secret_file=secret_file, env={})
    resolved_primary, previous = m.resolve_session_secrets(
        explicit="z" * 40, previous=[b"o" * 32], secret_file=secret_file,
    )
    assert resolved_primary == b"z" * 40
    assert previous == [b"o" * 32]


def test_secret_never_shares_runtime_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIGENT_ACCOUNTS_COOKIE_SECRET", "r" * 32)
    secret_file = tmp_path / "session_secret"
    out = m.resolve_session_secret(secret_file=secret_file, env={})
    assert out != b"r" * 32


# ---------------------------------------------------------------------------
# R5: disabled-exposure guard
# ---------------------------------------------------------------------------


def test_disabled_loopback_bindings_pass():
    m.validate_disabled_exposure(["127.0.0.1:7000:8000"])
    m.validate_disabled_exposure(["localhost:7000:8000"])
    m.validate_disabled_exposure(["[::1]:7000:8000"])


def test_disabled_wildcard_custom_and_bypass_fail():
    with pytest.raises(m.AuthConfigError, match="all interfaces"):
        m.validate_disabled_exposure(["7000:8000"])
    with pytest.raises(m.AuthConfigError, match="not loopback"):
        m.validate_disabled_exposure(["192.168.1.10:7000:8000"])
    with pytest.raises(m.AuthConfigError, match="wildcard"):
        m.validate_disabled_exposure(["0.0.0.0:7000:8000"])
    with pytest.raises(m.AuthConfigError, match="alternate listener"):
        m.validate_disabled_exposure(
            ["127.0.0.1:7000:8000"], alternate_listeners=["0.0.0.0:9000"]
        )
    with pytest.raises(m.AuthConfigError, match="proxy bypass"):
        m.validate_disabled_exposure(
            ["127.0.0.1:7000:8000"], proxy_bypass_possible=True
        )
    # Documented trusted-ingress evidence covers non-loopback publish.
    m.validate_disabled_exposure(
        ["192.168.1.10:7000:8000"], trusted_ingress_evidence=True
    )


def test_no_synthetic_admin_constructors_remain():
    import api_service.auth_providers as providers
    import inspect

    assert not hasattr(providers, "_disabled_auth_fallback_user")
    source = inspect.getsource(providers.get_current_user)
    assert "is_superuser=True" not in source
    assert "503" in source


# ---------------------------------------------------------------------------
# R6: base URL, proxy, callback origins, cookie policy
# ---------------------------------------------------------------------------


def test_cookie_policy_production_and_dev_loopback():
    prod = m.resolve_cookie_policy(is_https=True, request_host="example.com")
    assert prod.cookie_name == m.MOONMIND_PROD_COOKIE and prod.secure is True
    dev = m.resolve_cookie_policy(
        is_https=False, request_host="127.0.0.1", explicit_dev_loopback_http=True
    )
    assert dev.cookie_name == m.MOONMIND_DEV_COOKIE and dev.secure is False
    # Dev cookie never escapes explicit loopback HTTP.
    assert (
        m.resolve_cookie_policy(is_https=False, request_host="example.com",
                                explicit_dev_loopback_http=True).cookie_name
        == m.MOONMIND_PROD_COOKIE
    )
    assert (
        m.resolve_cookie_policy(is_https=True, request_host="127.0.0.1",
                                explicit_dev_loopback_http=True).cookie_name
        == m.MOONMIND_PROD_COOKIE
    )


def test_untrusted_forwarded_headers_fail_closed():
    m.validate_proxy_config(m.ProxyConfig())  # nothing presented: fine
    with pytest.raises(m.AuthConfigError, match="Untrusted forwarded"):
        m.validate_proxy_config(m.ProxyConfig(), forwarded_host="evil.example")
    with pytest.raises(m.AuthConfigError, match="Untrusted forwarded"):
        m.validate_proxy_config(
            m.ProxyConfig(trusted_proxies=("10.0.0.1",)),
            forwarded_proto="https",
        )
    # Explicit trusted-proxy configuration honors forwarded headers.
    m.validate_proxy_config(
        m.ProxyConfig(trusted_proxies=("10.0.0.1",), trust_forwarded_headers=True),
        forwarded_host="app.example.com",
        forwarded_proto="https",
    )


def test_base_url_and_callback_origins():
    assert m.validate_public_base_url("https://app.example.com") == "https://app.example.com"
    with pytest.raises(m.AuthConfigError):
        m.validate_public_base_url("http://app.example.com")
    m.validate_public_base_url("http://127.0.0.1:7000")
    good = m.validate_callback_origin(
        "https://app.example.com", "https://app.example.com/auth/callback"
    )
    assert good.startswith("https://app.example.com")
    with pytest.raises(m.AuthConfigError, match="open redirect"):
        m.validate_callback_origin("https://app.example.com", "https://evil.example/cb")


def test_compose_rendered_defaults_cover_auth_contract():
    rendered = open("docker-compose.yaml", encoding="utf-8").read()
    assert "AUTH_PROVIDER=${AUTH_PROVIDER:-disabled}" in rendered
    assert "MOONMIND_SESSION_SECRET_FILE" in rendered
    assert "MOONMIND_AUTH_MIGRATION_DECISION_FILE" in rendered


# ---------------------------------------------------------------------------
# R7: readiness + redacted diagnostics
# ---------------------------------------------------------------------------


def test_readiness_states_distinguishable():
    infra_down = m.build_auth_readiness(db_reachable=False, mode="accounts", bootstrap=None)
    assert (infra_down.infrastructure, infra_down.authentication) == ("degraded", "blocked")
    blocked = m.BootstrapDecision(
        action="require-operator-choice", mode="disabled",
        fresh_install=False, reason="record a decision",
    )
    setup = m.build_auth_readiness(db_reachable=True, mode="disabled", bootstrap=blocked)
    assert setup.authentication == "setup-required" and setup.setup_required is True
    ready = m.build_auth_readiness(db_reachable=True, mode="accounts", bootstrap=None)
    assert (ready.infrastructure, ready.authentication) == ("ready", "ready")


def test_diagnostics_contain_no_secret_values():
    secret = "super-secret-session-value-0123456789"
    diagnostics = m.build_auth_diagnostics(
        mode="accounts", db_reachable=True, secret_configured=True,
        extra={"MOONMIND_SESSION_SECRET": secret, "note": "plain"},
    )
    rendered = json.dumps(diagnostics)
    assert secret not in rendered
    assert diagnostics["MOONMIND_SESSION_SECRET"] == "<set>"
    assert diagnostics["session_secret"] == "<set>"
    assert diagnostics["note"] == "plain"
