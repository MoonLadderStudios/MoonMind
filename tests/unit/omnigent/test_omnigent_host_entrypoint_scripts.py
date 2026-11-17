"""Packaged Omnigent host entrypoint and health scripts.

Profile-bound on-demand hosts start through ``start-omnigent-host.sh`` and
report health through ``check-omnigent-host.sh``. These checks run the
packaged shell fragments hermetically: selected runtime-pack refs, credential
isolation, generation fencing, bounded readiness waits, and GitHub token
staging.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "services" / "omnigent" / "scripts"

# Ambient credential selectors the entrypoint refuses on any pack.
FORBIDDEN_AMBIENT_KEYS = (
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
# Variables that would smuggle commands, paths, or allowlists into the
# trusted entrypoint. Presence alone fails closed.
FORBIDDEN_CONTROL_KEYS = (
    "MOONMIND_OMNIGENT_ENTRYPOINT_CMD",
    "MOONMIND_OMNIGENT_EXTRA_ARGS",
    "MOONMIND_OMNIGENT_ENV_ALLOWLIST",
    "MOONMIND_OMNIGENT_TARGET_PATH",
    "MOONMIND_OMNIGENT_MOUNT_PATH",
    "OMNIGENT_HOST_CMD",
    "OMNIGENT_HOST_ENTRYPOINT_ARGS",
)


def _startup_script() -> str:
    return (SCRIPTS / "start-omnigent-host.sh").read_text()


def _assert_shell_syntax(name: str) -> None:
    result = subprocess.run(
        ["/bin/sh", "-n", str(SCRIPTS / name)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _run_shell_fragment(
    fragment: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as handle:
        handle.write("set -eu\n")
        handle.write(fragment)
        fragment_path = handle.name
    try:
        return subprocess.run(
            ["/bin/sh", fragment_path],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": "/usr/bin:/bin", **env},
        )
    finally:
        os.unlink(fragment_path)


def test_startup_contract_selects_only_trusted_packs_and_one_exec() -> None:
    script = _startup_script()
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
    for key in FORBIDDEN_AMBIENT_KEYS:
        assert key in script
    for key in FORBIDDEN_CONTROL_KEYS:
        assert key in script
    # No arbitrary command execution: the only exec is the fixed host start.
    # The `eval "present=..."` presence checks expand only a fixed local key
    # name; they never evaluate external input.
    assert "exec $" not in script
    assert 'exec "$' not in script
    assert script.count("\nexec ") == 1
    assert "unsupported runtime pack ref" in script
    assert "unapproved host control variable is set" in script
    assert "unapproved ambient credential selector is set" in script
    assert "cross-runtime credential generation" in script
    _assert_shell_syntax("start-omnigent-host.sh")


def test_health_contract_reports_safe_evidence_only() -> None:
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
    _assert_shell_syntax("check-omnigent-host.sh")


def test_startup_stages_generation_with_read_back_verification() -> None:
    assert "credential generation staging verification failed" in _startup_script()


def test_startup_readiness_waits_are_bounded_with_distinct_outcomes() -> None:
    script = _startup_script()
    assert "MOONMIND_OMNIGENT_STATIC_CREDENTIAL_TIMEOUT_SECONDS" in script
    assert "MOONMIND_OMNIGENT_STATIC_SKILL_TIMEOUT_SECONDS" in script
    assert "must be positive integers" in script
    # No unbounded `until ... sleep 5` loops.
    assert "\n    sleep 5\n" not in script
    assert 'sleep "$readiness_interval"' in script
    # Waiting-for-enrollment and missing-projection are distinct from ready:
    # each deadline exits nonzero naming the pending cause.
    assert "waiting-for-enrollment deadline exceeded" in script
    assert "Skill-projection deadline exceeded" in script
    assert "not admitted capacity" in script
    _assert_shell_syntax("start-omnigent-host.sh")


def _generation_fencing_fragment() -> str:
    lines = (SCRIPTS / "check-omnigent-host.sh").read_text().splitlines()
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


def test_packaged_generation_fencing_matches_staged_against_expected() -> None:
    fragment = _generation_fencing_fragment()
    assert "exit 78" in fragment and "exit 80" in fragment
    with tempfile.TemporaryDirectory() as state_dir:
        state_root = Path(state_dir)
        (state_root / "credential-generation").write_text("7\n")
        result = _run_shell_fragment(
            fragment, {"expected_generation": "7", "state_root": str(state_root)}
        )
        assert result.returncode == 0, result.stderr
        # A stale staged generation is rejected with the fencing outcome.
        result = _run_shell_fragment(
            fragment, {"expected_generation": "8", "state_root": str(state_root)}
        )
        assert result.returncode == 80
        # Missing staged file: rejected before any auth probe.
        (state_root / "credential-generation").unlink()
        result = _run_shell_fragment(
            fragment, {"expected_generation": "8", "state_root": str(state_root)}
        )
        assert result.returncode == 79
        # Missing expected generation: rejected, not defaulted.
        result = _run_shell_fragment(
            fragment, {"expected_generation": "", "state_root": str(state_root)}
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
        index for index in range(ambient_start, len(lines)) if lines[index] == "done"
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
        index for index in range(cross_start, len(lines)) if lines[index] == "fi"
    )
    cross = "\n".join(lines[cross_start : cross_end + 1]) + "\n"
    return ambient, cross


def test_packaged_startup_rejects_other_runtime_and_ambient_selectors() -> None:
    ambient, cross = _startup_control_fragments()
    harness = (
        'pack_ref="claude-native-pack@1"\n' + ambient + cross + 'printf "ADMITTED\\n"\n'
    )
    # Other runtime generation markers fail closed, including empty presence.
    for poison in (
        {"CODEX_CREDENTIAL_GENERATION": "1"},
        {"CODEX_CREDENTIAL_GENERATION": ""},
        {"OPENCODE_CREDENTIAL_GENERATION": "1"},
    ):
        result = _run_shell_fragment(harness, dict(poison))
        assert result.returncode == 64, poison
        assert "cross-runtime" in result.stderr
    for poison in (
        {"ANTHROPIC_API_KEY": "secret"},
        {"OPENAI_API_KEY": ""},
        {"CLAUDE_CODE_OAUTH_TOKEN": "secret"},
        {"OPENCODE_AUTH_CONTENT": "secret"},
        {"OPENCODE_AUTH_CONTENT": ""},
        {"OPENCODE_CONFIG": "/tmp/evil-config"},
        {"OPENCODE_CONFIG_CONTENT": "config"},
    ):
        result = _run_shell_fragment(harness, dict(poison))
        assert result.returncode == 64, poison
        assert "ambient credential selector" in result.stderr
    codex_harness = (
        'pack_ref="codex-native-pack@1"\n' + ambient + cross + 'printf "ADMITTED\\n"\n'
    )
    result = _run_shell_fragment(codex_harness, {"CLAUDE_CREDENTIAL_GENERATION": "1"})
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
    admitted = 'printf "ADMITTED\\n"\n'
    result = _run_shell_fragment(preamble + claude_case + admitted, {})
    assert result.returncode == 0
    # A wrong home or config dir fails closed instead of staging to an
    # unattested path (including stale-cache-style nested homes).
    for poison in (
        {"CLAUDE_HOME": "/home/app/.cache/stale/.claude"},
        {"CLAUDE_HOME": "/home/app/.codex"},
        {"CLAUDE_CONFIG_DIR": "/tmp/evil-claude"},
    ):
        result = _run_shell_fragment(preamble + claude_case + admitted, dict(poison))
        assert result.returncode == 64, poison
    codex_case = _startup_layout_case("codex")
    assert "unexpected Codex credential layout" in codex_case
    codex_preamble = 'credential_root="${CODEX_HOME:-/home/app/.codex}"\n'
    result = _run_shell_fragment(codex_preamble + codex_case + admitted, {})
    assert result.returncode == 0
    result = _run_shell_fragment(
        codex_preamble + codex_case + admitted,
        {"CODEX_HOME": "/home/app/.cache/stale/.codex"},
    )
    assert result.returncode == 64


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


@pytest.fixture
def gh_config_home(request):
    """A disposable config home under the entrypoint's approved cache prefix.

    The packaged entrypoint only admits a GitHub config home under
    ``/home/app/.cache/*``. Runners without that writable layout (for example
    GitHub-hosted CI) cannot execute the token-staging block end to end.
    """

    cache = Path("/home/app/.cache")
    if not (cache.is_dir() and os.access(cache, os.W_OK)):
        pytest.skip("requires writable /home/app/.cache host layout")
    home = cache / f"mm-gh-token-{request.node.name[:40]}-{os.getpid()}"
    (home / "gh").mkdir(parents=True, exist_ok=True)
    yield home
    shutil.rmtree(home, ignore_errors=True)


def _run_github_token_block(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return _run_shell_fragment(_github_token_block(), {"HOME": "/home/app", **env})


def test_github_token_block_writes_restart_preserving_and_rejects(
    gh_config_home: Path,
) -> None:
    block = _github_token_block()
    # No deletion path: a restart without a token cannot clear persisted auth.
    assert "rm " not in block
    assert "hosts.yml" in block
    assert "unset github_token GH_TOKEN" in block
    hosts = gh_config_home / "gh" / "hosts.yml"
    config_home = {"XDG_CONFIG_HOME": str(gh_config_home)}

    result = _run_github_token_block({**config_home, "GH_TOKEN": "Abc_123"})
    assert result.returncode == 0, result.stderr
    assert "oauth_token: Abc_123" in hosts.read_text()

    # Restart without a new token preserves the persisted config.
    sentinel = "github.com:\n    oauth_token: SENTINEL_PRESERVED\n"
    hosts.write_text(sentinel)
    result = _run_github_token_block(config_home)
    assert result.returncode == 0, result.stderr
    assert hosts.read_text() == sentinel

    # Unsupported characters are rejected before any write.
    hosts.unlink()
    result = _run_github_token_block({**config_home, "GH_TOKEN": "bad-token!"})
    assert result.returncode != 0
    assert not hosts.exists()

    # Config homes outside the approved cache prefix fail closed.
    result = _run_github_token_block(
        {"XDG_CONFIG_HOME": "/tmp/evil-gh-config", "GH_TOKEN": "Abc_123"}
    )
    assert result.returncode != 0


def test_github_token_block_rotates_and_never_clobbers_with_invalid_token(
    gh_config_home: Path,
) -> None:
    hosts = gh_config_home / "gh" / "hosts.yml"
    config_home = {"XDG_CONFIG_HOME": str(gh_config_home)}
    result = _run_github_token_block({**config_home, "GH_TOKEN": "First_1"})
    assert result.returncode == 0, result.stderr
    assert "oauth_token: First_1" in hosts.read_text()
    result = _run_github_token_block({**config_home, "GH_TOKEN": "Second_2"})
    assert result.returncode == 0, result.stderr
    assert "oauth_token: Second_2" in hosts.read_text()
    before = hosts.read_text()
    result = _run_github_token_block({**config_home, "GH_TOKEN": "bad-token!"})
    assert result.returncode != 0
    assert hosts.read_text() == before


def test_github_token_block_preserves_unrelated_config_contents(
    gh_config_home: Path,
) -> None:
    gh_dir = gh_config_home / "gh"
    gh_dir.mkdir(parents=True, exist_ok=True)
    (gh_dir / "hosts.yml").write_text("github.com:\n    oauth_token: STALE_1\n")
    (gh_dir / "config.yml").write_text("editor: vim\n")
    before_hosts = (gh_dir / "hosts.yml").read_text()
    config_home = {"XDG_CONFIG_HOME": str(gh_config_home)}
    # Restart without a token preserves every persisted credential. The one
    # config.yml write it may make adds the gh schema version this projection
    # already satisfies, so gh never runs its network-dependent multi-account
    # migration, and it keeps the operator's own settings.
    result = _run_github_token_block(config_home)
    assert result.returncode == 0, result.stderr
    assert (gh_dir / "hosts.yml").read_text() == before_hosts
    repaired_config = (gh_dir / "config.yml").read_text()
    assert "editor: vim" in repaired_config
    assert 'version: "1"' in repaired_config
    # A rejected token preserves the previous connection and its neighbors.
    result = _run_github_token_block({**config_home, "GH_TOKEN": "bad-token!"})
    assert result.returncode != 0
    assert (gh_dir / "hosts.yml").read_text() == before_hosts
    assert (gh_dir / "config.yml").read_text() == repaired_config
