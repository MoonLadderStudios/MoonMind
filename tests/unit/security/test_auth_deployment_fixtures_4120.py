"""Acceptance fixtures for MoonLadderStudios/MoonMind#4120 (remediation).

Rendered compose/ingress coverage through hermetic fixtures: loopback
publish, wildcard publish rejection, custom host, TLS termination, proxy
bypass, and internal runtime reachability. Plus bounded fail-closed
database/secret/configuration failure tests with secret-free diagnostics
assertions. All hermetic: no DB, no network, no container.
"""

from __future__ import annotations

import json

import pytest

from moonmind.security import auth_modes as m


# ---------------------------------------------------------------------------
# Rendered ingress fixtures
# ---------------------------------------------------------------------------

INGRESS_FIXTURES = {
    "loopback-ipv4": {
        "env": {"MOONMIND_API_PUBLISH_HOST": "127.0.0.1"},
        "mode": "disabled",
        "verdict": "pass",
    },
    "loopback-localhost": {
        "env": {"MOONMIND_API_PUBLISH_HOST": "localhost"},
        "mode": "disabled",
        "verdict": "pass",
    },
    "loopback-ipv6": {
        "env": {"MOONMIND_API_PUBLISH_HOST": "::1"},
        "mode": "disabled",
        "verdict": "pass",
    },
    "wildcard-publish": {
        "env": {},
        "mode": "disabled",
        "verdict": "reject",
    },
    "container-wildcard-is-not-evidence": {
        "env": {"MOONMIND_API_PUBLISH_HOST": "0.0.0.0"},
        "mode": "disabled",
        "verdict": "reject",
    },
    "custom-host-without-evidence": {
        "env": {"MOONMIND_API_PUBLISH_HOST": "203.0.113.10"},
        "mode": "disabled",
        "verdict": "reject",
    },
    "custom-host-with-trusted-ingress": {
        "env": {
            "MOONMIND_API_PUBLISH_HOST": "203.0.113.10",
            "MOONMIND_DISABLED_TRUSTED_INGRESS_EVIDENCE": "1",
        },
        "mode": "disabled",
        "verdict": "pass",
    },
    "tls-termination": {
        "env": {
            "MOONMIND_API_PUBLISH_HOST": "127.0.0.1",
            "MOONMIND_PUBLIC_BASE_URL": "https://app.example.com",
        },
        "mode": "accounts",
        "verdict": "pass",
    },
    "plaintext-remote-base-url": {
        "env": {
            "MOONMIND_API_PUBLISH_HOST": "127.0.0.1",
            "MOONMIND_PUBLIC_BASE_URL": "http://app.example.com",
        },
        "mode": "accounts",
        "verdict": "reject",
    },
    "proxy-bypass-without-evidence": {
        "env": {
            "MOONMIND_API_PUBLISH_HOST": "127.0.0.1",
            "MOONMIND_PROXY_BYPASS_POSSIBLE": "1",
        },
        "mode": "disabled",
        "verdict": "reject",
    },
    "proxy-bypass-with-trusted-ingress": {
        "env": {
            "MOONMIND_API_PUBLISH_HOST": "127.0.0.1",
            "MOONMIND_PROXY_BYPASS_POSSIBLE": "1",
            "MOONMIND_DISABLED_TRUSTED_INGRESS_EVIDENCE": "1",
        },
        "mode": "disabled",
        "verdict": "pass",
    },
}


@pytest.mark.parametrize("name", sorted(INGRESS_FIXTURES))
def test_rendered_ingress_fixtures(name):
    fixture = INGRESS_FIXTURES[name]
    config = m.deployment_auth_config_from_env(dict(fixture["env"]))
    if fixture["verdict"] == "pass":
        m.validate_deployment_auth_config(config, effective_mode=fixture["mode"])
    else:
        with pytest.raises(m.AuthConfigError):
            m.validate_deployment_auth_config(config, effective_mode=fixture["mode"])


