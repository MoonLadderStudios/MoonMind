"""Static-connected Codex/Claude host consolidation (MoonLadderStudios/MoonMind#3834).

Optional Compose profiles expose two static-connected Omnigent host
services — ``omnigent-host-codex`` and ``omnigent-host-claude`` — on the one
shared ``omnigent-host-moonmind`` image with one generic startup
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
import hashlib
import json
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
GENERIC_STATIC_LEASE_PURPOSE = "execution_generic_static"

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
SHARED_HOST_IMAGE_NAME_ENV = "OMNIGENT_SHARED_HOST_IMAGE"
SHARED_HOST_IMAGE_TAG_ENV = "OMNIGENT_SHARED_HOST_IMAGE_TAG"

# Explicit disposition of the static Claude Compose profile
# (MoonLadderStudios/MoonMind#3936).
#
# ``retained``: the optional static Claude path stays available for existing
# deployments, but the primary Claude destination is the on-demand generic
# host. This disposition changes no admitted host mode: exact pack /
# materializer selection is still enforced by
# :func:`validate_static_combination`, and new admission follows the
# code-owned retirement row ``omnigent.legacy.claude_static_host_startup`` in
# ``moonmind.omnigent.legacy_retirement`` (still ``ACTIVE_PRODUCT_PATH`` while
# retained). Removing the profile deletes its scripts, Compose service, and
# that row together once the generic Claude static row covers the existing
# deployments (``services/omnigent/scripts/STATIC_HOST_STARTUP_INVENTORY.md``).
STATIC_CLAUDE_DISPOSITION = "retained"
STATIC_CLAUDE_RETIREMENT_PATH_ID = "omnigent.legacy.claude_static_host_startup"
STATIC_CLAUDE_PRIMARY_PATH = "on-demand generic Claude host"

_DIGEST_PINNED_RE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_IMAGE_COORDINATE_RE = re.compile(
    r"(?:[A-Za-z0-9][A-Za-z0-9._-]*(?::[0-9]+)?/)?"
    r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9._-]*)?"
)

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


def static_host_row_for_harness(harness: str) -> StaticHostRow:
    from moonmind.omnigent.harness_platform.runtime_packs import get_runtime_pack

    matches = [
        row
        for row in STATIC_HOST_ROWS
        if get_runtime_pack(row.runtime_pack_ref).supports_harness(harness)
    ]
    if len(matches) != 1:
        raise HarnessPlatformError(
            f"no unique static host service for {harness!r}",
            code=HarnessPlatformFailure.OMNIGENT_HOST_CLASS_UNAVAILABLE,
        )
    return matches[0]


def validate_static_host_selection(
    value: Mapping[str, object], *, effective_launch: Mapping[str, object]
) -> dict[str, object]:
    """Validate the configured static scope inside immutable launch authority."""
    expected_fields = {
        "bindingRef",
        "providerProfileId",
        "endpointRef",
        "harness",
        "staticHostId",
    }
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise HarnessPlatformError(
            "static host selection has an invalid shape",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
        )
    for key, item in value.items():
        if key == "staticHostId" and item is None:
            continue
        if (
            not isinstance(item, str)
            or not item
            or len(item) > 255
            or any(character in item for character in "\0\r\n")
        ):
            raise HarnessPlatformError(
                f"invalid static host selection {key}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
    for key in ("providerProfileId", "endpointRef", "harness"):
        if value[key] != effective_launch.get(key):
            raise HarnessPlatformError(
                f"static host selection conflicts with admitted {key}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
    static_host_row_for_harness(str(value["harness"]))
    return dict(value)


def static_host_scope_ref(
    *, harness: str, endpoint_ref: str, host_id: str | None
) -> str:
    """Name one physical static host independently of its Provider Profile."""
    # Both explicit and discovered IDs address this one configured Compose
    # role. An ID spelling must not create another reservation for its mount.
    identity = ["static-compose", static_host_row_for_harness(harness).service]
    digest = hashlib.sha256(
        json.dumps(identity, separators=(",", ":")).encode()
    ).hexdigest()
    return f"omnigent-static-scope:sha256:{digest}"


async def bind_static_host_selection(
    *, repository, effective_launch: Mapping[str, object], commit_binding: bool = True
) -> dict[str, object]:
    """Freeze a static choice through the existing host-binding owner.

    Credential generations and mounts are acquired later. The compact
    configured scope stays in the signed launch artifact, so queued work
    cannot silently follow an operator's later host selection.
    """
    if effective_launch.get("hostMode") not in {"static-connected", "static_compose"}:
        return dict(effective_launch)
    from moonmind.omnigent.execution_profiles import (
        seal_effective_launch_snapshot,
        validate_effective_launch_snapshot,
    )

    validate_effective_launch_snapshot(effective_launch)
    static_host_row_for_harness(str(effective_launch["harness"]))
    profile_id = str(effective_launch["providerProfileId"])
    binding = await repository.get_binding_for_profile(profile_id)
    if binding is None:
        binding = await repository.create_or_update_static_binding(
            profile_id=profile_id,
            endpoint_ref=str(effective_launch["endpointRef"]),
            execution_profile_ref=str(effective_launch["executionProfileRef"]),
            launch_policy_ref=str(effective_launch["launchPolicyRef"]),
            effective_launch_snapshot=dict(effective_launch),
            require_idle=True,
            commit=commit_binding,
        )
    if binding.host_launch_profile_ref or (
        binding.launch_policy_ref
        and binding.launch_policy_ref != effective_launch["launchPolicyRef"]
    ):
        raise HarnessPlatformError(
            "static launch conflicts with the selected host binding",
            code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
        )
    selection = validate_static_host_selection(
        {
            "bindingRef": binding.binding_ref,
            "providerProfileId": binding.provider_profile_id,
            "endpointRef": binding.endpoint_ref,
            "harness": binding.harness,
            "staticHostId": binding.static_host_id,
        },
        effective_launch=effective_launch,
    )
    return seal_effective_launch_snapshot(
        {
            **effective_launch,
            "staticHostSelection": selection,
        }
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
        if str(environment.get(key) or "").strip():
            raise HarnessPlatformError(
                f"static host {service} must not carry {key}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
            )
    for key in FORBIDDEN_STATIC_AMBIENT_KEYS:
        if str(environment.get(key) or "").strip():
            raise HarnessPlatformError(
                f"static host {service} must not carry ambient {key}",
                code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
            )
    for key in FORBIDDEN_STATIC_CONTROL_KEYS:
        if str(environment.get(key) or "").strip():
            raise HarnessPlatformError(
                f"static host {service} must not accept control variable {key}",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
    return row


def resolve_static_host_image_ref(
    environment: Mapping[str, str] | None = None,
) -> str:
    """Resolve the selected static image without requiring an operator pin."""

    return resolve_effective_static_host_image(environment)


def static_claude_disposition() -> dict[str, str]:
    """Return the explicit qualify-or-retire disposition for static Claude.

    This is the recorded answer to issue #3936 item 1: the static Claude
    Compose profile (``omnigent-host-claude``) is ``retained`` — an optional
    path for existing deployments — while the on-demand generic Claude host
    is the primary destination. Static and on-demand modes are never
    substituted inside an admitted plan; the exact pack/materializer match
    enforced by :func:`validate_static_combination` fails closed instead.
    """

    return {
        "disposition": STATIC_CLAUDE_DISPOSITION,
        "retirement_path_id": STATIC_CLAUDE_RETIREMENT_PATH_ID,
        "retirement_class": "active_product_path",
        "new_admission_source": "docker-compose.yaml:omnigent-host-claude",
        "primary_path": STATIC_CLAUDE_PRIMARY_PATH,
        "admission_authority": (
            "moonmind.omnigent.legacy_retirement:RETIREMENT_INVENTORY"
        ),
        "removal_conditions": (
            "generic Claude static row serves the existing deployments; the "
            "scripts, Compose service, and retirement row are deleted together"
        ),
    }


def _expand_compose_default(value: str, environment: Mapping[str, str]) -> str:
    """Expand ``${VAR:-default}`` / ``${VAR}`` / ``$VAR`` Compose expressions.

    This models the effective operator-visible value Compose computes for a
    static service field from the operator environment: an unset or empty
    ``VAR`` selects ``default`` for the ``:-`` form. Nested defaults (as in
    the shared-image anchor expression) expand inside out via balanced-brace
    scanning.
    """

    name_re = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    result: list[str] = []
    i, n = 0, len(value)
    while i < n:
        if (
            value[i] == "$"
            and i + 1 < n
            and (value[i + 1] == "{" or name_re.match(value[i + 1]) is not None)
        ):
            if value[i + 1] == "{":
                depth, j = 1, i + 2
                while j < n and depth:
                    if value[j] == "}":
                        depth -= 1
                    elif value[j] == "{":
                        depth += 1
                    j += 1
                if depth:
                    result.append(value[i])
                    i += 1
                    continue
                inner = value[i + 2 : j - 1]
                name, sep, default = inner.partition(":-")
                name = name.strip()
                if name_re.fullmatch(name) is None:
                    result.append(value[i:j])
                else:
                    raw = str(environment.get(name) or "")
                    if (not raw) and sep:
                        result.append(
                            _expand_compose_default(default, environment)
                        )
                    else:
                        result.append(raw)
                i = j
            else:
                match = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)", value[i:])
                assert match is not None
                result.append(str(environment.get(match.group(1)) or ""))
                i += match.end()
        else:
            result.append(value[i])
            i += 1
    return "".join(result)


def render_static_service_env(
    raw_service_env: Mapping[str, str],
    operator_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Render a static service's effective environment from raw Compose values.

    ``raw_service_env`` is the service's ``environment`` mapping as parsed
    from ``docker-compose.yaml`` (interpolation expressions included);
    ``operator_env`` is the operator shell environment. The result is the
    effective value set a container would receive — the input that
    :func:`validate_static_combination` must judge, not the raw expression.
    """

    source: Mapping[str, str] = {} if operator_env is None else operator_env
    return {
        str(key): _expand_compose_default(str(value), source)
        for key, value in raw_service_env.items()
    }


