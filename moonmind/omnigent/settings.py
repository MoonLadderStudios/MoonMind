"""Runtime gate for the Omnigent external agent integration."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

OMNIGENT_DISABLED_MESSAGE = (
    "agentId=omnigent requires OMNIGENT_ENABLED=true with "
    "OMNIGENT_SERVER_URL configured"
)
OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR = "/opt/moonmind-skills"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

OMNIGENT_GENERIC_HOST_ENABLED_ENV = "MOONMIND_OMNIGENT_GENERIC_HOST_ENABLED"
OMNIGENT_OPENCODE_ENABLED_ENV = "MOONMIND_OMNIGENT_OPENCODE_ENABLED"
MOONMIND_OMNIGENT_EVIDENCE_POLICY_ENV = "MOONMIND_OMNIGENT_EVIDENCE_POLICY"
_DEPLOYMENT_EVIDENCE_POLICY_VALUES = {"deployment", "protected", "either"}

# An Omnigent server image is immutable launch authority only when it names a
# sha256 digest. Mirrors the launch-policy rule in
# ``moonmind/omnigent/execution_profiles.py`` so the native-UI gate and the
# execution catalog agree on one definition of immutable image evidence.
_IMMUTABLE_IMAGE_REF = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class OmnigentRuntimeGate:
    """Whether Omnigent is enabled and required env vars are present."""

    enabled: bool
    missing: tuple[str, ...]
    error_message: str


def _clean(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _enabled_from_env(*, env: Mapping[str, Any]) -> bool:
    raw = _clean(env.get("OMNIGENT_ENABLED"))
    if not raw:
        return False
    lowered = raw.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return False


def build_omnigent_gate(
    *,
    env: Mapping[str, Any] | None = None,
    error_message: str = OMNIGENT_DISABLED_MESSAGE,
) -> OmnigentRuntimeGate:
    """Return gate state for Omnigent (env-driven)."""

    source = env if env is not None else os.environ
    enabled_flag = _enabled_from_env(env=source)
    raw_enabled = source.get("OMNIGENT_ENABLED")
    server_url = _clean(source.get("OMNIGENT_SERVER_URL"))

    missing: list[str] = []
    if raw_enabled is None or _clean(raw_enabled) == "":
        missing.append("OMNIGENT_ENABLED")
        if not server_url:
            missing.append("OMNIGENT_SERVER_URL")
    elif enabled_flag and not server_url:
        missing.append("OMNIGENT_SERVER_URL")

    return OmnigentRuntimeGate(
        enabled=enabled_flag and len(missing) == 0,
        missing=tuple(missing),
        error_message=error_message,
    )


def is_omnigent_enabled(*, env: Mapping[str, Any] | None = None) -> bool:
    return build_omnigent_gate(env=env).enabled


OPENCODE_API_KEY_ENV = "OPENCODE_API_KEY"
OPENCODE_CONTRIBUTOR_DATA_USE_ENV = "OPENCODE_ACCEPT_CONTRIBUTOR_DATA_USE"


def _parse_bool_with_default(
    value: object | None, *, default: bool
) -> bool:
    cleaned = _clean(value)
    if not cleaned:
        return default
    lowered = cleaned.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    raise ValueError(
        f"invalid boolean value {cleaned!r}: expected one of {sorted(_TRUE_VALUES | _FALSE_VALUES)}"
    )


def generic_host_enabled(*, env: Mapping[str, Any] | None = None) -> bool:
    """Return whether the complete generic host plane is enabled.

    This gate means the production planner, lease, credential, host, and
    cleanup services are wired.  It is intentionally separate from support
    qualification for any individual harness.

    Default is enabled (true) so the capability is available for configuration.
    Readiness remains computed state (setup_required, preparing, etc.).
    Explicit false remains an emergency kill switch.
    """

    source = env if env is not None else os.environ
    return _parse_bool_with_default(
        source.get(OMNIGENT_GENERIC_HOST_ENABLED_ENV), default=True
    )


def opencode_support_enabled(*, env: Mapping[str, Any] | None = None) -> bool:
    """Return whether the qualified OpenCode combination may be advertised.

    Default is enabled. Explicit false disables the OpenCode harness.
    """

    source = env if env is not None else os.environ
    return _parse_bool_with_default(
        source.get(OMNIGENT_OPENCODE_ENABLED_ENV), default=True
    )


def resolved_opencode_api_key(*, env: Mapping[str, Any] | None = None) -> str:
    """Return the deployment-configured OpenCode Go API key, if any.

    Presence of this value is what makes the default Compose path launchable
    without a separate console action: the bootstrap reconciler enrolls and
    runtime-validates the OpenCode Provider Profile from it.
    """

    source = env if env is not None else os.environ
    return _clean(source.get(OPENCODE_API_KEY_ENV))


def opencode_contributor_data_use_accepted(
    *, env: Mapping[str, Any] | None = None
) -> bool:
    """Return whether the operator accepts OpenCode contributor data use.

    The stock OpenCode Go model is a contributor tier whose enrollment requires
    an explicit data-use acknowledgement. It defaults to accepted so the
    documented one-value setup completes, and stays an explicit, documented
    switch an operator can set to ``false``.
    """

    source = env if env is not None else os.environ
    return _parse_bool_with_default(
        source.get(OPENCODE_CONTRIBUTOR_DATA_USE_ENV), default=True
    )


def omnigent_evidence_policy(
    *, env: Mapping[str, Any] | None = None
) -> str:
    """Return the execution evidence admission policy.

    Values: deployment, protected, either (default).
    Deployment accepts locally-generated deployment qualification evidence.
    Protected requires protected CI evidence for official support tier.
    """

    source = env if env is not None else os.environ
    raw = _clean(source.get(MOONMIND_OMNIGENT_EVIDENCE_POLICY_ENV)).lower()
    if not raw:
        return "either"
    if raw in _DEPLOYMENT_EVIDENCE_POLICY_VALUES:
        return raw
    raise ValueError(
        f"invalid evidence policy {raw!r}: expected one of {sorted(_DEPLOYMENT_EVIDENCE_POLICY_VALUES)}"
    )


def resolved_server_url(*, env: Mapping[str, Any] | None = None) -> str:
    """Return configured Omnigent server URL."""

    source = env if env is not None else os.environ
    return _clean(source.get("OMNIGENT_SERVER_URL"))


def resolved_api_token(*, env: Mapping[str, Any] | None = None) -> str:
    """Return configured Omnigent API token."""

    source = env if env is not None else os.environ
    return _clean(source.get("OMNIGENT_API_TOKEN"))


def resolved_default_agent_name(*, env: Mapping[str, Any] | None = None) -> str:
    """Return configured default Omnigent agent name."""

    source = env if env is not None else os.environ
    return _clean(source.get("OMNIGENT_DEFAULT_AGENT_NAME"))


def resolved_native_ui_version(*, env: Mapping[str, Any] | None = None) -> str:
    """Return the asserted native Omnigent UI/server version for the deployment.

    MoonLadderStudios/MoonMind#3638, MoonLadderStudios/MoonMind#3685: serving the
    native UI is gated on a known-compatible version, and a mutable image tag
    must never be reported as verified.

    The version comes from one authority, in precedence order:

    1. an explicit ``OMNIGENT_NATIVE_UI_VERSION`` pin, which an operator sets
       after a new upstream build passes conformance; otherwise
    2. verified immutable image evidence — when the deployment's declared
       Omnigent server image (``OMNIGENT_IMAGE_REF``) names a sha256 digest, the
       deployment runs the immutable build MoonMind's launch authority is pinned
       to, so the single upstream source pin (``PINNED_OMNIGENT_COMMIT``) is the
       reported version.

    A mutable image reference such as ``ghcr.io/...:latest`` yields no version,
    so :func:`evaluate_native_ui_compatibility` returns
    ``native_ui_version_unknown`` and serving fails closed (AC11/AC12).
    Deriving from the same immutable image reference the execution catalog and
    launch policies already require means any deployment that can launch
    Omnigent also serves native Workflow Chat, with no separate override.
    """

    from moonmind.omnigent.host_auth_adapter import PINNED_OMNIGENT_COMMIT

    source = env if env is not None else os.environ
    explicit = _clean(source.get("OMNIGENT_NATIVE_UI_VERSION"))
    if explicit:
        return explicit
    if _IMMUTABLE_IMAGE_REF.fullmatch(_clean(source.get("OMNIGENT_IMAGE_REF"))):
        return PINNED_OMNIGENT_COMMIT
    return ""


def resolved_native_ui_serving_enabled(*, env: Mapping[str, Any] | None = None) -> bool:
    """Return whether MoonMind serves the native Omnigent UI through its origin.

    Defaults to enabled so the canonical deployment routes native Workflow Chat
    through MoonMind-scoped routes (issue #3638 requirement 7). An operator may
    set ``OMNIGENT_NATIVE_UI_ENABLED=false`` to fall back to the read-only
    compatibility projection without disabling the rest of the bridge.
    """

    source = env if env is not None else os.environ
    raw = _clean(source.get("OMNIGENT_NATIVE_UI_ENABLED"))
    if not raw:
        return True
    return raw.lower() in _TRUE_VALUES


def resolved_host_runner_token(*, env: Mapping[str, Any] | None = None) -> str:
    """Return the embedded host/runner auth token configured service-side."""

    source = env if env is not None else os.environ
    return _clean(source.get("OMNIGENT_HOST_RUNNER_TOKEN"))


def resolved_proxy_forward_headers(
    *, env: Mapping[str, Any] | None = None
) -> frozenset[str]:
    """Return the explicitly-configured upstream proxy header allowlist.

    Proxy mode forwards no MoonMind headers upstream by default (OmnigentBridge
    §16 rule 7); operators opt in per header via a comma-separated
    ``OMNIGENT_PROXY_FORWARD_HEADERS``. Names are normalized to lowercase.
    """

    source = env if env is not None else os.environ
    raw = _clean(source.get("OMNIGENT_PROXY_FORWARD_HEADERS"))
    if not raw:
        return frozenset()
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


__all__ = [
    "OMNIGENT_RUNTIME_ACTIVE_SKILLS_DIR",
    "OMNIGENT_DISABLED_MESSAGE",
    "OmnigentRuntimeGate",
    "build_omnigent_gate",
    "is_omnigent_enabled",
    "generic_host_enabled",
    "opencode_support_enabled",
    "opencode_contributor_data_use_accepted",
    "resolved_opencode_api_key",
    "OPENCODE_API_KEY_ENV",
    "OPENCODE_CONTRIBUTOR_DATA_USE_ENV",
    "omnigent_evidence_policy",
    "OMNIGENT_GENERIC_HOST_ENABLED_ENV",
    "OMNIGENT_OPENCODE_ENABLED_ENV",
    "MOONMIND_OMNIGENT_EVIDENCE_POLICY_ENV",
    "resolved_api_token",
    "resolved_default_agent_name",
    "resolved_host_runner_token",
    "resolved_native_ui_serving_enabled",
    "resolved_native_ui_version",
    "resolved_proxy_forward_headers",
    "resolved_server_url",
]