def test_compose_preserves_host_to_api_networking_without_extra_exposure():
    rendered = open("docker-compose.yaml", encoding="utf-8").read()
    # The host-to-API mapping stays the single published control-plane port.
    assert '"${MOONMIND_API_HOST_PORT:-7000}:8000"' in rendered
    # Fresh installs must not be forced into placeholder rejection: no
    # insecure default secret value is rendered into the deployment.
    assert ":-devsecret}" not in rendered
    assert "JWT_SECRET=${JWT_SECRET:-}" in rendered
    # The deployment-boundary inputs are passed through explicitly.
    for key in (
        "MOONMIND_API_PUBLISH_HOST",
        "MOONMIND_DISABLED_TRUSTED_INGRESS_EVIDENCE",
        "MOONMIND_PUBLIC_BASE_URL",
        "MOONMIND_TRUSTED_PROXIES",
        "MOONMIND_TRUST_FORWARDED_HEADERS",
        "MOONMIND_SESSION_SECRET_FILE",
        "MOONMIND_AUTH_MIGRATION_DECISION_FILE",
    ):
        assert key in rendered


def test_env_template_documents_deployment_boundary():
    template = open(".env-template", encoding="utf-8").read()
    for key in (
        "MOONMIND_API_PUBLISH_HOST=",
        "MOONMIND_API_HOST_PORT=",
        "MOONMIND_ADDITIONAL_LISTENERS=",
        "MOONMIND_PROXY_BYPASS_POSSIBLE=",
        "MOONMIND_DISABLED_TRUSTED_INGRESS_EVIDENCE=",
        "MOONMIND_PUBLIC_BASE_URL=",
        "MOONMIND_TRUSTED_PROXIES=",
        "MOONMIND_TRUST_FORWARDED_HEADERS=",
        "MOONMIND_DEV_LOOPBACK_HTTP_ALLOW=",
    ):
        assert key in template


def test_internal_runtime_addresses_are_not_loopback_evidence():
    # Container-internal listeners prove nothing about the published path.
    assert m.is_loopback_host("0.0.0.0") is False
    assert m.is_loopback_host("::") is False
    assert m.is_loopback_host("moonmind-api") is False
    assert m.is_loopback_host("10.0.0.5") is False


# ---------------------------------------------------------------------------
# Bounded fail-closed failures with secret-free diagnostics
# ---------------------------------------------------------------------------


def test_database_unreachable_readiness_is_distinguishable():
    readiness = m.build_auth_readiness(
        db_reachable=False, mode="accounts", bootstrap=None
    )
    assert readiness.infrastructure == "degraded"
    assert readiness.authentication == "blocked"
    assert readiness.setup_required is False


def test_secret_generation_refused_without_permission_fails_closed(tmp_path):
    secret_file = tmp_path / "session_secret"
    with pytest.raises(m.AuthConfigError, match="not permitted"):
        m.resolve_session_secret(secret_file=secret_file, allow_generate=False, env={})


def test_placeholder_explicit_secret_fails_closed(tmp_path):
    secret_file = tmp_path / "session_secret"
    with pytest.raises(m.AuthConfigError, match="placeholder|insecure"):
        m.resolve_session_secret(
            "devsecret", secret_file=secret_file, env={"JWT_SECRET": "devsecret"}
        )


def test_unknown_selector_and_corrupt_decision_fail_closed(tmp_path):
    with pytest.raises(m.AuthConfigError):
        m.validate_auth_provider("saml")
    bad = tmp_path / "decision.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(m.AuthConfigError, match="unreadable or corrupt"):
        m.load_migration_decision(bad)


def test_deployment_diagnostics_contain_no_secret_values():
    secret = "deployment-secret-value-0123456789abcdef"
    config = m.deployment_auth_config_from_env(
        {
            "MOONMIND_API_PUBLISH_HOST": "127.0.0.1",
            "MOONMIND_SESSION_SECRET": secret,
            "MOONMIND_PUBLIC_BASE_URL": "https://app.example.com",
        }
    )
    diagnostics = m.build_auth_diagnostics(
        mode="disabled",
        db_reachable=True,
        secret_configured=True,
        bindings=[config.published_binding()],
        extra={
            "MOONMIND_SESSION_SECRET": secret,
            "MOONMIND_TRUSTED_PROXIES": "10.0.0.1",
        },
    )
    rendered = json.dumps(diagnostics)
    assert secret not in rendered
    assert diagnostics["MOONMIND_SESSION_SECRET"] == "<set>"
    assert diagnostics["bindings"] == ["127.0.0.1:7000:8000"]