def classify_effective_static_host_image(
    environment: Mapping[str, str] | None = None,
) -> tuple[str | None, bool, str]:
    """Resolve Compose's image selection; provenance is recorded at launch.

    Explicit mappings are effective launch inputs and are never rewritten by
    later installed state. Without a mapping, reuse the installed-image owner
    so a long-lived process does not keep selecting its startup environment.
    Tags and the ordinary latest default are valid selection inputs. This
    does not qualify a host, admit a session, or weaken artifact attestation.
    """

    if environment is None:
        from moonmind.omnigent.bootstrap.store import with_installed_image_refs

        source = with_installed_image_refs()
    else:
        source = environment
    shared = str(source.get(SHARED_HOST_IMAGE_ENV) or "").strip()
    legacy = str(source.get(LEGACY_HOST_IMAGE_ENV) or "").strip()
    if shared:
        image, reason = shared, SHARED_HOST_IMAGE_ENV
    elif legacy:
        image, reason = legacy, f"{LEGACY_HOST_IMAGE_ENV} bounded-alias"
    else:
        name = str(source.get(SHARED_HOST_IMAGE_NAME_ENV) or "").strip()
        tag = str(source.get(SHARED_HOST_IMAGE_TAG_ENV) or "").strip()
        image = (
            f"{name or 'ghcr.io/moonladderstudios/omnigent-host-moonmind'}:"
            f"{tag or 'latest'}"
        )
        reason = "shared image/tag or latest default"
    # Check the repository and optional tag before the digest separately:
    # an explicit empty tag must not be normalized into an omitted/latest tag.
    coordinate = image.partition("@")[0]
    if not _IMAGE_COORDINATE_RE.fullmatch(coordinate) or (
        "@" in image and not _DIGEST_PINNED_RE.fullmatch(image)
    ):
        return None, False, f"invalid static host image reference from {reason}"
    return image, True, reason


