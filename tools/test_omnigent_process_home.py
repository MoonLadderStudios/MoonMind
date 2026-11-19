#!/usr/bin/env python3
"""Exercise generated MoonMind startup against an actual Omnigent checkout.

Run with the runtime's Python (including Omnigent dependencies), for example:
    python tools/test_omnigent_process_home.py --upstream omnigent
No provider calls, Docker, credentials, or browser are required. This checks
parser/restore compatibility and real OS-helper execution, not provider output.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify(upstream: Path, python: Path, *, api_key: bool = False) -> None:
    from moonmind.omnigent.host_services.runtime_scripts import (
        OmnigentRuntimeScriptService,
    )

    if not (upstream / "omnigent/inner/os_env.py").is_file():
        raise ValueError(
            "initialize the Omnigent checkout before this conformance test"
        )
    configuration = {
        "homePathOverrides": {"HOME": "{{runtime_support_dir}}/process-home"},
        "fileTemplates": [
            {
                "path": "{{runtime_support_dir}}/process-home/NOTES.md",
                "format": "text",
                "permissions": "0600",
                "merge_strategy": "replace",
                "content_template": "Admitted notes for {{workspace_path}}.\n",
            }
        ],
    }
    with tempfile.TemporaryDirectory(prefix="moonmind-process-home-") as directory:
        root = Path(directory)
        for sibling in ("one", "two"):
            runner_home, skills, binaries = (
                root / sibling / name for name in ("runner", "skills", "bin")
            )
            for path in (runner_home, skills, binaries):
                path.mkdir(parents=True)
            (binaries / "python3").symlink_to(python)
            (binaries / "claude").write_text('#!/bin/sh\nprintf "%s\\n" "$HOME" "$CLAUDE_CONFIG_DIR"; cat "$HOME/NOTES.md"\n')
            (binaries / "claude").chmod(0o700)
            private = runner_home / ".omnigent/moonmind/runtime-context/process-home"
            config_home = runner_home / ".omnigent/moonmind/runtime-context/claude-home" if api_key else runner_home / ".claude"
            if api_key:
                projection = runner_home / ".moonmind-provider-config"
                projection.mkdir()
                (projection / ".moonmind-generation").write_text("3\n")
                config = {"homePathOverrides": {**configuration["homePathOverrides"], "CLAUDE_CONFIG_DIR": "{{runtime_support_dir}}/claude-home"}, "fileTemplates": configuration["fileTemplates"]}
                (projection / "claude-profile.json").write_text(json.dumps(config))
            workspace = "/work/" + sibling
            expected = str(private) + "\nAdmitted notes for " + workspace + ".\n"
            runner = binaries / "omnigent"
            runner.write_text(f"""#!{python}
import asyncio, dataclasses, json, os, subprocess
from omnigent.inner.terminal import terminal_pane_environment
from omnigent.inner.loader import _parse_os_env_spec
from omnigent.inner.os_env import create_os_environment
from omnigent.spec.parser import _parse_os_env
original = os.environ['HOME']
assert os.environ['CLAUDE_CONFIG_DIR'] == {str(config_home)!r}
spec = _parse_os_env_spec(dataclasses.asdict(_parse_os_env({{'process_home': {str(private)!r}, 'sandbox': {{'type': 'none'}}}})))
for _ in range(2):
    environment = create_os_environment(spec)
    try:
        observed = asyncio.run(environment.shell('printf "%s\\\\n" "$HOME"; cat "$HOME/NOTES.md"'))
        assert observed['stdout'] == {expected!r}, observed
    finally:
        environment.close()
terminal_env = terminal_pane_environment(inherit_env=True, env={{}}, env_unset=[], process_home={str(private)!r})
cli = subprocess.run([{str(runner_home / '.omnigent/moonmind/bin/claude')!r}], env=terminal_env, capture_output=True, text=True, check=True)
assert cli.stdout == {str(private) + chr(10) + str(config_home) + chr(10) + 'Admitted notes for ' + workspace + '.' + chr(10)!r}, cli
assert os.environ['HOME'] == original
print(json.dumps({{'home': {str(private)!r}, 'runnerHome': original}}))
""")
            runner.chmod(0o700)
            script, environment = OmnigentRuntimeScriptService().build_entrypoint(
                credential_handles=[
                    {
                        "materializerRef": "omnigent-provider-config@1" if api_key else "claude-oauth-home@1",
                        **({"runtimeEnvironment": {"MOONMIND_CLAUDE_PROFILE_CONFIG_FILE": "/home/app/.moonmind-provider-config/claude-profile.json", "OMNIGENT_CONFIG_HOME": "/home/app/.moonmind-provider-config"}} if api_key else {}),
                        "credentialGeneration": 3,
                        "attachments": [],
                        "claudeProcessHomeConfiguration": configuration,
                    }
                ],
                skill_attachment={"targetPath": str(skills)},
                step_execution_id="step:" + sibling,
                workspace_path=workspace,
                enable_claude_runtime=True,
            )
            assert "HOME" not in environment
            script = script.replace("/home/app", str(runner_home)).replace(
                "/opt/venv/bin/python", str(python)
            )
            environment = {
                key: value.replace("/home/app", str(runner_home))
                for key, value in environment.items()
            }
            result = subprocess.run(
                check=False,
                args=["/bin/sh", "-ceu", script, "--", "http://unused.invalid"],
                env={
                    **os.environ,
                    **environment,
                    "HOME": str(runner_home),
                    "PATH": f"{binaries}:/usr/bin:/bin",
                    "PYTHONPATH": str(upstream),
                },
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode:
                raise RuntimeError(result.stderr)
            assert json.loads(result.stdout) == {
                "home": str(private),
                "runnerHome": str(runner_home),
            }
            assert private.stat().st_mode & 0o777 == 0o700
            assert (private / "NOTES.md").stat().st_mode & 0o777 == 0o600
    print(
        "PASS: actual parser/restore and helper restart preserve private HOME for two siblings; runner HOME and Claude selector remain unchanged"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, default=ROOT / "omnigent")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--api-key", action="store_true")
    args = parser.parse_args()
    verify(args.upstream.resolve(), args.python.absolute(), api_key=args.api_key)


if __name__ == "__main__":
    main()
