"""Static-connected Codex/Claude host consolidation (MoonLadderStudios/MoonMind#3834).

The canonical Compose deployment runs two static-connected Omnigent host
services — ``omnigent-host-codex`` and ``omnigent-host-claude`` — on the one
digest-pinned shared ``omnigent-host-moonmind`` image with one generic startup
entrypoint (``services/omnigent/scripts/start-omnigent-host.sh``) and one
generic health contract (``services/omnigent/scripts/check-omnigent-host.sh``).

This module is the Python-side source of truth for the static rows:

- which trusted runtime-pack and credential-materializer refs each Compose
  service selects (the *only* allowed runtime difference),
- credential isolation (neither row receives the other runtime's state or
  OpenCode API-key state),
- the bounded legacy image-variable alias rule,
- and the rule that Compose process existence is never host authority:
  static rows still resolve through Provider Profile capacity, host binding /
  host lease, credential-generation fencing, exact registered host identity,
  one-session limits, canonical session/turn ownership, and cleanup/drain
  ordering owned by the existing planner, attestation, lease, and cleanup
  modules (referenced, not reimplemented, here).

Source: ``docs/Omnigent/PrimaryRuntimeProviderStrategy.md`` section 10 and
``docs/Omnigent/OmnigentHostOAuth.md`` sections 14, 15, and 20.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)

STATIC_CODEX_SERVICE = "omnigent-host-codex"
STATIC_CLAUDE_SERVICE = "omnigent-host-claude"
STATIC_CODEX_PROFILE = "omnigent-host-codex"
STATIC_CLAUDE_PROFILE = "omnigent-host-claude"

STATIC_CODEX_PACK_REF = "codex-native-pack@1"
STATIC_CLAUDE_PACK_REF = "claude-native-pack@1"
STATIC_CODEX_MATERIALIZER_REF = "codex-oauth-home@1"
STATIC_CLAUDE_MATERIALIZER_REF = "claude-oauth-home@1"

STATIC_CODEX_HOST_CLASS_REF = "omnigent-codex@1"
STATIC_CLAUDE_HOST_CLASS_REF = "omnigent-claude@1"

GENERIC_STATIC_ENTRYPOINT = "/opt/moonmind/start-omnigent-host.sh"
GENERIC_STATIC_HEALTHCHECK = (
    "/opt/moonmind/check-omnigent-host.sh"
    " && /opt/moonmind/check-runner-projections.sh"
)

SHARED_HOST_IMAGE_ENV = "OMNIGENT_SHARED_HOST_IMAGE_REF"
LEGACY_HOST_IMAGE_ENV = "OMNIGENT_HOST_IMAGE_REF"

_DIGEST_PINNED_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")

# Ambient credential selectors that must never be present on a static row.
# Mirrors the deny-list enforced by start-omnigent-host.sh / check scripts.
FORBIDDEN_STATIC_AMBIENT_KEYS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "CODEX_ACCESS_TOKEN",
    "OPENAI_BASE_URL",
    "MINIMAX_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENCODE_AUTH_CONTENT",
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_CONTENT",
)

# Variables that would smuggle arbitrary commands, target paths, or
# environment allowlists into the trusted entrypoint. Never read; presence
# fails closed.
FORBIDDEN_STATIC_CONTROL_KEYS: tuple[str, ...] = (
    "MOONMIND_OMNIGENT_ENTRYPOINT_CMD",
    "MOONMIND_OMNIGENT_EXTRA_ARGS",
    "MOONMIND_OMNIGENT_ENV_ALLOWLIST",
    "MOONMIND_OMNIGENT_TARGET_PATH",
    "MOONMIND_OMNIGENT_MOUNT_PATH",
    "OMNIGENT_HOST_CMD",
    "OMNIGENT_HOST_ENTRYPOINT_ARGS",
)


@dataclass(frozen=True)
class StaticHostRow:
    """One static-connected Compose row and its trusted selections."""

    service: str
    compose_profile: str
    host_class_ref: str
    runtime_pack_ref: str
    materializer_ref: str
    generation_env: str
    credential_home: str


STATIC_HOST_ROWS: tuple[StaticHostRow, ...] = (
    StaticHostRow(
        service=STATIC_CODEX_SERVICE,
        compose_profile=STATIC_CODEX_PROFILE,
        host_class_ref=STATIC_CODEX_HOST_CLASS_REF,
        runtime_pack_ref=STATIC_CODEX_PACK_REF,
        materializer_ref=STATIC_CODEX_MATERIALIZER_REF,
        generation_env="CODEX_CREDENTIAL_GENERATION",
        credential_home="/home/app/.codex",
    ),
    StaticHostRow(
        service=STATIC_CLAUDE_SERVICE,
        compose_profile=STATIC_CLAUDE_PROFILE,
        host_class_ref=STATIC_CLAUDE_HOST_CLASS_REF,
        runtime_pack_ref=STATIC_CLAUDE_PACK_REF,
        materializer_ref=STATIC_CLAUDE_MATERIALIZER_REF,
        generation_env="CLAUDE_CREDENTIAL_GENERATION",
        credential_home="/home/app/.claude",
    ),
)


def static_host_row(service: str) -> StaticHostRow:
    for row in STATIC_HOST_ROWS:
        if row.service == service:
            return row
    raise HarnessPlatformError(
        f"unknown static Omnigent host service {service!r}",
        code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
    )


def validate_static_pack_selection(*, service: str, pack_ref: str) -> StaticHostRow:
    """Validate that a static row selects exactly its trusted pack ref.

    A wrong runtime-pack combination fails before host startup; it never
    silently becomes the other runtime.
    """

    row = static_host_row(service)
    if pack_ref != row.runtime_pack_ref:
        raise HarnessPlatformError(
            f"static host {service} must select {row.runtime_pack_ref}, "
            f"got {pack_ref!r}",
            code=HarnessPlatformFailure.OMNIGENT_RUNTIME_PACK_MISMATCH,
        )
    return row


def validate_static_materializer_selection(
    *, service: str, materializer_ref: str
) -> StaticHostRow:
    """Validate that a static row selects exactly its trusted materializer."""

    row = static_host_row(service)
    if materializer_ref != row.materializer_ref:
        raise HarnessPlatformError(
            f"static host {service} must select {row.materializer_ref}, "
            f"got {materializer_ref!r}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_MATERIALIZER_UNAVAILABLE,
        )
    return row


def validate_static_combination(
    *,
    service: str,
    pack_ref: str,
    materializer_ref: str,
    environment: Mapping[str, str],
) -> StaticHostRow:
    """Validate a full static row: pack + materializer + credential isolation.

    ``environment`` is the service's rendered environment mapping. Validation
    fails closed on:

    - a wrong pack or materializer for the service,
    - a missing generation marker for the selected runtime,
    - a present generation marker for another runtime,
    - any ambient API-key / cross-runtime credential selector,
    - any unapproved host-control variable.
    """

    row = static_host_row(service)
    validate_static_pack_selection(service=service, pack_ref=pack_ref)
    validate_static_materializer_selection(
        service=service, materializer_ref=materializer_ref
    )
    generation = str(environment.get(row.generation_env) or "").strip()
    if not generation:
        raise HarnessPlatformError(
            f"static host {service} requires {row.generation_env}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
        )
    other_generations = (
        {"CODEX_CREDENTIAL_GENERATION", "CLAUDE_CREDENTIAL_GENERATION",
         "OPENCODE_CREDENTIAL_GENERATION"} - {row.generation_env}
    )
    for key in sorted(other_generations):
        if key in environment:
            raise HarnessPlatformError(
                f"static host {service} must not carry {key}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
            )
    for key in FORBIDDEN_STATIC_AMBIENT_KEYS:
        if key in environment:
            raise HarnessPlatformError(
                f"static host {service} must not carry ambient {key}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
            )
    for key in FORBIDDEN_STATIC_CONTROL_KEYS:
        if key in environment:
            raise HarnessPlatformError(
                f"static host {service} must not accept control variable {key}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
    return row


def resolve_static_host_image_ref(
    environment: Mapping[str, str] | None = None,
) -> str:
    """Resolve the digest-pinned shared image ref for static rows.

    Primary authority is ``OMNIGENT_SHARED_HOST_IMAGE_REF``. The legacy
    ``OMNIGENT_HOST_IMAGE_REF`` is honored only as a bounded alias when the
    shared ref is unset, and must itself be digest-pinned. Legacy mutable
    ``OMNIGENT_HOST_IMAGE`` / ``OMNIGENT_HOST_IMAGE_TAG`` construction is not
    honored here: static rows fail closed instead of launching a mutable tag.
    """

    source: Mapping[str, str] = os.environ if environment is None else environment
    shared = str(source.get(SHARED_HOST_IMAGE_ENV) or "").strip()
    if shared:
        if not _DIGEST_PINNED_RE.fullmatch(shared):
            raise HarnessPlatformError(
                f"{SHARED_HOST_IMAGE_ENV} must be a digest-pinned image ref",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
        return shared
    legacy = str(source.get(LEGACY_HOST_IMAGE_ENV) or "").strip()
    if legacy:
        if not _DIGEST_PINNED_RE.fullmatch(legacy):
            raise HarnessPlatformError(
                f"{LEGACY_HOST_IMAGE_ENV} must be a digest-pinned image ref",
                code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
            )
        return legacy
    raise HarnessPlatformError(
        f"{SHARED_HOST_IMAGE_ENV} (or bounded alias {LEGACY_HOST_IMAGE_ENV}) "
        "must be set to a digest-pinned image ref",
        code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
    )


def static_host_authority_notes() -> dict[str, str]:
    """Name the durable owners for static-row authority handoffs.

    Compose process existence is not host authority. A static-connected
    service still participates in Provider Profile capacity, host binding and
    host lease, credential-generation fencing, exact registered host identity,
    one-session limits, canonical session/turn ownership, and cleanup/drain
    ordering. The owners below are the existing modules that already enforce
    those rules for on-demand hosts; static rows reuse them unchanged.
    """

    return {
        "provider_profile_capacity": (
            "moonmind.omnigent.harness_platform.capabilities"
        ),
        "host_binding_and_lease": "moonmind.omnigent.host_leases",
        "generation_fencing": (
            "moonmind.omnigent.harness_platform.credential_bindings"
        ),
        "exact_host_attestation": (
            "moonmind.omnigent.harness_platform.attestation and "
            "moonmind.omnigent.host_services.attestation"
        ),
        "session_and_turn_ownership": (
            "moonmind.omnigent canonical session/turn control plane"
        ),
        "cleanup_and_drain": (
            "moonmind.omnigent.host_services.cleanup and "
            "moonmind.omnigent.host_services.registration"
        ),
    }


__all__ = [
    "STATIC_CLAUDE_MATERIALIZER_REF",
    "STATIC_CLAUDE_PACK_REF",
    "STATIC_CLAUDE_PROFILE",
    "STATIC_CLAUDE_SERVICE",
    "STATIC_CODEX_MATERIALIZER_REF",
    "STATIC_CODEX_PACK_REF",
    "STATIC_CODEX_PROFILE",
    "STATIC_CODEX_SERVICE",
    "STATIC_HOST_ROWS",
    "StaticHostRow",
    "FORBIDDEN_STATIC_AMBIENT_KEYS",
    "FORBIDDEN_STATIC_CONTROL_KEYS",
    "GENERIC_STATIC_ENTRYPOINT",
    "GENERIC_STATIC_HEALTHCHECK",
    "resolve_static_host_image_ref",
    "static_host_authority_notes",
    "static_host_row",
    "validate_static_combination",
    "validate_static_materializer_selection",
    "validate_static_pack_selection",
]