def resolve_effective_static_host_image(
    environment: Mapping[str, str] | None = None,
) -> str:
    """Resolve the effective static image, rejecting malformed references."""

    image_ref, supported, reason = classify_effective_static_host_image(environment)
    if supported and image_ref is not None:
        return image_ref
    raise HarnessPlatformError(
        reason,
        code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH,
    )


def admit_static_host_compose_launch(
    *,
    service: str,
    pack_ref: str,
    materializer_ref: str,
    operator_env: Mapping[str, str],
    raw_service_env: Mapping[str, str],
) -> dict[str, object]:
    """Admit one static Compose service at the prelaunch boundary.

    This is the trusted Python admission boundary for the Compose launch
    path (MoonLadderStudios/MoonMind#3936 R3): it judges the service's
    *rendered effective* environment — the values a container would
    receive — through :func:`validate_static_combination`, and resolves
    the *effective* launch image through
    :func:`resolve_effective_static_host_image`, which preserves the selected image or ordinary latest default. A helper-level
    classification alone is not launch authority; callers must pass
    through this boundary before ``docker compose up`` admits the service.

    ``operator_env`` is the operator shell environment;
    ``raw_service_env`` is the service's ``environment`` mapping as parsed
    from ``docker-compose.yaml`` (interpolation expressions included).
    """

    rendered = render_static_service_env(raw_service_env, operator_env)
    return admit_static_host_effective_launch(
        service=service,
        pack_ref=pack_ref,
        materializer_ref=materializer_ref,
        rendered_env=rendered,
        operator_env=operator_env,
    )


