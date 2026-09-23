"""Static Claude qualify-or-retire evidence (MoonLadderStudios/MoonMind#3936).

The shared static template baseline (R1) is covered by
``test_static_host_consolidation.py``. This module closes the remaining
qualify-or-retire items for the static Claude path against effective inputs:

- R2: the retained disposition is recorded in code and matches the
  code-owned retirement row (no silent host-mode change).
- R3: the effective launch image is classified for every operator
  configuration (shared digest, missing ref, mutable tag, legacy-only
  digest, conflicting settings) through the trusted boundary
  ``resolve_effective_static_host_image``, not raw-YAML equality; the
  Compose prelaunch admission boundary
  ``admit_static_host_compose_launch`` (and its rendered-env equivalent
  ``admit_static_host_effective_launch``) and the bootstrap persistence gate
  ``require_static_host_image_authority`` enforce it at their callers.
  The managed launch path admits through
  ``OmnigentOAuthHostRuntime._compose_static_check`` immediately before
  ``docker compose up``.
- R4: credential generation fencing is verified through rendered effective
  environments plus the packaged startup read-back check; the staged marker
  is fenced against the live lease via
  ``verify_staged_generation_against_lease`` and the profile/lease/host/
  exclusivity/rotation chain via ``trace_static_credential_ownership``.
- R5: readiness waits are bounded with distinct waiting vs failed outcomes
  at the script level and through the deterministic workflow-side
  ``classify_static_enrollment_status`` (the stream-admission deadline in
  ``execute.py`` is unrelated and is not enrollment evidence). The
  production caller is ``OmnigentOAuthHostRuntime.prepare_host`` via
  ``_classify_static_enrollment`` / ``_require_static_enrollment_admitted``
  with entrypoint-parity timeouts from
  ``static_enrollment_timeouts_from_env``.
- R6: isolation is verified through rendered effective inputs, effective
  mounts, and executed packaged-script fragments (GH_TOKEN write/rotate/
  preserve/reject, generation fencing, ambient/cross-runtime rejection,
  credential-home layout gate).
- R7: lifecycle/drain ownership is pinned to the retirement row, the
  authority owners, and the host-class mapping.

A full Profile -> static-host binding/attestation -> canonical
session/turn -> terminal evidence -> cleanup journey against an exact-image
Docker host and live-provider evidence remains out of unit scope and is
named explicitly in ``test_static_claude_drain_ownership`` instead of being
inferred from these hermetic checks.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from moonmind.omnigent.harness_platform import static_hosts
from moonmind.omnigent.bootstrap import image_resolution
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.host_classes import (
    DEFAULT_HOST_CLASS_TEMPLATES,
)
from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
from moonmind.omnigent.oauth_hosts import HostPreflightFailure
from moonmind.schemas.agent_runtime_models import (
    AuthVolumeRef,
    CredentialMountRef,
    OmnigentHostLease,
    OmnigentOAuthHostBinding,
)
from moonmind.omnigent.legacy_retirement import (
    RETIREMENT_INVENTORY,
    RemovalStage,
    RetirementClass,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "services" / "omnigent" / "scripts"
FAKE_DIGEST = (
    "ghcr.io/moonladderstudios/omnigent-host-moonmind" "@sha256:" + "a1" * 32
)
LEGACY_DIGEST = "ghcr.io/omnigent-ai/omnigent-host" + "@sha256:" + "b2" * 32


def _load_compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text())


def _raw_env_map(service: dict) -> dict[str, str]:
    environment = service.get("environment", {})
    if isinstance(environment, dict):
        return {str(k): str(v) for k, v in environment.items()}
    mapped: dict[str, str] = {}
    for item in environment:
        text = str(item)
        if "=" in text:
            key, value = text.split("=", 1)
            mapped[key] = value
    return mapped


def _startup_script() -> str:
    return (SCRIPTS / "start-omnigent-host.sh").read_text()


def _claude_retirement_row():
    for row in RETIREMENT_INVENTORY:
        if row.path_id == static_hosts.STATIC_CLAUDE_RETIREMENT_PATH_ID:
            return row
    raise AssertionError("claude static retirement row is missing")


# ---------------------------------------------------------------- R2: disposition


def test_static_claude_disposition_is_retained_and_matches_retirement_row() -> None:
    disposition = static_hosts.static_claude_disposition()
    assert disposition["disposition"] == "retained"
    assert static_hosts.STATIC_CLAUDE_DISPOSITION == "retained"
    assert (
        disposition["retirement_path_id"]
        == "omnigent.legacy.claude_static_host_startup"
    )
    assert (
        disposition["new_admission_source"]
        == "docker-compose.yaml:omnigent-host-claude"
    )
    assert disposition["primary_path"] == static_hosts.STATIC_CLAUDE_PRIMARY_PATH
    assert "on-demand" in disposition["primary_path"]

    row = _claude_retirement_row()
    assert row.retirement_class is RetirementClass.ACTIVE_PRODUCT_PATH
    assert row.new_admission_source == disposition["new_admission_source"]
    # Retained still admits new work through the same named source: the
    # disposition records intent without silently changing an admitted mode.
    assert row.admits_new_work


def test_static_claude_disposition_never_substitutes_host_modes() -> None:
    compose = _load_compose()
    claude_env = static_hosts.render_static_service_env(
        _raw_env_map(compose["services"]["omnigent-host-claude"]), {}
    )
    # The exact pack/materializer match fails closed instead of becoming the
    # other runtime or the on-demand path.
    with pytest.raises(HarnessPlatformError):
        static_hosts.validate_static_combination(
            service="omnigent-host-claude",
            pack_ref="codex-native-pack@1",
            materializer_ref="claude-oauth-home@1",
            environment=claude_env,
        )


# ------------------------------------------------------- R3: effective image


def test_effective_launch_image_covers_all_configuration_cases() -> None:
    # Shared digest: the only supported operator configuration.
    ref, supported, _ = static_hosts.classify_effective_static_host_image(
        {"OMNIGENT_SHARED_HOST_IMAGE_REF": FAKE_DIGEST}
    )
    assert (ref, supported) == (FAKE_DIGEST, True)
    assert (
        static_hosts.resolve_effective_static_host_image(
            {"OMNIGENT_SHARED_HOST_IMAGE_REF": FAKE_DIGEST}
        )
        == FAKE_DIGEST
    )
    # Legacy-only digest: honored as the bounded alias.
    ref, supported, reason = static_hosts.classify_effective_static_host_image(
        {"OMNIGENT_HOST_IMAGE_REF": LEGACY_DIGEST}
    )
    assert (ref, supported) == (LEGACY_DIGEST, True)
    assert "bounded-alias" in reason
    # Conflicting settings: the shared ref always wins.
    assert (
        static_hosts.resolve_effective_static_host_image(
            {
                "OMNIGENT_SHARED_HOST_IMAGE_REF": FAKE_DIGEST,
                "OMNIGENT_HOST_IMAGE_REF": LEGACY_DIGEST,
            }
        )
        == FAKE_DIGEST
    )
    # Unset configuration: fail closed, never a mutable default launch.
    ref, supported, reason = static_hosts.classify_effective_static_host_image({})
    assert ref is None and supported is False
    with pytest.raises(HarnessPlatformError):
        static_hosts.resolve_effective_static_host_image({})
    # The bootstrap classifier and Compose must construct the same default tag.
    ref, supported, _ = static_hosts.classify_effective_static_host_image(
        {"OMNIGENT_SHARED_HOST_IMAGE": "ghcr.io/moonladderstudios/omnigent-host-moonmind"}
    )
    assert ref == "ghcr.io/moonladderstudios/omnigent-host-moonmind:latest"
    assert supported is False
    # Mutable tag construction: development/bootstrap input only.
    ref, supported, reason = static_hosts.classify_effective_static_host_image(
        {
            "OMNIGENT_SHARED_HOST_IMAGE": "ghcr.io/moonladderstudios/omnigent-host-moonmind",
            "OMNIGENT_SHARED_HOST_IMAGE_TAG": "1.18.11",
        }
    )
    assert ref is not None and supported is False
    assert "bootstrap" in reason
    with pytest.raises(HarnessPlatformError):
        static_hosts.resolve_effective_static_host_image(
            {"OMNIGENT_SHARED_HOST_IMAGE_TAG": "1.18.11"}
        )
    # Mutable pins are rejected on both ref paths, even when the other path
    # holds a valid digest (fail-closed precedence).
    with pytest.raises(HarnessPlatformError):
        static_hosts.resolve_effective_static_host_image(
            {"OMNIGENT_SHARED_HOST_IMAGE_REF": "some-image:latest"}
        )
    with pytest.raises(HarnessPlatformError):
        static_hosts.resolve_effective_static_host_image(
            {"OMNIGENT_HOST_IMAGE_REF": "some-image:latest"}
        )
    with pytest.raises(HarnessPlatformError):
        static_hosts.resolve_effective_static_host_image(
            {
                "OMNIGENT_SHARED_HOST_IMAGE_REF": "some-image:latest",
                "OMNIGENT_HOST_IMAGE_REF": LEGACY_DIGEST,
            }
        )


def test_rendered_compose_anchor_resolves_effective_image_per_case() -> None:
    compose = _load_compose()
    anchor_image = compose["x-omnigent-static-host"]["image"]
    assert "OMNIGENT_SHARED_HOST_IMAGE_REF" in anchor_image

    def _rendered(operator_env: dict[str, str]) -> str:
        return static_hosts.render_static_service_env(
            {"image": anchor_image}, operator_env
        )["image"]

    # Unset operator configuration renders the mutable dev default, which the
    # trusted boundary rejects as launch authority.
    rendered_default = _rendered({})
    assert rendered_default == (
        "ghcr.io/moonladderstudios/omnigent-host-moonmind:latest"
    )
    _, supported, _ = static_hosts.classify_effective_static_host_image({})
    assert supported is False
    # A digest-pinned shared ref renders through to supported authority.
    assert _rendered({"OMNIGENT_SHARED_HOST_IMAGE_REF": FAKE_DIGEST}) == (
        FAKE_DIGEST
    )
    assert (
        static_hosts.resolve_effective_static_host_image(
            {"OMNIGENT_SHARED_HOST_IMAGE_REF": FAKE_DIGEST}
        )
        == FAKE_DIGEST
    )
    # Both static services share the anchor expression, so the same
    # classification holds for each row.
    services = compose["services"]
    assert (
        services["omnigent-host-codex"]["image"]
        == services["omnigent-host-claude"]["image"]
        == anchor_image
    )
    # The anchor documents the effective contract and its trusted boundary.
    anchor_comment = (REPO_ROOT / "docker-compose.yaml").read_text()
    assert "resolve_effective_static_host_image" in anchor_comment
    assert "bootstrap" in anchor_comment


# --------------------------------- R4/R6: effective credential environment


def test_rendered_static_environments_pass_generation_fencing() -> None:
    compose = _load_compose()
    services = compose["services"]
    codex_env = static_hosts.render_static_service_env(
        _raw_env_map(services["omnigent-host-codex"]), {}
    )
    claude_env = static_hosts.render_static_service_env(
        _raw_env_map(services["omnigent-host-claude"]), {}
    )
    # Rendered defaults (not raw expressions) are the judged input.
    assert codex_env["CODEX_CREDENTIAL_GENERATION"] == "1"
    assert claude_env["CLAUDE_CREDENTIAL_GENERATION"] == "1"
    assert "${" not in codex_env["CODEX_CREDENTIAL_GENERATION"]
    assert "${" not in claude_env["CLAUDE_CREDENTIAL_GENERATION"]
    codex_row = static_hosts.validate_static_combination(
        service="omnigent-host-codex",
        pack_ref="codex-native-pack@1",
        materializer_ref="codex-oauth-home@1",
        environment=codex_env,
    )
    claude_row = static_hosts.validate_static_combination(
        service="omnigent-host-claude",
        pack_ref="claude-native-pack@1",
        materializer_ref="claude-oauth-home@1",
        environment=claude_env,
    )
    assert codex_row.generation_env == "CODEX_CREDENTIAL_GENERATION"
    assert claude_row.generation_env == "CLAUDE_CREDENTIAL_GENERATION"


def test_empty_selectors_fail_closed_on_rendered_inputs() -> None:
    compose = _load_compose()
    raw_claude = _raw_env_map(compose["services"]["omnigent-host-claude"])
    # Compose `:-` semantics: an explicitly empty operator value still selects
    # the declared default. The renderer must model that faithfully.
    empty_selects_default = static_hosts.render_static_service_env(
        raw_claude, {"CLAUDE_CREDENTIAL_GENERATION": ""}
    )
    assert empty_selects_default["CLAUDE_CREDENTIAL_GENERATION"] == "1"
    # An empty *rendered* generation is missing generation, not a default:
    # injected or materialized empty values fail closed.
    empty_rendered = static_hosts.render_static_service_env(raw_claude, {})
    empty_rendered["CLAUDE_CREDENTIAL_GENERATION"] = ""
    with pytest.raises(HarnessPlatformError):
        static_hosts.validate_static_combination(
            service="omnigent-host-claude",
            pack_ref="claude-native-pack@1",
            materializer_ref="claude-oauth-home@1",
            environment=empty_rendered,
        )
    # Empty ambient and cross-runtime selectors are treated as unset: the
    # canonical worker service defines credential keys (including as empty
    # strings) that are not part of the static service environment, so an
    # empty rendered value must not fail closed. Only a non-empty rendered
    # value is a credential-isolation violation.
    for poison in (
        {"ANTHROPIC_API_KEY": ""},
        {"CODEX_CREDENTIAL_GENERATION": ""},
        {"OPENCODE_CREDENTIAL_GENERATION": ""},
    ):
        rendered = static_hosts.render_static_service_env(raw_claude, {})
        rendered.update(poison)
        static_hosts.validate_static_combination(
            service="omnigent-host-claude",
            pack_ref="claude-native-pack@1",
            materializer_ref="claude-oauth-home@1",
            environment=rendered,
        )
    for poison in (
        {"ANTHROPIC_API_KEY": "secret"},
        {"CODEX_CREDENTIAL_GENERATION": "7"},
        {"OPENCODE_CREDENTIAL_GENERATION": "3"},
    ):
        rendered = static_hosts.render_static_service_env(raw_claude, {})
        rendered.update(poison)
        with pytest.raises(HarnessPlatformError):
            static_hosts.validate_static_combination(
                service="omnigent-host-claude",
                pack_ref="claude-native-pack@1",
                materializer_ref="claude-oauth-home@1",
                environment=rendered,
            )


def test_startup_stages_generation_with_read_back_verification() -> None:
    script = _startup_script()
    assert "credential generation staging verification failed" in script
    # The staged marker is namespaced as staging-only: admission authority
    # stays with the lease/attestation owners, never the marker alone.
    assert "static_host_authority_notes" in script
    assert "admission evidence by itself" in script


def _github_token_block() -> str:
    lines = _startup_script().splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("github_token=${GH_TOKEN:-}")
    )
    end = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("unset github_token")
    )
    return "\n".join(lines[start : end + 1]) + "\n"


def _require_writable_static_host_cache() -> None:
    """Skip when the static-host home layout is unavailable.

    The packaged entrypoint only admits a GitHub config home under
    ``/home/app/.cache/*``. Hermetic runners without that writable layout
    (for example GitHub-hosted CI) cannot execute the token-staging block
    end to end, so these tests skip there with an explicit reason instead
    of failing on the environment.
    """

    cache = Path("/home/app/.cache")
    if not (cache.is_dir() and os.access(cache, os.W_OK)):
        pytest.skip("requires writable static-host /home/app/.cache layout")


def _run_github_token_block(env: dict[str, str], config_dir: Path) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".sh", delete=False
    ) as handle:
        handle.write("set -eu\n")
        handle.write(_github_token_block())
        block_path = handle.name
    config_dir.mkdir(parents=True, exist_ok=True)
    run_env = dict(env)
    return subprocess.run(
        ["/bin/sh", block_path],
        capture_output=True,
        text=True,
        check=False,
        env=run_env,
    )


def test_packaged_github_token_block_writes_restart_preserving_and_rejects() -> None:
    _require_writable_static_host_cache()
    block = _github_token_block()
    # No deletion path: a restart without a token cannot clear persisted auth.
    assert "rm " not in block
    assert "hosts.yml" in block
    assert block.startswith("github_token=${GH_TOKEN:-}")
    assert "unset github_token GH_TOKEN" in block

    base_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/app",
    }
    pid = str(subprocess.os.getpid())
    config_home = Path(f"/home/app/.cache/mm-gh-token-test-{pid}")
    try:
        # Supplied token is written with the bounded selector charset.
        result = _run_github_token_block(
            {**base_env, "XDG_CONFIG_HOME": str(config_home), "GH_TOKEN": "Abc_123"},
            config_home / "gh",
        )
        assert result.returncode == 0, result.stderr
        hosts = (config_home / "gh" / "hosts.yml").read_text()
        assert "oauth_token: Abc_123" in hosts

        # Restart without a new token preserves the persisted config.
        sentinel = "github.com:\n    oauth_token: SENTINEL_PRESERVED\n"
        (config_home / "gh" / "hosts.yml").write_text(sentinel)
        result = _run_github_token_block(
            {**base_env, "XDG_CONFIG_HOME": str(config_home)},
            config_home / "gh",
        )
        assert result.returncode == 0, result.stderr
        assert (config_home / "gh" / "hosts.yml").read_text() == sentinel

        # Unsupported characters are rejected before any write.
        (config_home / "gh" / "hosts.yml").unlink()
        result = _run_github_token_block(
            {
                **base_env,
                "XDG_CONFIG_HOME": str(config_home),
                "GH_TOKEN": "bad-token!",
            },
            config_home / "gh",
        )
        assert result.returncode != 0
        assert not (config_home / "gh" / "hosts.yml").exists()

        # Config homes outside the approved cache prefix fail closed.
        result = _run_github_token_block(
            {
                **base_env,
                "XDG_CONFIG_HOME": "/tmp/evil-gh-config",
                "GH_TOKEN": "Abc_123",
            },
            config_home / "gh",
        )
        assert result.returncode != 0
    finally:
        import shutil

        shutil.rmtree(config_home, ignore_errors=True)


# -------------------------------------------------------- R5: bounded waits


def test_startup_readiness_waits_are_bounded_with_distinct_outcomes() -> None:
    script = _startup_script()
    assert "MOONMIND_OMNIGENT_STATIC_CREDENTIAL_TIMEOUT_SECONDS" in script
    assert "MOONMIND_OMNIGENT_STATIC_SKILL_TIMEOUT_SECONDS" in script
    assert "must be positive integers" in script
    # The old unbounded `until ... sleep 5` loops are gone.
    assert "\n    sleep 5\n" not in script
    assert 'sleep "$readiness_interval"' in script
    # Waiting-for-enrollment and missing-projection are distinct from ready:
    # each deadline exits nonzero with a state that names the pending cause
    # instead of admitted capacity.
    assert "waiting-for-enrollment deadline exceeded" in script
    assert "Skill-projection deadline exceeded" in script
    assert "not admitted capacity" in script
    assert "process existence is never admitted usable" in script
    result = subprocess.run(
        ["/bin/sh", "-n", str(SCRIPTS / "start-omnigent-host.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# ------------------------------------------------- R7: lifecycle/drain owner


def test_static_claude_lifecycle_and_drain_have_a_durable_owner() -> None:
    row = _claude_retirement_row()
    assert row.owner == "omnigent-deployment"
    assert row.earliest_removal_stage is RemovalStage.STARTUP_AND_COMPOSE
    assert "static_or_on_demand_host" in {
        dependency.value for dependency in row.active_resource_dependencies
    }
    assert "host_binding_or_lease" in {
        dependency.value for dependency in row.active_resource_dependencies
    }
    # The consolidation inventory defers removal authority to this row.
    inventory = (SCRIPTS / "STATIC_HOST_STARTUP_INVENTORY.md").read_text()
    assert "omnigent.legacy.claude_static_host_startup" in inventory
    assert "RETIREMENT_INVENTORY" in inventory
    # The static Claude row still resolves through the shared digest-pinned
    # host-class authority used by every execution owner.
    templates = {
        template.ref: template for template in DEFAULT_HOST_CLASS_TEMPLATES
    }
    claude_row = static_hosts.static_host_row("omnigent-host-claude")
    template = templates[claude_row.host_class_ref]
    assert template.image_env == "OMNIGENT_SHARED_HOST_IMAGE_REF"
    assert template.runtime_pack_ref == claude_row.runtime_pack_ref
    assert claude_row.materializer_ref in template.materializer_refs
    # Every authority handoff the journey requires names its durable owner;
    # Compose process existence alone is not host authority.
    notes = static_hosts.static_host_authority_notes()
    for owner in (
        "provider_profile_capacity",
        "host_binding_and_lease",
        "generation_fencing",
        "exact_host_attestation",
        "session_and_turn_ownership",
        "cleanup_and_drain",
    ):
        assert notes[owner], owner
    # Hermetic scope boundary: the Profile -> binding/attestation -> canonical
    # session/turn -> terminal evidence -> cleanup journey on the static
    # image/auth/host-mode combination (including interruption, rotation, and
    # reconnect) requires exact-image Docker runs and authorized
    # live-provider evidence; it is not inferred from these unit checks or
    # from on-demand generic success.


# --------------------------------- R3: admission boundary wiring


def _claude_raw_env() -> dict[str, str]:
    return _raw_env_map(_load_compose()["services"]["omnigent-host-claude"])


def test_admit_static_host_compose_launch_admits_pinned_and_rejects() -> None:
    raw = _claude_raw_env()
    # Digest-pinned shared ref: admitted with the effective image and the
    # rendered effective environment (not raw expressions).
    admitted = static_hosts.admit_static_host_compose_launch(
        service="omnigent-host-claude",
        pack_ref="claude-native-pack@1",
        materializer_ref="claude-oauth-home@1",
        operator_env={"OMNIGENT_SHARED_HOST_IMAGE_REF": FAKE_DIGEST},
        raw_service_env=raw,
    )
    assert admitted["image_ref"] == FAKE_DIGEST
    assert admitted["service"] == "omnigent-host-claude"
    rendered = admitted["rendered_env"]
    assert isinstance(rendered, dict)
    assert rendered["CLAUDE_CREDENTIAL_GENERATION"] == "1"
    assert "${" not in rendered["CLAUDE_CREDENTIAL_GENERATION"]
    # Bounded legacy alias: admitted when the shared ref is unset.
    admitted_legacy = static_hosts.admit_static_host_compose_launch(
        service="omnigent-host-claude",
        pack_ref="claude-native-pack@1",
        materializer_ref="claude-oauth-home@1",
        operator_env={"OMNIGENT_HOST_IMAGE_REF": LEGACY_DIGEST},
        raw_service_env=raw,
    )
    assert admitted_legacy["image_ref"] == LEGACY_DIGEST
    # Unset configuration: the boundary rejects instead of launching the
    # mutable Compose default.
    with pytest.raises(HarnessPlatformError):
        static_hosts.admit_static_host_compose_launch(
            service="omnigent-host-claude",
            pack_ref="claude-native-pack@1",
            materializer_ref="claude-oauth-home@1",
            operator_env={},
            raw_service_env=raw,
        )
    # Mutable tag construction: rejected at the boundary, not launched.
    with pytest.raises(HarnessPlatformError):
        static_hosts.admit_static_host_compose_launch(
            service="omnigent-host-claude",
            pack_ref="claude-native-pack@1",
            materializer_ref="claude-oauth-home@1",
            operator_env={"OMNIGENT_SHARED_HOST_IMAGE_TAG": "1.18.11"},
            raw_service_env=raw,
        )
    # Wrong pack for the service: rejected at the boundary instead of
    # becoming the other runtime.
    with pytest.raises(HarnessPlatformError):
        static_hosts.admit_static_host_compose_launch(
            service="omnigent-host-claude",
            pack_ref="codex-native-pack@1",
            materializer_ref="claude-oauth-home@1",
            operator_env={"OMNIGENT_SHARED_HOST_IMAGE_REF": FAKE_DIGEST},
            raw_service_env=raw,
        )


def test_require_static_host_image_authority_fails_closed_at_bootstrap() -> None:
    # Static profiles are opt-in: only an explicit COMPOSE_PROFILES selection
    # admits static rows into the deployment.
    assert image_resolution.static_host_profiles_requested(
        env={"COMPOSE_PROFILES": "omnigent-host-claude"}
    ) == ("omnigent-host-claude",)
    assert image_resolution.static_host_profiles_requested(
        env={"COMPOSE_PROFILES": "temporal-ui, omnigent-host-codex"}
    ) == ("omnigent-host-codex",)
    assert (
        image_resolution.static_host_profiles_requested(env={}) == ()
    )
    assert (
        image_resolution.static_host_profiles_requested(
            env={"COMPOSE_PROFILES": "temporal-ui"}
        )
        == ()
    )
    # Digest-pinned resolution: authoritative at the bootstrap boundary.
    assert (
        image_resolution.require_static_host_image_authority(
            env={"COMPOSE_PROFILES": "omnigent-host-claude"},
            shared_ref=FAKE_DIGEST,
        )
        == FAKE_DIGEST
    )
    # Bounded legacy alias: honored only when the shared ref is unset.
    assert (
        image_resolution.require_static_host_image_authority(
            env={
                "COMPOSE_PROFILES": "omnigent-host-claude",
                "OMNIGENT_HOST_IMAGE_REF": LEGACY_DIGEST,
            },
            shared_ref=None,
        )
        == LEGACY_DIGEST
    )
    # Missing resolution with static profiles requested: fail the
    # reconciliation pass instead of launching an unqualified image.
    with pytest.raises(RuntimeError):
        image_resolution.require_static_host_image_authority(
            env={"COMPOSE_PROFILES": "omnigent-host-claude"},
            shared_ref=None,
        )
    with pytest.raises(RuntimeError):
        image_resolution.require_static_host_image_authority(
            env={"COMPOSE_PROFILES": "omnigent-host-claude"},
            shared_ref="ghcr.io/moonladderstudios/omnigent-host-moonmind:1.18.11",
        )
    # Non-static deployments are unaffected: no static profiles requested,
    # no gate, even with no resolution.
    assert (
        image_resolution.require_static_host_image_authority(
            env={}, shared_ref=FAKE_DIGEST
        )
        == FAKE_DIGEST
    )


# --------------------------------- R5: workflow-side enrollment deadline


def test_static_enrollment_classifier_separates_waiting_ready_failed() -> None:
    classify = static_hosts.classify_static_enrollment_status
    # Nothing ready yet: waiting state, never admitted capacity.
    waiting = classify(credential_ready=False, projection_ready=False)
    assert waiting["status"] == "waiting-for-enrollment"
    assert waiting["admitted"] is False
    assert waiting["retryable"] is True
    # Late enrollment within budget: still waiting, still not capacity.
    assert (
        classify(
            credential_ready=False,
            projection_ready=False,
            elapsed_seconds=1799.0,
        )["status"]
        == "waiting-for-enrollment"
    )
    # Enrollment deadline spent: this attempt fails with a distinct outcome.
    failed = classify(
        credential_ready=False, projection_ready=False, elapsed_seconds=1800.0
    )
    assert failed["status"] == "failed-enrollment-timeout"
    assert failed["admitted"] is False
    assert failed["retryable"] is False
    # Enrolled but projection missing: a different waiting state.
    waiting_projection = classify(
        credential_ready=True, projection_ready=False, elapsed_seconds=10.0
    )
    assert waiting_projection["status"] == "waiting-for-projection"
    assert waiting_projection["admitted"] is False
    # Missing projection past its deadline: distinct failure, not capacity.
    failed_projection = classify(
        credential_ready=True, projection_ready=False, elapsed_seconds=600.0
    )
    assert failed_projection["status"] == "failed-projection-timeout"
    assert failed_projection["admitted"] is False
    # Both gates report ready: the only admitted outcome.
    ready = classify(
        credential_ready=True, projection_ready=True, elapsed_seconds=3.0
    )
    assert ready["status"] == "ready"
    assert ready["admitted"] is True
    # Stale generation: unqualified even when both probes look ready.
    # Process existence (or probe success) is never admitted capacity on a
    # fenced generation.
    unqualified = classify(
        credential_ready=True,
        projection_ready=True,
        generation_fenced_ok=False,
    )
    assert unqualified["status"] == "unqualified"
    assert unqualified["admitted"] is False
    assert unqualified["retryable"] is False
    # Invalid budgets fail closed instead of waiting indefinitely.
    with pytest.raises(HarnessPlatformError):
        classify(credential_ready=False, projection_ready=False,
                 credential_timeout_seconds=0)
    with pytest.raises(HarnessPlatformError):
        classify(credential_ready=True, projection_ready=False,
                 skill_timeout_seconds=-5)


# --------------------------------- R4: marker vs live lease


def test_verify_staged_generation_against_lease_binds_marker_to_lease() -> None:
    verified = static_hosts.verify_staged_generation_against_lease(
        staged_generation="7",
        lease_generation="7",
        provider_profile_id="claude-oauth",
        registered_host_id="omnigent-host-claude",
    )
    assert verified["verified_generation"] == "7"
    # The staged marker alone is not admission evidence: a stale marker
    # against the current lease fails closed.
    with pytest.raises(HarnessPlatformError):
        static_hosts.verify_staged_generation_against_lease(
            staged_generation="6",
            lease_generation="7",
            provider_profile_id="claude-oauth",
            registered_host_id="omnigent-host-claude",
        )
    # Missing marker or missing lease: no silent trust.
    with pytest.raises(HarnessPlatformError):
        static_hosts.verify_staged_generation_against_lease(
            staged_generation="",
            lease_generation="7",
            provider_profile_id="claude-oauth",
            registered_host_id="omnigent-host-claude",
        )
    with pytest.raises(HarnessPlatformError):
        static_hosts.verify_staged_generation_against_lease(
            staged_generation="7",
            lease_generation="",
            provider_profile_id="claude-oauth",
            registered_host_id="omnigent-host-claude",
        )
    # No profile or host identity: the ownership chain is incomplete.
    with pytest.raises(HarnessPlatformError):
        static_hosts.verify_staged_generation_against_lease(
            staged_generation="7",
            lease_generation="7",
            provider_profile_id="",
            registered_host_id="omnigent-host-claude",
        )


def test_trace_static_credential_ownership_enforces_exclusivity() -> None:
    owned = static_hosts.trace_static_credential_ownership(
        provider_profile_id="claude-oauth",
        provider_lease_ref="lease-123",
        credential_generation="7",
        registered_host_id="omnigent-host-claude",
        active_session_count=1,
        execution_modes_observed=("static",),
    )
    assert owned["exclusive"] is True
    assert owned["execution_modes_observed"] == ("static",)
    # Rotation across static, direct, and on-demand stays evidenced against
    # the same lease and generation.
    rotated = static_hosts.trace_static_credential_ownership(
        provider_profile_id="claude-oauth",
        provider_lease_ref="lease-123",
        credential_generation="8",
        registered_host_id="omnigent-host-claude",
        active_session_count=0,
        execution_modes_observed=("static", "direct", "on-demand"),
    )
    assert rotated["verified_generation"] == "8"
    assert rotated["execution_modes_observed"] == (
        "static",
        "direct",
        "on-demand",
    )
    # One-session/lease exclusivity: a second active session fails closed
    # instead of sharing the lease.
    with pytest.raises(HarnessPlatformError):
        static_hosts.trace_static_credential_ownership(
            provider_profile_id="claude-oauth",
            provider_lease_ref="lease-123",
            credential_generation="8",
            registered_host_id="omnigent-host-claude",
            active_session_count=2,
            execution_modes_observed=("static",),
        )
    # Unknown execution mode: rejected, not recorded.
    with pytest.raises(HarnessPlatformError):
        static_hosts.trace_static_credential_ownership(
            provider_profile_id="claude-oauth",
            provider_lease_ref="lease-123",
            credential_generation="8",
            registered_host_id="omnigent-host-claude",
            active_session_count=1,
            execution_modes_observed=("sidecar",),
        )
    # Incomplete ownership chain: rejected.
    with pytest.raises(HarnessPlatformError):
        static_hosts.trace_static_credential_ownership(
            provider_profile_id="claude-oauth",
            provider_lease_ref="",
            credential_generation="8",
            registered_host_id="omnigent-host-claude",
            active_session_count=1,
            execution_modes_observed=("static",),
        )


# --------------------------------- R6: effective isolation


def test_static_service_mounts_are_effective_and_not_crossed() -> None:
    compose = _load_compose()
    services = compose["services"]
    codex_volumes = [str(item) for item in services["omnigent-host-codex"]["volumes"]]
    claude_volumes = [str(item) for item in services["omnigent-host-claude"]["volumes"]]
    assert any(
        item.startswith("codex_auth_volume:/home/app/.codex") for item in codex_volumes
    )
    assert any(
        item.startswith("claude_auth_volume:/home/app/.claude")
        for item in claude_volumes
    )
    # Neither row mounts the other runtime's credential volume.
    assert not any("claude_auth_volume" in item for item in codex_volumes)
    assert not any("codex_auth_volume" in item for item in claude_volumes)
    # State volumes are per-row; a restart of one row cannot clear the other.
    assert any(
        item.startswith("omnigent-host-codex-state:") for item in codex_volumes
    )
    assert any(
        item.startswith("omnigent-host-claude-state:") for item in claude_volumes
    )
    # Rendered effective environments carry only their own generation marker.
    codex_env = static_hosts.render_static_service_env(
        _raw_env_map(services["omnigent-host-codex"]), {}
    )
    claude_env = static_hosts.render_static_service_env(
        _raw_env_map(services["omnigent-host-claude"]), {}
    )
    for key in (
        "CLAUDE_CREDENTIAL_GENERATION",
        "OPENCODE_CREDENTIAL_GENERATION",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    ):
        assert key not in codex_env, key
    for key in (
        "CODEX_CREDENTIAL_GENERATION",
        "OPENCODE_CREDENTIAL_GENERATION",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        assert key not in claude_env, key


def _check_script_lines() -> list[str]:
    return (SCRIPTS / "check-omnigent-host.sh").read_text().splitlines()


def _generation_fencing_fragment() -> str:
    lines = _check_script_lines()
    start = next(
        index
        for index, line in enumerate(lines)
        if line.strip() == '[ -n "$expected_generation" ] || exit 78'
    )
    end = next(
        index
        for index, line in enumerate(lines)
        if 'credential-generation")" = "$expected_generation"' in line
    )
    return "\n".join(lines[start : end + 1]) + "\n"


def _run_shell_fragment(fragment: str, env: dict[str, str]) -> "subprocess.CompletedProcess[str]":
    import tempfile as _tempfile

    with _tempfile.NamedTemporaryFile(
        mode="w", suffix=".sh", delete=False
    ) as handle:
        handle.write("set -eu\n")
        handle.write(fragment)
        fragment_path = handle.name
    run_env = {"PATH": "/usr/bin:/bin", **env}
    try:
        return subprocess.run(
            ["/bin/sh", fragment_path],
            capture_output=True,
            text=True,
            check=False,
            env=run_env,
        )
    finally:
        import os as _os

        _os.unlink(fragment_path)


def test_packaged_generation_fencing_matches_staged_against_expected() -> None:
    fragment = _generation_fencing_fragment()
    assert "exit 78" in fragment and "exit 80" in fragment
    with tempfile.TemporaryDirectory() as state_dir:
        state_root = Path(state_dir)
        (state_root / "credential-generation").write_text("7\n")
        # Staged generation matches the acquired generation: gate passes.
        result = _run_shell_fragment(
            fragment,
            {
                "expected_generation": "7",
                "state_root": str(state_root),
            },
        )
        assert result.returncode == 0, result.stderr
        # Stale staged generation against the current lease: rejected with
        # the fencing outcome, never admitted.
        result = _run_shell_fragment(
            fragment,
            {
                "expected_generation": "8",
                "state_root": str(state_root),
            },
        )
        assert result.returncode == 80
        # Missing staged file: rejected before any auth probe.
        (state_root / "credential-generation").unlink()
        result = _run_shell_fragment(
            fragment,
            {
                "expected_generation": "8",
                "state_root": str(state_root),
            },
        )
        assert result.returncode == 79
        # Missing expected generation: rejected, not defaulted.
        result = _run_shell_fragment(
            fragment,
            {"expected_generation": "", "state_root": str(state_root)},
        )
        assert result.returncode == 78


def _startup_control_fragments() -> tuple[str, str]:
    lines = _startup_script().splitlines()
    ambient_start = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("for key in OPENAI_API_KEY CODEX_ACCESS_TOKEN")
    )
    ambient_end = next(
        index
        for index in range(ambient_start, len(lines))
        if lines[index] == "done"
    )
    ambient = "\n".join(lines[ambient_start : ambient_end + 1]) + "\n"
    cross_start = next(
        index
        for index, line in enumerate(lines)
        if line.strip() == 'if [ "$pack_ref" = "codex-native-pack@1" ]; then'
        and any(
            "cross-runtime credential generation is set" in candidate
            for candidate in lines[index : index + 4]
        )
    )
    cross_end = next(
        index
        for index in range(cross_start, len(lines))
        if lines[index] == "fi"
    )
    cross = "\n".join(lines[cross_start : cross_end + 1]) + "\n"
    return ambient, cross


def test_packaged_startup_rejects_other_runtime_and_ambient_selectors() -> None:
    ambient, cross = _startup_control_fragments()
    preamble = 'pack_ref="claude-native-pack@1"\n'
    harness = preamble + ambient + cross + 'printf "ADMITTED\\n"\n'
    # Other runtime generation markers fail closed on the Claude row,
    # including empty-valued presence.
    for poison in (
        {"CODEX_CREDENTIAL_GENERATION": "1"},
        {"CODEX_CREDENTIAL_GENERATION": ""},
        {"OPENCODE_CREDENTIAL_GENERATION": "1"},
    ):
        result = _run_shell_fragment(harness, dict(poison))
        assert result.returncode == 64, poison
        assert "cross-runtime" in result.stderr
    # Ambient API-key selectors fail closed on the packaged path as well.
    for poison in (
        {"ANTHROPIC_API_KEY": "secret"},
        {"OPENAI_API_KEY": ""},
        {"CLAUDE_CODE_OAUTH_TOKEN": "secret"},
    ):
        result = _run_shell_fragment(harness, dict(poison))
        assert result.returncode == 64, poison
        assert "ambient credential selector" in result.stderr
    # The codex row mirrors the fence for the other runtimes.
    codex_harness = (
        'pack_ref="codex-native-pack@1"\n' + ambient + cross + 'printf "ADMITTED\\n"\n'
    )
    result = _run_shell_fragment(
        codex_harness, {"CLAUDE_CREDENTIAL_GENERATION": "1"}
    )
    assert result.returncode == 64
    result = _run_shell_fragment(codex_harness, {})
    assert result.returncode == 0
    assert "ADMITTED" in result.stdout


def _startup_layout_case(pack: str) -> str:
    lines = _startup_script().splitlines()
    if pack == "claude":
        anchor = 'case "$credential_root:${CLAUDE_HOME:-/home/app/.claude}" in'
    else:
        anchor = 'case "$credential_root:${CODEX_CONFIG_HOME:-/home/app/.codex}:${CODEX_CONFIG_PATH:-/home/app/.codex/config.toml}" in'
    start = next(index for index, line in enumerate(lines) if anchor in line)
    end = next(
        index for index in range(start, len(lines)) if lines[index].strip() == "esac"
    )
    return "\n".join(lines[start : end + 1]) + "\n"


def test_packaged_startup_rejects_wrong_credential_homes() -> None:
    claude_case = _startup_layout_case("claude")
    assert "unexpected Claude credential layout" in claude_case
    preamble = 'credential_root="${CLAUDE_CONFIG_DIR:-/home/app/.claude}"\n'
    # Canonical layout passes the packaged gate.
    result = _run_shell_fragment(preamble + claude_case + 'printf "ADMITTED\\n"\n', {})
    assert result.returncode == 0
    # A wrong home or config dir fails closed instead of staging to an
    # unattested path (including stale-cache-style nested homes).
    for poison in (
        {"CLAUDE_HOME": "/home/app/.cache/stale/.claude"},
        {"CLAUDE_HOME": "/home/app/.codex"},
        {"CLAUDE_CONFIG_DIR": "/tmp/evil-claude"},
    ):
        result = _run_shell_fragment(
            preamble + claude_case + 'printf "ADMITTED\\n"\n', dict(poison)
        )
        assert result.returncode == 64, poison
    codex_case = _startup_layout_case("codex")
    assert "unexpected Codex credential layout" in codex_case
    codex_preamble = 'credential_root="${CODEX_HOME:-/home/app/.codex}"\n'
    result = _run_shell_fragment(
        codex_preamble + codex_case + 'printf "ADMITTED\\n"\n', {}
    )
    assert result.returncode == 0
    result = _run_shell_fragment(
        codex_preamble + codex_case + 'printf "ADMITTED\\n"\n',
        {"CODEX_HOME": "/home/app/.cache/stale/.codex"},
    )
    assert result.returncode == 64


def test_packaged_github_token_block_rotates_and_preserves() -> None:
    _require_writable_static_host_cache()
    base_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/app",
    }
    pid = str(subprocess.os.getpid())
    config_home = Path(f"/home/app/.cache/mm-gh-token-rotate-test-{pid}")
    try:
        # A changed connection (new valid token) rotates the persisted config.
        result = _run_github_token_block(
            {**base_env, "XDG_CONFIG_HOME": str(config_home), "GH_TOKEN": "First_1"},
            config_home / "gh",
        )
        assert result.returncode == 0, result.stderr
        assert "oauth_token: First_1" in (config_home / "gh" / "hosts.yml").read_text()
        result = _run_github_token_block(
            {**base_env, "XDG_CONFIG_HOME": str(config_home), "GH_TOKEN": "Second_2"},
            config_home / "gh",
        )
        assert result.returncode == 0, result.stderr
        assert "oauth_token: Second_2" in (config_home / "gh" / "hosts.yml").read_text()
        # An invalid token never clobbers the persisted connection: the write
        # is rejected and the previous hosts.yml survives untouched.
        before = (config_home / "gh" / "hosts.yml").read_text()
        result = _run_github_token_block(
            {
                **base_env,
                "XDG_CONFIG_HOME": str(config_home),
                "GH_TOKEN": "bad-token!",
            },
            config_home / "gh",
        )
        assert result.returncode != 0
        assert (config_home / "gh" / "hosts.yml").read_text() == before
    finally:
        import shutil

        shutil.rmtree(config_home, ignore_errors=True)


# --------------------------------- R3: effective-launch production parity


def test_admit_static_host_effective_launch_matches_compose_boundary() -> None:
    raw = _claude_raw_env()
    operator_env = {"OMNIGENT_SHARED_HOST_IMAGE_REF": FAKE_DIGEST}
    rendered = static_hosts.render_static_service_env(raw, operator_env)
    via_compose = static_hosts.admit_static_host_compose_launch(
        service="omnigent-host-claude",
        pack_ref="claude-native-pack@1",
        materializer_ref="claude-oauth-home@1",
        operator_env=operator_env,
        raw_service_env=raw,
    )
    via_effective = static_hosts.admit_static_host_effective_launch(
        service="omnigent-host-claude",
        pack_ref="claude-native-pack@1",
        materializer_ref="claude-oauth-home@1",
        rendered_env=rendered,
        operator_env=operator_env,
    )
    assert via_effective == via_compose
    assert via_effective["image_ref"] == FAKE_DIGEST
    # The effective boundary fails closed on the same unqualified inputs.
    for bad_operator in (
        {},
        {"OMNIGENT_SHARED_HOST_IMAGE_TAG": "1.18.11"},
        {"OMNIGENT_SHARED_HOST_IMAGE_REF": "some-image:latest"},
    ):
        with pytest.raises(HarnessPlatformError):
            static_hosts.admit_static_host_effective_launch(
                service="omnigent-host-claude",
                pack_ref="claude-native-pack@1",
                materializer_ref="claude-oauth-home@1",
                rendered_env=rendered,
                operator_env=bad_operator,
            )
    poisoned = dict(rendered)
    poisoned["ANTHROPIC_API_KEY"] = "secret"
    with pytest.raises(HarnessPlatformError):
        static_hosts.admit_static_host_effective_launch(
            service="omnigent-host-claude",
            pack_ref="claude-native-pack@1",
            materializer_ref="claude-oauth-home@1",
            rendered_env=poisoned,
            operator_env=operator_env,
        )


# --------------------------------- R5: enrollment timeout parity


def test_static_enrollment_timeouts_from_env_match_entrypoint_defaults() -> None:
    assert static_hosts.static_enrollment_timeouts_from_env({}) == (1800, 600)
    assert static_hosts.static_enrollment_timeouts_from_env(
        {
            "MOONMIND_OMNIGENT_STATIC_CREDENTIAL_TIMEOUT_SECONDS": "60",
            "MOONMIND_OMNIGENT_STATIC_SKILL_TIMEOUT_SECONDS": "30",
        }
    ) == (60, 30)
    # An explicitly empty value selects the entrypoint `:-` default, exactly
    # as `start-omnigent-host.sh` models it.
    assert static_hosts.static_enrollment_timeouts_from_env(
        {"MOONMIND_OMNIGENT_STATIC_CREDENTIAL_TIMEOUT_SECONDS": ""}
    ) == (1800, 600)
    for bad in (
        {"MOONMIND_OMNIGENT_STATIC_CREDENTIAL_TIMEOUT_SECONDS": "0"},
        {"MOONMIND_OMNIGENT_STATIC_CREDENTIAL_TIMEOUT_SECONDS": "-5"},
        {"MOONMIND_OMNIGENT_STATIC_CREDENTIAL_TIMEOUT_SECONDS": "soon"},
        {"MOONMIND_OMNIGENT_STATIC_SKILL_TIMEOUT_SECONDS": "1.5"},
    ):
        with pytest.raises(HarnessPlatformError):
            static_hosts.static_enrollment_timeouts_from_env(bad)


# --------------------------------- R3/R4: production prelaunch gate


def _production_claude_binding(*, generation: int = 7) -> OmnigentOAuthHostBinding:
    return OmnigentOAuthHostBinding(
        bindingRef="omnigent-oauth:claude",
        providerProfileId="claude-oauth",
        endpointRef="default",
        harness="claude-native",
        credentialMountRef=CredentialMountRef(
            authVolumeRef=AuthVolumeRef(
                providerProfileId="claude-oauth",
                runtimeId="claude_code",
                providerId="anthropic",
                volumeRef="claude_auth_volume",
                credentialGeneration=generation,
                ownerUserId="user-1",
            ),
            targetPath="/home/app/.claude",
            runtimeUid=1000,
            runtimeGid=1000,
        ),
        staticHostId="omnigent-host-claude",
    )


def _production_claude_lease(
    *, generation: int = 7, session_id: str | None = None
) -> OmnigentHostLease:
    now = datetime(2026, 9, 15, tzinfo=UTC)
    return OmnigentHostLease(
        leaseId="host-lease-claude-1",
        providerProfileId="claude-oauth",
        providerLeaseId="provider-lease-claude-1",
        bindingRef="omnigent-oauth:claude",
        credentialGeneration=generation,
        omnigentHostId="omnigent-host-claude",
        omnigentSessionId=session_id,
        status="starting",
        acquiredAt=now,
        lastHeartbeatAt=now,
        expiresAt=now + timedelta(hours=1),
    )


def _pinned_claude_operator_env() -> dict[str, str]:
    return {
        "OMNIGENT_SHARED_HOST_IMAGE_REF": FAKE_DIGEST,
        "CLAUDE_CREDENTIAL_GENERATION": "7",
    }


def test_production_prelaunch_gate_admits_pinned_claude_launch() -> None:
    admission = OmnigentOAuthHostRuntime._admit_static_compose_prelaunch(
        binding=_production_claude_binding(),
        host_lease=_production_claude_lease(),
        operator_env=_pinned_claude_operator_env(),
    )
    assert admission["service"] == "omnigent-host-claude"
    assert admission["image_ref"] == FAKE_DIGEST
    assert admission["registered_host_id"] == "omnigent-host-claude"


def test_production_prelaunch_gate_binds_staged_marker_to_lease() -> None:
    # The staged marker alone is not admission evidence: a stale marker
    # against the live lease fails closed with the generation-stale outcome.
    with pytest.raises(OmnigentOAuthHostError) as excinfo:
        OmnigentOAuthHostRuntime._admit_static_compose_prelaunch(
            binding=_production_claude_binding(generation=7),
            host_lease=_production_claude_lease(generation=8),
            operator_env=_pinned_claude_operator_env(),
        )
    assert excinfo.value.code == HostPreflightFailure.GENERATION_STALE.value


def test_production_prelaunch_gate_rejects_unpinned_image_at_launch() -> None:
    binding = _production_claude_binding()
    lease = _production_claude_lease()
    # Unset configuration: rejected with the exact platform authority code
    # instead of launching the mutable Compose default.
    with pytest.raises(OmnigentOAuthHostError) as excinfo:
        OmnigentOAuthHostRuntime._admit_static_compose_prelaunch(
            binding=binding,
            host_lease=lease,
            operator_env={"CLAUDE_CREDENTIAL_GENERATION": "7"},
        )
    assert excinfo.value.code == "OMNIGENT_HARNESS_BUILD_MISMATCH"
    # Mutable tag construction: rejected at the launch boundary, not launched.
    with pytest.raises(OmnigentOAuthHostError) as excinfo:
        OmnigentOAuthHostRuntime._admit_static_compose_prelaunch(
            binding=binding,
            host_lease=lease,
            operator_env={
                "OMNIGENT_SHARED_HOST_IMAGE": (
                    "ghcr.io/moonladderstudios/omnigent-host-moonmind"
                ),
                "OMNIGENT_SHARED_HOST_IMAGE_TAG": "1.18.11",
                "CLAUDE_CREDENTIAL_GENERATION": "7",
            },
        )
    assert excinfo.value.code == "OMNIGENT_HARNESS_BUILD_MISMATCH"


def test_production_prelaunch_gate_ignores_worker_ambient_but_rejects_rendered_selector() -> None:
    # The canonical worker service defines credential keys (including as
    # empty strings) that are not part of the static service environment:
    # the prelaunch gate judges the rendered service environment, so worker
    # ambient selectors must not reject an otherwise qualified launch.
    ambient = _pinned_claude_operator_env()
    ambient["ANTHROPIC_API_KEY"] = "secret"
    ambient["CLAUDE_API_KEY"] = ""
    admission = OmnigentOAuthHostRuntime._admit_static_compose_prelaunch(
        binding=_production_claude_binding(),
        host_lease=_production_claude_lease(),
        operator_env=ambient,
    )
    assert admission["service"] == "omnigent-host-claude"
    # A forbidden selector in the rendered service environment still fails
    # closed at the launch boundary.
    raw = _claude_raw_env()
    poisoned_raw = dict(raw)
    poisoned_raw["ANTHROPIC_API_KEY"] = "secret"
    rendered = static_hosts.render_static_service_env(
        poisoned_raw, _pinned_claude_operator_env()
    )
    with pytest.raises(HarnessPlatformError):
        static_hosts.validate_static_combination(
            service="omnigent-host-claude",
            pack_ref="claude-native-pack@1",
            materializer_ref="claude-oauth-home@1",
            environment=rendered,
        )


@pytest.mark.asyncio
async def test_compose_static_check_runs_prelaunch_gate_before_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_COMPOSE_FILE", str(REPO_ROOT / "docker-compose.yaml")
    )
    for key in (
        "MOONMIND_OMNIGENT_STATIC_CREDENTIAL_TIMEOUT_SECONDS",
        "MOONMIND_OMNIGENT_STATIC_SKILL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace())
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str, env=None, check=True):  # type: ignore[no-untyped-def]
        calls.append(tuple(args))
        return 0, "", ""

    runtime._run = fake_run  # type: ignore[method-assign]
    binding = _production_claude_binding()
    lease = _production_claude_lease()
    effective_launch = {
        "hostImageRef": FAKE_DIGEST,
        "serverImageRef": FAKE_DIGEST,
        "snapshotRef": "omnigent-launch:sha256:" + "c" * 64,
        "providerRuntime": "claude_code",
        "harness": "claude-native",
        "hostMode": "static_compose",
        "runtimeUid": 1000,
        "runtimeGid": 1000,
        "limits": {
            "cpuMillis": 2000,
            "memoryMiB": 4096,
            "processes": 256,
            "temporaryStorageMiB": 256,
            "timeoutSeconds": 5400,
        },
        "capture": {"retentionDays": 30},
        "controlCapabilities": ["interrupt", "terminate"],
    }
    env = await runtime._compose_static_check(
        binding=binding,
        host_lease=lease,
        workspace_source=tmp_path,
        skill_projection=None,
        effective_launch=effective_launch,
        egress_attestation=None,
    )
    assert env["OMNIGENT_SHARED_HOST_IMAGE_REF"] == FAKE_DIGEST
    assert env["CLAUDE_CREDENTIAL_GENERATION"] == "7"
    up_calls = [args for args in calls if "up" in args]
    assert len(up_calls) == 1
    assert "omnigent-host-claude" in up_calls[0]

    # An unqualified image never reaches `docker compose up`: the gate fails
    # first at the production launch boundary.
    calls.clear()
    bad_launch = dict(
        effective_launch,
        hostImageRef=(
            "ghcr.io/moonladderstudios/omnigent-host-moonmind:1.18.11"
        ),
    )
    with pytest.raises(OmnigentOAuthHostError):
        await runtime._compose_static_check(
            binding=binding,
            host_lease=lease,
            workspace_source=tmp_path,
            skill_projection=None,
            effective_launch=bad_launch,
            egress_attestation=None,
        )
    assert calls == []


# --------------------------------- R5: production enrollment admission


def test_production_enrollment_admits_ready_and_rejects_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "MOONMIND_OMNIGENT_STATIC_CREDENTIAL_TIMEOUT_SECONDS",
        "MOONMIND_OMNIGENT_STATIC_SKILL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    binding = _production_claude_binding(generation=7)
    lease = _production_claude_lease(generation=7)
    ready = OmnigentOAuthHostRuntime._classify_static_enrollment(
        binding=binding,
        host_lease=lease,
        credential_ready=True,
        projection_ready=True,
        elapsed_seconds=3.0,
    )
    assert ready["status"] == "ready"
    admitted = OmnigentOAuthHostRuntime._require_static_enrollment_admitted(ready)
    assert admitted["admitted"] is True

    # Stale lease generation: unqualified even when both probes report ready.
    # Probe success (or process existence) is never admitted capacity on a
    # fenced generation.
    stale_lease = _production_claude_lease(generation=8)
    unqualified = OmnigentOAuthHostRuntime._classify_static_enrollment(
        binding=binding,
        host_lease=stale_lease,
        credential_ready=True,
        projection_ready=True,
        elapsed_seconds=3.0,
    )
    assert unqualified["status"] == "unqualified"
    with pytest.raises(OmnigentOAuthHostError) as excinfo:
        OmnigentOAuthHostRuntime._require_static_enrollment_admitted(unqualified)
    assert excinfo.value.code == HostPreflightFailure.GENERATION_STALE.value


def test_production_enrollment_failure_spends_budget_past_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_OMNIGENT_STATIC_CREDENTIAL_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("MOONMIND_OMNIGENT_STATIC_SKILL_TIMEOUT_SECONDS", "30")
    runtime = OmnigentOAuthHostRuntime(client=SimpleNamespace())
    binding = _production_claude_binding()
    lease = _production_claude_lease()
    probe_error = RuntimeError("projection probe failed")
    # Past the Skill-projection deadline: the distinct failed outcome
    # replaces the probe error so this attempt's budget is spent.
    with pytest.raises(OmnigentOAuthHostError) as excinfo:
        runtime._raise_static_enrollment_failure(
            binding=binding,
            host_lease=lease,
            credential_ready=True,
            projection_ready=False,
            elapsed_seconds=30.0,
            fallback=probe_error,
        )
    assert excinfo.value.code == HostPreflightFailure.LOGIN_STATUS_FAILED.value
    assert excinfo.value.__cause__ is probe_error
    # Within budget: the original probe error propagates (operator
    # enrollment pending, retryable as a new attempt).
    with pytest.raises(RuntimeError) as excinfo2:
        runtime._raise_static_enrollment_failure(
            binding=binding,
            host_lease=lease,
            credential_ready=True,
            projection_ready=False,
            elapsed_seconds=3.0,
            fallback=probe_error,
        )
    assert excinfo2.value is probe_error


# --------------------------------- R6: packaged-script isolation


def test_packaged_startup_rejects_opencode_ambient_selectors() -> None:
    ambient, _cross = _startup_control_fragments()
    harness = ambient + 'printf "ADMITTED\\n"\n'
    # The packaged ambient fence covers the on-demand runtime's selectors on
    # the static rows too: presence alone fails closed, even empty-valued.
    for poison in (
        {"OPENCODE_AUTH_CONTENT": "secret"},
        {"OPENCODE_AUTH_CONTENT": ""},
        {"OPENCODE_CONFIG": "/tmp/evil-config"},
        {"OPENCODE_CONFIG_CONTENT": "config"},
    ):
        result = _run_shell_fragment(harness, dict(poison))
        assert result.returncode == 64, poison
        assert "ambient credential selector" in result.stderr
    result = _run_shell_fragment(harness, {})
    assert result.returncode == 0
    assert "ADMITTED" in result.stdout


def test_github_token_block_preserves_unrelated_config_contents() -> None:
    _require_writable_static_host_cache()
    base_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/app",
    }
    pid = str(subprocess.os.getpid())
    config_home = Path(f"/home/app/.cache/mm-gh-token-preserve-test-{pid}")
    try:
        gh_dir = config_home / "gh"
        gh_dir.mkdir(parents=True, exist_ok=True)
        (gh_dir / "hosts.yml").write_text(
            "github.com:\n    oauth_token: STALE_1\n"
        )
        (gh_dir / "config.yml").write_text("editor: vim\n")
        before_hosts = (gh_dir / "hosts.yml").read_text()
        before_config = (gh_dir / "config.yml").read_text()
        # Restart without a token preserves every persisted file untouched:
        # the block has no deletion or directory-reset path.
        result = _run_github_token_block(
            {**base_env, "XDG_CONFIG_HOME": str(config_home)}, gh_dir
        )
        assert result.returncode == 0, result.stderr
        assert (gh_dir / "hosts.yml").read_text() == before_hosts
        assert (gh_dir / "config.yml").read_text() == before_config
        # A rejected token preserves the previous connection and its neighbors.
        result = _run_github_token_block(
            {
                **base_env,
                "XDG_CONFIG_HOME": str(config_home),
                "GH_TOKEN": "bad-token!",
            },
            gh_dir,
        )
        assert result.returncode != 0
        assert (gh_dir / "hosts.yml").read_text() == before_hosts
        assert (gh_dir / "config.yml").read_text() == before_config
    finally:
        import shutil

        shutil.rmtree(config_home, ignore_errors=True)
