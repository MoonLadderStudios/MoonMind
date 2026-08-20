"""Unit tests for Omnigent runtime gate settings."""

from __future__ import annotations

from moonmind.omnigent.settings import (
    build_omnigent_gate,
    resolved_host_runner_token,
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
    assert (
        resolved_host_runner_token(
            env={"OMNIGENT_HOST_RUNNER_TOKEN": " embedded-host-token "}
        )
        == "embedded-host-token"
    )


def test_proxy_forward_headers_empty_by_default() -> None:
    from moonmind.omnigent.settings import resolved_proxy_forward_headers

    assert resolved_proxy_forward_headers(env={}) == frozenset()


def test_proxy_forward_headers_parses_comma_separated_allowlist() -> None:
    from moonmind.omnigent.settings import resolved_proxy_forward_headers

    resolved = resolved_proxy_forward_headers(
        env={"OMNIGENT_PROXY_FORWARD_HEADERS": " X-Trace-Id , X-MoonMind-Trace ,"}
    )

    assert resolved == frozenset({"x-trace-id", "x-moonmind-trace"})


# --- Native UI compatibility version authority (#3685 review) ---------------
# The version resolves from one authority: an explicit operator pin, otherwise
# verified immutable image evidence. A mutable tag must never be reported as
# verified, and a deployment that pinned an immutable Omnigent image must not
# need a separate undocumented override to serve native Workflow Chat.

_DIGEST_REF = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "a" * 64


def test_native_ui_version_uses_explicit_operator_pin() -> None:
    from moonmind.omnigent.settings import resolved_native_ui_version

    assert (
        resolved_native_ui_version(
            env={"OMNIGENT_NATIVE_UI_VERSION": " conformance-verified-build "}
        )
        == "conformance-verified-build"
    )


def test_native_ui_version_derives_from_immutable_image_evidence() -> None:
    from moonmind.omnigent.host_auth_adapter import PINNED_OMNIGENT_COMMIT
    from moonmind.omnigent.settings import resolved_native_ui_version

    assert (
        resolved_native_ui_version(env={"OMNIGENT_IMAGE_REF": _DIGEST_REF})
        == PINNED_OMNIGENT_COMMIT
    )


def test_native_ui_version_unknown_for_mutable_image_tag() -> None:
    from moonmind.omnigent.settings import resolved_native_ui_version

    for mutable in (
        "",
        "ghcr.io/omnigent-ai/omnigent-server:latest",
        "ghcr.io/omnigent-ai/omnigent-server:v0.1.1",
        "ghcr.io/omnigent-ai/omnigent-server@sha256:not-a-digest",
    ):
        assert resolved_native_ui_version(env={"OMNIGENT_IMAGE_REF": mutable}) == ""


def test_native_ui_gate_fails_closed_for_mutable_image_and_serves_when_pinned() -> None:
    """The canonical Compose default (mutable tag) fails closed; pinning the
    same immutable image the execution catalog requires makes chat available."""

    from moonmind.omnigent.native_ui import evaluate_native_ui_compatibility
    from moonmind.omnigent.settings import resolved_native_ui_version

    mutable_env = {
        "OMNIGENT_NATIVE_UI_ENABLED": "true",
        "OMNIGENT_IMAGE": "ghcr.io/omnigent-ai/omnigent-server",
        "OMNIGENT_IMAGE_TAG": "latest",
        "OMNIGENT_IMAGE_REF": "",
    }
    mutable = evaluate_native_ui_compatibility(
        resolved_native_ui_version(env=mutable_env)
    )
    assert mutable.ready is False
    assert mutable.reason == "native_ui_version_unknown"

    pinned = evaluate_native_ui_compatibility(
        resolved_native_ui_version(env={**mutable_env, "OMNIGENT_IMAGE_REF": _DIGEST_REF})
    )
    assert pinned.ready is True
    assert pinned.reason is None