def admit_static_host_effective_launch(
    *,
    service: str,
    pack_ref: str,
    materializer_ref: str,
    rendered_env: Mapping[str, str],
    operator_env: Mapping[str, str],
) -> dict[str, object]:
    """Admit one static Compose service from its rendered effective inputs.

    This is the same trusted admission as
    :func:`admit_static_host_compose_launch` for callers that already hold
    the rendered environment a container would receive (the managed launch
    path ``OmnigentOAuthHostRuntime._compose_static_check`` in
    ``moonmind/omnigent/oauth_host_runtime.py`` calls this immediately
    before ``docker compose up``): the rendered mapping is judged through
    :func:`validate_static_combination` and the effective launch image is
    resolved through :func:`resolve_effective_static_host_image`, which
    rejects malformed references without requiring manual digest pins.
    """

    rendered = dict(rendered_env)
    row = validate_static_combination(
        service=service,
        pack_ref=pack_ref,
        materializer_ref=materializer_ref,
        environment=rendered,
    )
    image_ref = resolve_effective_static_host_image(operator_env)
    return {
        "service": row.service,
        "compose_profile": row.compose_profile,
        "host_class_ref": row.host_class_ref,
        "image_ref": image_ref,
        "rendered_env": rendered,
    }


# --- R5: workflow-side bounded static enrollment -----------------------------
#
# The script-level readiness loops bound the *operator waiting state* with
# distinct nonzero exits. A workflow must additionally bound its own
# admission/retry wait and must never treat process existence as admitted
# capacity. The helpers below are pure functions of explicit inputs (no
# clock reads, no environment reads) so Temporal workflow and Activity
# callers stay deterministic: the caller supplies the elapsed wait and the
# observed readiness probes, and the classifier returns the waiting /
# ready / failed / unqualified separation. The stream-admission deadline
# in ``moonmind.omnigent.execute`` is unrelated (first-message stream
# admission) and must not be used as static-enrollment evidence.

#: Distinct static-enrollment outcomes. Only ``ready`` is admitted usable
#: capacity; ``waiting-*`` keeps the operator wait distinguishable from a
#: decision; ``failed-*`` spends this attempt's budget (a new admission
#: attempt may start only after operator action); ``unqualified`` fails
#: closed without consuming the enrollment budget.
STATIC_ENROLLMENT_READY = "ready"
STATIC_ENROLLMENT_WAITING_FOR_ENROLLMENT = "waiting-for-enrollment"
STATIC_ENROLLMENT_WAITING_FOR_PROJECTION = "waiting-for-projection"
STATIC_ENROLLMENT_FAILED_ENROLLMENT_TIMEOUT = "failed-enrollment-timeout"
STATIC_ENROLLMENT_FAILED_PROJECTION_TIMEOUT = "failed-projection-timeout"
STATIC_ENROLLMENT_UNQUALIFIED = "unqualified"


