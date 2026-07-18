"""Unit tests for Omnigent runtime gate settings."""

from __future__ import annotations

from moonmind.omnigent.settings import (
    build_omnigent_gate,
    resolved_host_runner_token,
    resolve_host_runner_credential,
    resolved_server_url,
)


def test_omnigent_gate_disabled_when_env_missing() -> None:
    gate = build_omnigent_gate(env={})

    assert gate.enabled is False
    assert gate.missing == ("OMNIGENT_ENABLED", "OMNIGENT_SERVER_URL")


def test_omnigent_gate_requires_server_url_when_enabled() -> None:
    gate = build_omnigent_gate(env={"OMNIGENT_ENABLED": "1"})

    assert gate.enabled is False
    assert gate.missing == ("OMNIGENT_SERVER_URL",)


def test_omnigent_gate_preserves_explicit_false_values() -> None:
    for raw_enabled in ("false", "0", False, 0):
        gate = build_omnigent_gate(env={"OMNIGENT_ENABLED": raw_enabled})

        assert gate.enabled is False
        assert gate.missing == ()


def test_omnigent_gate_enabled_with_flag_and_server_url() -> None:
    env = {
        "OMNIGENT_ENABLED": "true",
        "OMNIGENT_SERVER_URL": " https://omnigent.example.test ",
        "OMNIGENT_API_TOKEN": "activity-boundary-only",
    }

    gate = build_omnigent_gate(env=env)

    assert gate.enabled is True
    assert gate.missing == ()
    assert resolved_server_url(env=env) == "https://omnigent.example.test"


def test_host_runner_token_resolves_service_side_secret() -> None:
    env = {
        "OMNIGENT_HOST_RUNNER_TOKEN_REF": "env://EMBEDDED_TOKEN",
        "EMBEDDED_TOKEN": " embedded-host-token ",
        "OMNIGENT_HOST_RUNNER_CREDENTIAL_GENERATION": "4",
    }
    credential = resolve_host_runner_credential(env=env)

    assert resolved_host_runner_token(env=env) == "embedded-host-token"
    assert credential.secret_ref == "env://EMBEDDED_TOKEN"
    assert credential.generation == 4
    assert "embedded-host-token" not in repr(credential.secret_ref)


def test_proxy_forward_headers_empty_by_default() -> None:
    from moonmind.omnigent.settings import resolved_proxy_forward_headers

    assert resolved_proxy_forward_headers(env={}) == frozenset()


def test_proxy_forward_headers_parses_comma_separated_allowlist() -> None:
    from moonmind.omnigent.settings import resolved_proxy_forward_headers

    resolved = resolved_proxy_forward_headers(
        env={"OMNIGENT_PROXY_FORWARD_HEADERS": " X-Trace-Id , X-MoonMind-Trace ,"}
    )

    assert resolved == frozenset({"x-trace-id", "x-moonmind-trace"})
