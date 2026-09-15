"""Static Claude qualify-or-retire evidence (MoonLadderStudios/MoonMind#3936).

The shared static template baseline (R1) is covered by
``test_static_host_consolidation.py``. This module closes the remaining
qualify-or-retire items for the static Claude path against effective inputs:

- R2: the retained disposition is recorded in code and matches the
  code-owned retirement row (no silent host-mode change).
- R3: the effective launch image is classified for every operator
  configuration (shared digest, missing ref, mutable tag, legacy-only
  digest, conflicting settings) through the trusted boundary
  ``resolve_effective_static_host_image``, not raw-YAML equality.
- R4: credential generation fencing is verified through rendered effective
  environments plus the packaged startup read-back check.
- R5: readiness waits are bounded with distinct waiting vs failed outcomes.
- R6: isolation is verified through rendered effective inputs and the
  packaged GH_TOKEN block (write, invalid-token rejection, and
  restart-without-token preservation).
- R7: lifecycle/drain ownership is pinned to the retirement row, the
  authority owners, and the host-class mapping.

A full Profile -> static-host binding/attestation -> canonical
session/turn -> terminal evidence -> cleanup journey against an exact-image
Docker host and live-provider evidence remains out of unit scope and is
named explicitly in ``test_static_claude_drain_ownership`` instead of being
inferred from these hermetic checks.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

from moonmind.omnigent.harness_platform import static_hosts
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.host_classes import (
    DEFAULT_HOST_CLASS_TEMPLATES,
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
        "ghcr.io/moonladderstudios/omnigent-host-moonmind:1.18.11"
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
    # Empty ambient and cross-runtime selectors still fail: presence alone
    # fails closed, even with an empty value.
    for poison in (
        {"ANTHROPIC_API_KEY": ""},
        {"CODEX_CREDENTIAL_GENERATION": ""},
        {"OPENCODE_CREDENTIAL_GENERATION": ""},
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