#: Operator-wait bounds shared by the packaged entrypoint and workflow-side
#: admission. The entrypoint reads the same names with the same defaults;
#: a workflow caller must bound its own wait with identical values so the
#: two sides never disagree about how long enrollment may wait.
STATIC_CREDENTIAL_TIMEOUT_ENV = (
    "MOONMIND_OMNIGENT_STATIC_CREDENTIAL_TIMEOUT_SECONDS"
)
STATIC_SKILL_TIMEOUT_ENV = "MOONMIND_OMNIGENT_STATIC_SKILL_TIMEOUT_SECONDS"
STATIC_DEFAULT_CREDENTIAL_TIMEOUT_SECONDS = 1800
STATIC_DEFAULT_SKILL_TIMEOUT_SECONDS = 600


def static_enrollment_timeouts_from_env(
    environment: Mapping[str, str] | None = None,
) -> tuple[int, int]:
    """Read the bounded static-enrollment waits with entrypoint parity.

    Returns ``(credential_timeout_seconds, skill_timeout_seconds)`` using
    the same variable names and defaults as
    ``services/omnigent/scripts/start-omnigent-host.sh``. Non-integer or
    non-positive values fail closed instead of waiting indefinitely.
    """

    source: Mapping[str, str] = os.environ if environment is None else environment
    timeouts: list[int] = []
    for key, default in (
        (STATIC_CREDENTIAL_TIMEOUT_ENV, STATIC_DEFAULT_CREDENTIAL_TIMEOUT_SECONDS),
        (STATIC_SKILL_TIMEOUT_ENV, STATIC_DEFAULT_SKILL_TIMEOUT_SECONDS),
    ):
        raw = str(source.get(key) or "").strip()
        if not raw:
            timeouts.append(default)
            continue
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value <= 0:
            raise HarnessPlatformError(
                f"static enrollment {key} must be a positive integer",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
        timeouts.append(value)
    return (timeouts[0], timeouts[1])


def classify_static_enrollment_status(
    *,
    credential_ready: bool,
    projection_ready: bool,
    generation_fenced_ok: bool = True,
    elapsed_seconds: float = 0.0,
    credential_timeout_seconds: int = 1800,
    skill_timeout_seconds: int = 600,
) -> dict[str, object]:
    """Classify one static-enrollment observation for workflow admission.

    ``credential_ready`` mirrors the ``check-omnigent-host.sh`` gate
    (authenticated credentials enrolled and generation-fenced);
    ``projection_ready`` mirrors the ``check-runner-projections.sh`` gate
    (resolved Skill projection present). ``elapsed_seconds`` is this
    admission attempt's waited time; the attempt fails with a distinct
    ``failed-*`` outcome once its deadline passes instead of waiting
    indefinitely. A stale or mismatched generation (``generation_fenced_ok``
    false) is ``unqualified``: fail-closed, never admitted, never retried
    as the same attempt.
    """

    for label, value in (
        ("credential_timeout_seconds", credential_timeout_seconds),
        ("skill_timeout_seconds", skill_timeout_seconds),
    ):
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value <= 0
        ):
            raise HarnessPlatformError(
                f"static enrollment {label} must be a positive integer",
                code=HarnessPlatformFailure.OMNIGENT_EXECUTION_PLAN_CONFLICT,
            )
    if not generation_fenced_ok:
        return {
            "status": STATIC_ENROLLMENT_UNQUALIFIED,
            "admitted": False,
            "retryable": False,
            "reason": (
                "stale or mismatched credential generation: fail closed, "
                "never admitted usable capacity"
            ),
        }
    if not credential_ready:
        if elapsed_seconds >= credential_timeout_seconds:
            return {
                "status": STATIC_ENROLLMENT_FAILED_ENROLLMENT_TIMEOUT,
                "admitted": False,
                "retryable": False,
                "reason": (
                    "waiting-for-enrollment deadline exceeded: authenticated "
                    "credentials not ready (operator enrollment pending, "
                    "not admitted capacity)"
                ),
            }
        return {
            "status": STATIC_ENROLLMENT_WAITING_FOR_ENROLLMENT,
            "admitted": False,
            "retryable": True,
            "reason": (
                "operator enrollment pending: waiting state, "
                "not admitted capacity"
            ),
        }
    if not projection_ready:
        if elapsed_seconds >= skill_timeout_seconds:
            return {
                "status": STATIC_ENROLLMENT_FAILED_PROJECTION_TIMEOUT,
                "admitted": False,
                "retryable": False,
                "reason": (
                    "Skill-projection deadline exceeded: no resolved Skill "
                    "projection (missing projection, not admitted capacity)"
                ),
            }
        return {
            "status": STATIC_ENROLLMENT_WAITING_FOR_PROJECTION,
            "admitted": False,
            "retryable": True,
            "reason": (
                "resolved Skill projection missing: waiting state, "
                "not admitted capacity"
            ),
        }
    return {
        "status": STATIC_ENROLLMENT_READY,
        "admitted": True,
        "retryable": False,
        "reason": "credential and Skill-projection gates both report ready",
    }


