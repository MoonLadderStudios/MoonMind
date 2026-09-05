"""Static Codex/Claude host consolidation (MoonLadderStudios/MoonMind#3834).

Both static-connected services share the digest-pinned
`omnigent-host-moonmind` image and one generic startup implementation.
Runtime differences are limited to trusted runtime-pack and
credential-materializer selections; each service receives only its own
runtime's credential state.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from moonmind.omnigent.harness_platform import static_hosts
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.host_classes import (
    DEFAULT_HOST_CLASS_TEMPLATES,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "services" / "omnigent" / "scripts"
FAKE_DIGEST = (
    "ghcr.io/moonladderstudios/omnigent-host-moonmind"
    "@sha256:" + "a1" * 32
)


def _load_compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yaml").read_text())


def _env_map(service: dict) -> dict[str, str]:
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


def test_static_rows_share_one_exact_image() -> None:
    compose = _load_compose()
    services = compose["services"]
    codex_image = services["omnigent-host-codex"]["image"]
    claude_image = services["omnigent-host-claude"]["image"]
    anchor_image = compose["x-omnigent-static-host"]["image"]
    assert codex_image == claude_image == anchor_image
    assert "OMNIGENT_SHARED_HOST_IMAGE_REF" in codex_image
    assert "omnigent-ai/omnigent-host" not in codex_image

    resolved = static_hosts.resolve_static_host_image_ref(
        {"OMNIGENT_SHARED_HOST_IMAGE_REF": FAKE_DIGEST}
    )
    assert resolved == FAKE_DIGEST


def test_legacy_image_var_is_a_bounded_alias_only() -> None:
    legacy_digest = (
        "ghcr.io/omnigent-ai/omnigent-host" + "@sha256:" + "b2" * 32
    )
    assert static_hosts.resolve_static_host_image_ref(
        {"OMNIGENT_HOST_IMAGE_REF": legacy_digest}
    ) == legacy_digest
    # The shared ref always wins; the alias never overrides it.
    assert static_hosts.resolve_static_host_image_ref(
        {
            "OMNIGENT_SHARED_HOST_IMAGE_REF": FAKE_DIGEST,
            "OMNIGENT_HOST_IMAGE_REF": legacy_digest,
        }
    ) == FAKE_DIGEST
    # Mutable tags are rejected on both paths.
    with pytest.raises(HarnessPlatformError):
        static_hosts.resolve_static_host_image_ref(
            {"OMNIGENT_SHARED_HOST_IMAGE_REF": "some-image:latest"}
        )
    with pytest.raises(HarnessPlatformError):
        static_hosts.resolve_static_host_image_ref(
            {"OMNIGENT_HOST_IMAGE_REF": "some-image:latest"}
        )
    with pytest.raises(HarnessPlatformError):
        static_hosts.resolve_static_host_image_ref({})


def test_static_rows_share_common_hardening_and_topology() -> None:
    compose = _load_compose()
    services = compose["services"]
    codex = services["omnigent-host-codex"]
    claude = services["omnigent-host-claude"]
    anchor = compose["x-omnigent-static-host"]
    for key in (
        "user",
        "working_dir",
        "read_only",
        "tmpfs",
        "cap_drop",
        "security_opt",
        "cpus",
        "mem_limit",
        "pids_limit",
        "stop_grace_period",
        "restart",
        "entrypoint",
    ):
        assert codex[key] == claude[key] == anchor[key], key
    assert codex["entrypoint"] == [static_hosts.GENERIC_STATIC_ENTRYPOINT]
    assert codex["healthcheck"] == claude["healthcheck"]
    assert "check-omnigent-host.sh" in codex["healthcheck"]["test"][1]
    for service in (codex, claude):
        networks = service.get("networks", [])
        names = set(networks) if isinstance(networks, list) else set(networks)
        assert names == {"omnigent-egress-network"}
        assert service["restart"] == "unless-stopped"
        assert service["depends_on"]["omnigent-tools-init"] == {
            "condition": "service_completed_successfully"
        }
        assert service["depends_on"]["sandbox-egress-proxy"] == {
            "condition": "service_healthy"
        }
    # Migration compatibility: runtime-specific names and profiles stay.
    assert codex["profiles"] == ["omnigent-host-codex"]
    assert claude["profiles"] == ["omnigent-host-claude"]
    assert codex["hostname"] == "omnigent-host-codex"
    assert claude["hostname"] == "omnigent-host-claude"


def test_static_rows_select_only_their_trusted_pack_and_materializer() -> None:
    compose = _load_compose()
    services = compose["services"]
    for service_name, pack_ref, materializer_ref, generation_env in (
        (
            "omnigent-host-codex",
            "codex-native-pack@1",
            "codex-oauth-home@1",
            "CODEX_CREDENTIAL_GENERATION",
        ),
        (
            "omnigent-host-claude",
            "claude-native-pack@1",
            "claude-oauth-home@1",
            "CLAUDE_CREDENTIAL_GENERATION",
        ),
    ):
        env = _env_map(services[service_name])
        assert env["MOONMIND_OMNIGENT_RUNTIME_PACK_REF"] == pack_ref
        assert (
            env["MOONMIND_OMNIGENT_CREDENTIAL_MATERIALIZER_REF"]
            == materializer_ref
        )
        row = static_hosts.validate_static_combination(
            service=service_name,
            pack_ref=env["MOONMIND_OMNIGENT_RUNTIME_PACK_REF"],
            materializer_ref=env[
                "MOONMIND_OMNIGENT_CREDENTIAL_MATERIALIZER_REF"
            ],
            environment=env,
        )
        assert row.generation_env == generation_env


def test_static_rows_carry_only_their_runtime_credential_state() -> None:
    compose = _load_compose()
    services = compose["services"]
    codex_env = _env_map(services["omnigent-host-codex"])
    claude_env = _env_map(services["omnigent-host-claude"])
    codex_volumes = " ".join(
        str(v) for v in services["omnigent-host-codex"]["volumes"]
    )
    claude_volumes = " ".join(
        str(v) for v in services["omnigent-host-claude"]["volumes"]
    )
    # Codex row: no Claude or OpenCode credential state.
    assert "CLAUDE_CREDENTIAL_GENERATION" not in codex_env
    assert "OPENCODE_CREDENTIAL_GENERATION" not in codex_env
    assert not any("CLAUDE" in key for key in codex_env)
    assert not any("OPENCODE" in key for key in codex_env)
    assert "claude_auth_volume" not in codex_volumes
    assert "codex_auth_volume" in codex_volumes
    # Claude row: no Codex or OpenCode credential state.
    assert "CODEX_CREDENTIAL_GENERATION" not in claude_env
    assert "OPENCODE_CREDENTIAL_GENERATION" not in claude_env
    assert not any("CODEX" in key for key in claude_env)
    assert not any("OPENCODE" in key for key in claude_env)
    assert "codex_auth_volume" not in claude_volumes
    assert "claude_auth_volume" in claude_volumes
    # Neither row accepts ambient API-key selectors.
    for env in (codex_env, claude_env):
        for key in static_hosts.FORBIDDEN_STATIC_AMBIENT_KEYS:
            assert key not in env, key


def test_wrong_pack_and_materializer_combinations_fail_closed() -> None:
    compose = _load_compose()
    codex_env = _env_map(
        compose["services"]["omnigent-host-codex"]
    )
    with pytest.raises(HarnessPlatformError):
        static_hosts.validate_static_combination(
            service="omnigent-host-codex",
            pack_ref="claude-native-pack@1",
            materializer_ref="codex-oauth-home@1",
            environment=codex_env,
        )
    with pytest.raises(HarnessPlatformError):
        static_hosts.validate_static_combination(
            service="omnigent-host-codex",
            pack_ref="codex-native-pack@1",
            materializer_ref="claude-oauth-home@1",
            environment=codex_env,
        )
    with pytest.raises(HarnessPlatformError):
        static_hosts.validate_static_combination(
            service="omnigent-host-codex",
            pack_ref="opencode-native-pack@1",
            materializer_ref="codex-oauth-home@1",
            environment=codex_env,
        )
    # Cross-runtime generation markers fail.
    poisoned = dict(codex_env, CLAUDE_CREDENTIAL_GENERATION="2")
    with pytest.raises(HarnessPlatformError):
        static_hosts.validate_static_combination(
            service="omnigent-host-codex",
            pack_ref="codex-native-pack@1",
            materializer_ref="codex-oauth-home@1",
            environment=poisoned,
        )
    # Ambient selectors fail.
    poisoned = dict(codex_env, ANTHROPIC_API_KEY="secret")
    with pytest.raises(HarnessPlatformError):
        static_hosts.validate_static_combination(
            service="omnigent-host-codex",
            pack_ref="codex-native-pack@1",
            materializer_ref="codex-oauth-home@1",
            environment=poisoned,
        )
    # Missing generation fails (generation fencing).
    missing = {
        key: value
        for key, value in codex_env.items()
        if key != "CODEX_CREDENTIAL_GENERATION"
    }
    with pytest.raises(HarnessPlatformError):
        static_hosts.validate_static_combination(
            service="omnigent-host-codex",
            pack_ref="codex-native-pack@1",
            materializer_ref="codex-oauth-home@1",
            environment=missing,
        )


def test_generic_startup_contract() -> None:
    script = (SCRIPTS / "start-omnigent-host.sh").read_text()
    # Trusted registries only: exact pack refs, no workflow-authored packs.
    assert "codex-native-pack@1" in script
    assert "claude-native-pack@1" in script
    # Generation, Skill/tool, control-credential, and gh handling.
    assert "CODEX_CREDENTIAL_GENERATION" in script
    assert "CLAUDE_CREDENTIAL_GENERATION" in script
    assert "_manifest.json" in script
    assert "/opt/moonmind-tools/manifest.json" in script
    assert "MOONMIND_OMNIGENT_CONTROL_CREDENTIAL_FILE" in script
    assert 'exec omnigent host --server "$server" --non-interactive' in script
    # Unapproved ambient selectors and control smuggling fail closed.
    for key in static_hosts.FORBIDDEN_STATIC_AMBIENT_KEYS:
        assert key in script
    for key in static_hosts.FORBIDDEN_STATIC_CONTROL_KEYS:
        assert key in script
    # No arbitrary command execution: the only exec is the fixed host start
    # (plus the wrapper-level `exec /opt/moonmind/...` delegation, which this
    # file must not contain). The `eval "present=..."` presence checks below
    # expand only a fixed local key name; they never evaluate external input.
    assert "exec /opt/moonmind/start-omnigent-host.sh" not in script
    assert "exec $" not in script
    assert 'exec "$' not in script
    assert script.count("\nexec ") == 1
    result = subprocess.run(
        ["/bin/sh", "-n", str(SCRIPTS / "start-omnigent-host.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_generic_startup_rejects_unapproved_pack_and_control() -> None:
    script = (SCRIPTS / "start-omnigent-host.sh").read_text()
    assert "unsupported runtime pack ref" in script
    assert "unapproved host control variable is set" in script
    assert "unapproved ambient credential selector is set" in script
    assert "cross-runtime credential generation" in script


def test_generic_health_contract_reports_safe_evidence_only() -> None:
    script = (SCRIPTS / "check-omnigent-host.sh").read_text()
    for token in (
        "pack=",
        "runtime_version=",
        "generation=",
        "auth=",
        "endpoint=",
        "host=",
    ):
        assert token in script
    # Executable lines never print token bodies, session ids, raw auth
    # output, or credential filenames: UPPER_CASE deny-list names (matched
    # case-sensitively below) are the enforcement mechanism, not a leak.
    executable = "\n".join(
        line
        for line in script.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    for forbidden in (
        "oauth_token:",
        "auth.json",
        "provider-session",
        "provider_session",
    ):
        assert forbidden not in executable.lower()
    # Auth probes discard raw output instead of printing it.
    assert "login status >/dev/null 2>&1" in executable
    assert "auth status >/dev/null 2>&1" in executable
    # Generation fencing: staged file must match the acquired generation.
    assert "credential-generation" in script
    # Cross-runtime and ambient isolation mirror the startup contract.
    assert "CLAUDE_CREDENTIAL_GENERATION" in script
    assert "CODEX_CREDENTIAL_GENERATION" in script
    assert "OPENCODE_CREDENTIAL_GENERATION" in script
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        assert key in script
    result = subprocess.run(
        ["/bin/sh", "-n", str(SCRIPTS / "check-omnigent-host.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_legacy_wrappers_are_thin_and_select_only_a_pack_ref() -> None:
    for wrapper_name, pack_ref in (
        ("start-codex-oauth-host.sh", "codex-native-pack@1"),
        ("start-claude-oauth-host.sh", "claude-native-pack@1"),
        ("check-codex-oauth-host.sh", "codex-native-pack@1"),
        ("check-claude-oauth-host.sh", "claude-native-pack@1"),
    ):
        wrapper = (SCRIPTS / wrapper_name).read_text()
        assert f"MOONMIND_OMNIGENT_RUNTIME_PACK_REF={pack_ref}" in wrapper
        assert len(wrapper.splitlines()) < 15
        assert "credential-generation" not in wrapper
        assert "github_config_home" not in wrapper
        assert "check-runner-projections" not in wrapper
        result = subprocess.run(
            ["/bin/sh", "-n", str(SCRIPTS / wrapper_name)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_old_startup_paths_have_an_inventory_with_retirement() -> None:
    inventory = (SCRIPTS / "STATIC_HOST_STARTUP_INVENTORY.md").read_text()
    for old_path in (
        "start-codex-oauth-host.sh",
        "start-claude-oauth-host.sh",
        "check-codex-oauth-host.sh",
        "check-claude-oauth-host.sh",
        "init-codex-oauth-host.sh",
        "OMNIGENT_HOST_IMAGE_REF",
    ):
        assert old_path in inventory
    assert "codex-profile-bound@1" in inventory
    assert "Retirement" in inventory or "retirement" in inventory


def test_static_rows_map_to_shared_image_host_classes() -> None:
    templates = {template.ref: template for template in DEFAULT_HOST_CLASS_TEMPLATES}
    for row in static_hosts.STATIC_HOST_ROWS:
        template = templates[row.host_class_ref]
        assert template.image_env == "OMNIGENT_SHARED_HOST_IMAGE_REF"
        assert template.runtime_pack_ref == row.runtime_pack_ref
        assert row.materializer_ref in template.materializer_refs
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


def test_rendered_static_environment_carries_no_secrets() -> None:
    compose = _load_compose()
    secret_pattern = re.compile(
        r"ghp_|github_pat_|AIza|ATATT|AKIA|BEGIN [A-Z ]*PRIVATE KEY|"
        r"oauth_token:\s*\S|\"key\"\s*:",
        re.IGNORECASE,
    )
    for service_name in ("omnigent-host-codex", "omnigent-host-claude"):
        env = _env_map(compose["services"][service_name])
        rendered = "\n".join(f"{key}={value}" for key, value in env.items())
        assert not secret_pattern.search(rendered), service_name
        health = " ".join(
            compose["services"][service_name]["healthcheck"]["test"]
        )
        assert not secret_pattern.search(health), service_name
