"""`gh` in a MoonMind host must never need a network config migration.

GitHub CLI 2.40+ migrates an unversioned ``config.yml`` on *every* invocation
and resolves the account name from ``api.github.com`` to do it. MoonMind
projects ``hosts.yml`` without that version marker, so a blocked, throttled, or
merely flaky provider call made ``gh --version`` exit 1 inside an otherwise
healthy host. Mounted-tool attestation reported that as
``OMNIGENT_HARNESS_BUILD_MISMATCH`` with ``align_host_build``, a remediation
that can never repair a credential projection.

Two invariants keep that from recurring:

1. every gh config projection declares the schema version it already
   satisfies, so no gh command depends on a provider round trip;
2. a mounted-tool version probe attests the *build* and therefore runs with a
   cleared environment, so operator configuration cannot fail a build check.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from moonmind.omnigent.host_services.attestation import mounted_tool_probe_argv
from moonmind.omnigent.host_services.runtime_scripts import OmnigentRuntimeScriptService

REPO_ROOT = Path(__file__).resolve().parents[3]
STATIC_HOST_SCRIPT = REPO_ROOT / "services/omnigent/scripts/start-omnigent-host.sh"
GH_CONFIG_DIR = "/home/app/.config/gh"


def _generic_host_entrypoint() -> str:
    script, _environment = OmnigentRuntimeScriptService().build_entrypoint(
        credential_handles=[
            {
                "credentialGeneration": 3,
                "attachments": [{"targetPath": "/run/mm-credentials/opencode"}],
            }
        ],
        skill_attachment={"targetPath": "/opt/moonmind-skills"},
        step_execution_id="workflow:run:node-1:execution:1",
        github_credential_attachment={"targetPath": "/run/mm-credentials/github"},
    )
    return script


def _static_host_github_block() -> str:
    lines = STATIC_HOST_SCRIPT.read_text().splitlines()
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
    return "set -eu\n" + "\n".join(lines[start : end + 1]) + "\n"


def test_generic_host_projection_declares_the_gh_schema_version() -> None:
    """The on-demand host writes the marker beside the copied credential."""

    script = _generic_host_entrypoint()

    assert (
        f"cp /run/mm-credentials/github/hosts.yml {GH_CONFIG_DIR}/hosts.yml" in script
    )
    assert f"""printf 'version: "1"\\n' > {GH_CONFIG_DIR}/config.yml""" in script
    assert f"chmod 0600 {GH_CONFIG_DIR}/hosts.yml {GH_CONFIG_DIR}/config.yml" in script

    syntax = subprocess.run(
        ["/bin/sh", "-n"],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr


def test_static_host_block_carries_the_marker_write_everywhere() -> None:
    """CI runners cannot execute the block, so prove its shape unconditionally.

    The packaged entrypoint only admits a GitHub config home under
    ``/home/app/.cache``, which a hermetic runner does not have, so the
    end-to-end test below skips there. This keeps the static host's half of
    the invariant covered on every runner.
    """

    block = _static_host_github_block()

    assert 'printf \'version: "1"\\n\' > "$github_version_tmp"' in block
    # Guarded on the live credential, not on a supplied token, so a restart
    # that carries no new token still repairs a pre-marker projection.
    assert 'if [ -f "$github_config_dir/hosts.yml" ]; then' in block
    assert 'mv "$github_version_tmp" "$github_config_dir/config.yml"' in block
    # No deletion path may enter the block with the marker write.
    assert "rm " not in block

    syntax = subprocess.run(
        ["/bin/sh", "-n"],
        input=block,
        text=True,
        capture_output=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr


def test_static_host_projection_declares_the_gh_schema_version(
    tmp_path: Path,
) -> None:
    """The static host writes the marker for a supplied and a preserved token."""

    cache = Path("/home/app/.cache")
    if not (cache.is_dir() and os.access(cache, os.W_OK)):
        pytest.skip("requires writable static-host /home/app/.cache layout")

    block = tmp_path / "github-block.sh"
    block.write_text(_static_host_github_block())
    config_home = Path(f"/home/app/.cache/mm-gh-version-test-{os.getpid()}")
    config_dir = config_home / "gh"

    def run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh", str(block)],
            capture_output=True,
            text=True,
            check=False,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/home/app",
                "XDG_CONFIG_HOME": str(config_home),
                **env,
            },
        )

    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        result = run({"GH_TOKEN": "Abc_123"})
        assert result.returncode == 0, result.stderr
        assert (config_dir / "config.yml").read_text() == 'version: "1"\n'
        assert "oauth_token: Abc_123" in (config_dir / "hosts.yml").read_text()
        assert stat.S_IMODE((config_dir / "config.yml").stat().st_mode) == 0o600

        # A restart that carries no new token still repairs a projection that
        # predates the marker, because the persisted hosts.yml is still live.
        (config_dir / "config.yml").unlink()
        result = run({})
        assert result.returncode == 0, result.stderr
        assert (config_dir / "config.yml").read_text() == 'version: "1"\n'
    finally:
        for name in ("hosts.yml", "config.yml"):
            (config_dir / name).unlink(missing_ok=True)
        config_dir.rmdir()
        config_home.rmdir()


def test_version_probe_runs_without_the_host_runtime_environment(
    tmp_path: Path,
) -> None:
    """A build probe cannot inherit the credential configuration it must ignore."""

    probed = tmp_path / "bin/gh"
    probed.parent.mkdir(parents=True)
    probed.write_text(
        "#!/bin/sh\n"
        'test -z "${GH_CONFIG_DIR:-}" || { echo "leaked GH_CONFIG_DIR" >&2; exit 1; }\n'
        'test -z "${MOONMIND_STEP_EXECUTION_ID:-}" || exit 1\n'
        'echo "gh version 2.76.2 $1"\n'
    )
    probed.chmod(0o755)

    argv = mounted_tool_probe_argv("mm-host-1", str(probed), ["--version"])
    assert argv[:3] == ["docker", "exec", "mm-host-1"]
    assert argv[-2:] == [str(probed), "--version"]

    leaked = {
        **os.environ,
        "GH_CONFIG_DIR": GH_CONFIG_DIR,
        "MOONMIND_STEP_EXECUTION_ID": "workflow:run:node-1:execution:1",
    }
    completed = subprocess.run(
        argv[3:],
        capture_output=True,
        text=True,
        check=False,
        env=leaked,
    )

    assert completed.returncode == 0, completed.stderr
    assert "gh version 2.76.2 --version" in completed.stdout