# --- R4: staged-marker vs live-lease credential ownership --------------------
#
# ``start-omnigent-host.sh`` stages the supplied generation marker and
# verifies the write by reading it back. That read-back proves staging
# integrity only; admission authority stays with the Provider Profile
# lease/generation, host binding/lease, and attestation owners named in
# :func:`static_host_authority_notes`. The helpers below fence the staged
# value against the live lease at the admission boundary instead of
# trusting the marker alone. Lease acquisition, binding, attestation,
# session/turn ownership, and cleanup ordering themselves stay owned by
# the existing planner, lease, attestation, session, and cleanup modules
# (referenced, not reimplemented, here).

#: Execution modes across which one credential generation may rotate.
STATIC_CREDENTIAL_EXECUTION_MODES: tuple[str, ...] = (
    "static",
    "direct",
    "on-demand",
)


def verify_staged_generation_against_lease(
    *,
    staged_generation: str,
    lease_generation: str,
    provider_profile_id: str,
    registered_host_id: str,
) -> dict[str, str]:
    """Fence a staged generation marker against the live Provider lease.

    Both values must be present and equal; a missing or mismatched marker
    fails closed. The staged file is staging evidence only — this check
    is what binds it to current lease authority before admission.
    """

    staged = str(staged_generation or "").strip()
    live = str(lease_generation or "").strip()
    profile = str(provider_profile_id or "").strip()
    host = str(registered_host_id or "").strip()
    if not staged or not live:
        raise HarnessPlatformError(
            "staged credential generation is not lease-verified: "
            "marker or live lease generation is missing",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
        )
    if not profile or not host:
        raise HarnessPlatformError(
            "staged credential generation is not lease-verified: "
            "provider profile or registered host identity is missing",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
        )
    if staged != live:
        raise HarnessPlatformError(
            "staged credential generation does not match the live Provider "
            f"Profile lease generation for {profile} on {host}",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
        )
    return {
        "provider_profile_id": profile,
        "registered_host_id": host,
        "verified_generation": staged,
    }


