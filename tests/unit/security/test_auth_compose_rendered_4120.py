"""Rendered Compose/ingress + production-boundary evidence for #4120 G1-G5.

Source issue: MoonLadderStudios/MoonMind#4120. This file pins the
deployment-boundary wiring the verifier gates on:

- blank ``AUTH_PROVIDER`` validates as undecided and classifies to
  omitted-fresh ``accounts`` vs. omitted-populated ``migration_required``;
- the published API port is bound to ``MOONMIND_API_PUBLISH_HOST``
  (loopback by default), not a bare host-port mapping;
- the rendered ingress matrix (loopback, wildcard, custom host, TLS
  termination with trusted ingress, proxy bypass, internal reachability)
  is evaluated through the production owner;
- the production control-plane path resolves explicitly from MoonMind-owned
  inputs while hostile ``OMNIGENT_AUTH_*`` ambient values are ignored;
- the persisted migration-decision row shape round-trips through the
  versioned contract with env precedence.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import yaml

from api_service.auth_providers import build_moonmind_control_plane_config
from moonmind.config.settings import settings
from moonmind.security import auth_modes_4120 as m
from moonmind.security import omnigent_auth_qualification as q


def _service(service: str) -> dict:
    compose = yaml.safe_load(Path("docker-compose.yaml").read_text(encoding="utf-8"))
    return compose["services"][service]


def test_blank_selector_is_undecided_and_classifies():
    settings.oidc.AUTH_PROVIDER = ""
    try:
        assert settings.oidc.validate_auth_provider() == ""
    finally:
        settings.oidc.AUTH_PROVIDER = "disabled"
    # Omitted-fresh and explicit-accounts take the same production path.
    assert (
        m.resolve_production_mode(
            raw_selector="", explicit=False, has_users=False
        )
        == "accounts"
    )
    # Omitted-populated stops actionably without modifying owners.
    classification = m.classify_deployment(
        raw_selector="", explicit=False, has_users=True
    )
    assert classification.migration_required is True
    assert classification.production_mode == "migration_required"


def test_api_ports_bind_publish_host_with_loopback_default():
    ports = _service("api")["ports"]
    assert ports, "api service must publish a host port"
    mapping = str(ports[0])
    assert "MOONMIND_API_PUBLISH_HOST" in mapping, mapping
    assert "MOONMIND_API_HOST_PORT" in mapping, mapping
    # Default collapses to loopback-bound publish under an empty .env.
    assert mapping.startswith("${MOONMIND_API_PUBLISH_HOST:-127.0.0.1}:"), mapping
    environment = _service("api")["environment"]
    entries = (
        environment
        if isinstance(environment, list)
        else [f"{k}={v}" for k, v in environment.items()]
    )
    publish_default = next(
        str(e) for e in entries if str(e).startswith("MOONMIND_API_PUBLISH_HOST=")
    )
    assert "127.0.0.1" in publish_default, publish_default


def test_rendered_ingress_matrix_covers_deployment_boundary():
    fixtures = [
        {"name": "loopback", "mode": "disabled", "publish_host": "127.0.0.1"},
        {"name": "wildcard", "mode": "disabled", "publish_host": "0.0.0.0"},
        {"name": "custom-host", "mode": "disabled", "publish_host": "example.com"},
        {
            "name": "tls-terminated-trusted",
            "mode": "disabled",
            "publish_host": "0.0.0.0",
            "trusted_ingress": True,
            "tls_terminated": True,
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
    assert results["tls-terminated-trusted"].allowed is True
    assert results["proxy-bypass"].allowed is False
    assert results["internal-exposed"].allowed is False
    assert results["internal-reachable"].allowed is True


def test_production_control_plane_ignores_hostile_runtime_ambient():
    secret = secrets.token_bytes(32)
    hostile = {
        "OMNIGENT_AUTH_PROVIDER": "header",
        "OMNIGENT_AUTH_ENABLED": "1",
        "OMNIGENT_ACCOUNTS_COOKIE_SECRET": "runtime-secret",
        "OMNIGENT_OIDC_ISSUER": "https://evil.example",
    }
    # Startup-style explicit resolution from MoonMind-owned inputs.
    config = m.resolve_moonmind_auth_config(
        mode="accounts", cookie_secret=secret, environ=hostile
    )
    assert config.mode == "accounts"
    assert config.cookie_secret == secret
    assert config.cookie_name not in q.UPSTREAM_SESSION_COOKIES
    # Request-time helper with the classified production mode behaves the
    # same way under contradictory ambient values and same-origin use.
    settings.oidc.AUTH_PROVIDER = "accounts"
    try:
        built = build_moonmind_control_plane_config(
            environ=hostile, mode="accounts"
        )
    finally:
        settings.oidc.AUTH_PROVIDER = "disabled"
    assert built.mode == "accounts"
    assert built.cookie_secret is not None


def test_persisted_decision_row_round_trips_with_env_precedence():
    sql = m.ensure_migration_decision_table_sql()
    assert "CREATE TABLE IF NOT EXISTS" in sql
    assert m.MIGRATION_DECISION_TABLE in sql
    persisted = m.AuthMigrationDecision(mode="header", version=1)
    assert m.parse_migration_decision(m.format_migration_decision(persisted)) == persisted
    # Operator-held env decision takes precedence when present; otherwise the
    # persisted row supplies the omitted-selector decision.
    env_decision = m.parse_migration_decision("accounts:v1")
    effective = env_decision if env_decision is not None else persisted
    assert effective.mode == "accounts"
    assert (
        m.resolve_production_mode(
            raw_selector="", explicit=False, has_users=True, migration_decision=effective
        )
        == "accounts"
    )