def trace_static_credential_ownership(
    *,
    provider_profile_id: str,
    provider_lease_ref: str,
    credential_generation: str,
    registered_host_id: str,
    active_session_count: int,
    execution_modes_observed: tuple[str, ...] | list[str],
) -> dict[str, object]:
    """Trace one static credential-ownership claim to its lease authority.

    Binds the selected Provider Profile to its current lease ref and
    generation, to the exact registered host, and to one-session/lease
    exclusivity: more than one active session on the same lease fails
    closed instead of sharing the lease. ``execution_modes_observed``
    records which of the ``static`` / ``direct`` / ``on-demand`` modes
    have consumed this generation, so rotation across modes stays
    evidenced against the same lease rather than assumed from the
    staged marker.
    """

    profile = str(provider_profile_id or "").strip()
    lease_ref = str(provider_lease_ref or "").strip()
    generation = str(credential_generation or "").strip()
    host = str(registered_host_id or "").strip()
    modes = [str(mode).strip() for mode in (execution_modes_observed or [])]
    if not profile or not lease_ref or not generation or not host:
        raise HarnessPlatformError(
            "static credential ownership is incomplete: provider profile, "
            "lease ref, generation, and registered host are all required",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_GENERATION_FENCED,
        )
    unknown_modes = [mode for mode in modes if mode not in STATIC_CREDENTIAL_EXECUTION_MODES]
    if unknown_modes:
        raise HarnessPlatformError(
            "static credential ownership names an unknown execution mode: "
            + ", ".join(sorted(set(unknown_modes))),
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
        )
    if (
        not isinstance(active_session_count, int)
        or isinstance(active_session_count, bool)
        or active_session_count < 0
    ):
        raise HarnessPlatformError(
            "static credential ownership requires a non-negative active "
            "session count",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
        )
    if active_session_count > 1:
        raise HarnessPlatformError(
            f"static credential lease {lease_ref} is not exclusive: "
            f"{active_session_count} active sessions",
            code=HarnessPlatformFailure.OMNIGENT_CREDENTIAL_BINDING_SET_CONFLICT,
        )
    return {
        "provider_profile_id": profile,
        "provider_lease_ref": lease_ref,
        "verified_generation": generation,
        "registered_host_id": host,
        # active_session_count > 1 raises above, so exclusivity holds here.
        "exclusive": True,
        "execution_modes_observed": tuple(modes),
    }


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
    "SHARED_HOST_IMAGE_ENV",
    "SHARED_HOST_IMAGE_NAME_ENV",
    "SHARED_HOST_IMAGE_TAG_ENV",
    "STATIC_CLAUDE_DISPOSITION",
    "STATIC_CLAUDE_PRIMARY_PATH",
    "STATIC_CLAUDE_RETIREMENT_PATH_ID",
    "STATIC_CLAUDE_MATERIALIZER_REF",
    "STATIC_CLAUDE_PACK_REF",
    "STATIC_CLAUDE_PROFILE",
    "STATIC_CLAUDE_SERVICE",
    "STATIC_CODEX_MATERIALIZER_REF",
    "STATIC_CODEX_PACK_REF",
    "STATIC_CODEX_PROFILE",
    "STATIC_CODEX_SERVICE",
    "STATIC_CREDENTIAL_EXECUTION_MODES",
    "STATIC_CREDENTIAL_TIMEOUT_ENV",
    "STATIC_DEFAULT_CREDENTIAL_TIMEOUT_SECONDS",
    "STATIC_DEFAULT_SKILL_TIMEOUT_SECONDS",
    "STATIC_ENROLLMENT_FAILED_ENROLLMENT_TIMEOUT",
    "STATIC_ENROLLMENT_FAILED_PROJECTION_TIMEOUT",
    "STATIC_ENROLLMENT_READY",
    "STATIC_ENROLLMENT_UNQUALIFIED",
    "STATIC_ENROLLMENT_WAITING_FOR_ENROLLMENT",
    "STATIC_ENROLLMENT_WAITING_FOR_PROJECTION",
    "STATIC_SKILL_TIMEOUT_ENV",
    "STATIC_HOST_ROWS",
    "StaticHostRow",
    "FORBIDDEN_STATIC_AMBIENT_KEYS",
    "FORBIDDEN_STATIC_CONTROL_KEYS",
    "GENERIC_STATIC_ENTRYPOINT",
    "GENERIC_STATIC_HEALTHCHECK",
    "classify_effective_static_host_image",
    "classify_static_enrollment_status",
    "admit_static_host_compose_launch",
    "admit_static_host_effective_launch",
    "resolve_effective_static_host_image",
    "render_static_service_env",
    "resolve_static_host_image_ref",
    "static_claude_disposition",
    "static_enrollment_timeouts_from_env",
    "static_host_authority_notes",
    "static_host_row",
    "trace_static_credential_ownership",
    "validate_static_combination",
    "validate_static_materializer_selection",
    "validate_static_pack_selection",
    "verify_staged_generation_against_lease",
]
