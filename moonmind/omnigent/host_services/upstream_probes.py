"""Retained in-host probes for behavior the Omnigent host tunnel does not serve.

Exact-host attestation prefers the supported tunnel endpoint
``GET /v1/hosts/{id}/harnesses/{harness}/model-options``. Three facts it
needs have no supported upstream surface yet, so this module is the one place
that runs upstream helpers inside the admitted host image:

- the OpenCode CLI catalog: the tunnel answers ``opencode-native`` as an
  unsupported harness;
- whether Claude Code serves a canonical ``claude-`` id that no catalog row
  spells exactly: the tunnel returns rows but no served verdict;
- the runner environment: Omnigent exposes no command that executes under the
  environment ``HostConnect`` gives a runner.

Each probe returns one structured result to its caller and fails typed when
the expected helper is absent (``OMNIGENT_HARNESS_BUILD_MISMATCH``), so a
changed upstream layout is reported as the missing behavior it is.

Removal: delete the matching probe when the host tunnel serves an
``opencode-native`` catalog, returns a served verdict for a requested model,
or offers runner-environment execution. The pre-0.13 module locations in the
layout selectors can go once no admitted host runs Omnigent older than 0.13.
"""

from __future__ import annotations

import json
from typing import Any

from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.host_services.docker_backend import DockerCommandBackend

# Select the layout before importing: a broken helper or dependency in the
# selected layout must fail attestation, never fall back to another layout.
_OPENCODE_APP_SERVER_IMPORT = (
    "import importlib, importlib.util; "
    "opencode_app_server = importlib.import_module("
    "'omnigent.harnesses.opencode_native.app_server' "
    "if importlib.util.find_spec('omnigent.harnesses') is not None "
    "else 'omnigent.opencode_native_app_server'); "
)
_CLAUDE_NATIVE_IMPORT = (
    "import importlib, importlib.util; "
    "claude_native = importlib.import_module("
    "'omnigent.harnesses.claude_native.main' "
    "if importlib.util.find_spec('omnigent.harnesses') is not None "
    "and importlib.util.find_spec('omnigent.harnesses.claude_native') is not None "
    "else 'omnigent.claude_native'); "
)
# Reserved exit status meaning "the attestation helper itself is unavailable in
# the host image". The wrapper pairs it with the marker line below; only that
# pair is remapped, so a probed command that happens to exit 97 keeps its own
# authoritative contract status.
_PROBE_SUBSTRATE_UNAVAILABLE_EXIT_CODE = 97
_PROBE_SUBSTRATE_UNAVAILABLE_MARKER = "moonmind-attestation-substrate-unavailable"


def runner_env_statement(runner_id: str) -> str:
    """Return Python statements binding ``env`` to the stock runner environment.

    ``HostConnect._handle_launch`` builds this environment immediately before
    it starts a runner, so a probe run under it sees what a session will see.
    """

    return (
        "import os; "
        "from omnigent.host.connect import _build_runner_env; "
        "env = _build_runner_env(os.environ, "
        "server_url=os.environ.get('OMNIGENT_SERVER_URL', ''), "
        f"runner_id={runner_id!r}, binding_token={runner_id!r}, "
        "workspace='/workspaces/run', parent_pid=os.getpid()); "
    )


def substrate_guarded_probe(body: str) -> str:
    """Wrap one line of in-host probe statements so helper drift fails typed.

    A missing module, renamed helper, or changed helper signature exits with
    the reserved status and a marker line naming the exception. The probed
    command's own exit status still propagates unchanged.
    """

    return (
        "import sys\n"
        "try:\n"
        f"    {body}\n"
        "except (ImportError, AttributeError, TypeError) as exc:\n"
        f"    sys.stderr.write({_PROBE_SUBSTRATE_UNAVAILABLE_MARKER!r} + ': ' "
        "+ type(exc).__name__ + ': ' + str(exc) + '\\n')\n"
        f"    raise SystemExit({_PROBE_SUBSTRATE_UNAVAILABLE_EXIT_CODE})\n"
    )


def raise_if_probe_substrate_unavailable(
    code: int, stderr: str, *, boundary: str
) -> None:
    """Translate the reserved probe status into an actionable build mismatch."""

    if code != _PROBE_SUBSTRATE_UNAVAILABLE_EXIT_CODE:
        return
    marker_line = next(
        (
            line
            for line in stderr.splitlines()
            if line.startswith(_PROBE_SUBSTRATE_UNAVAILABLE_MARKER)
        ),
        None,
    )
    if marker_line is None:
        # The reserved status without the wrapper's marker is the probed
        # command's own exit status: authoritative contract evidence, not a
        # substrate fault.
        return
    detail = marker_line[len(_PROBE_SUBSTRATE_UNAVAILABLE_MARKER) :].lstrip(": ")
    detail = detail.strip()[:200]
    message = f"{boundary} attestation helper is unavailable in the exact host image"
    if detail:
        message = f"{message}: {detail}"
    raise HarnessPlatformError(
        message, code=HarnessPlatformFailure.OMNIGENT_HARNESS_BUILD_MISMATCH
    )


def _catalog_unavailable(message: str) -> HarnessPlatformError:
    # A failed read is an unavailable observation, never proof of absence.
    return HarnessPlatformError(
        message,
        code=HarnessPlatformFailure.OMNIGENT_HARNESS_CATALOG_UNAVAILABLE,
        transient=True,
    )


async def read_opencode_model_options(
    *, backend: DockerCommandBackend, container_name: str
) -> dict[str, Any]:
    """Read the OpenCode CLI catalog with the host's own portable helper.

    The selected host already owns the exact image, environment, egress policy,
    and materialized credential that helper must inspect.
    """

    probe = substrate_guarded_probe(
        "import json; " + _OPENCODE_APP_SERVER_IMPORT + "print(json.dumps({'models': "
        "opencode_app_server.list_opencode_cli_model_options()}))"
    )
    code, stdout, stderr = await backend.run(
        ["docker", "exec", container_name, "/opt/venv/bin/python", "-c", probe],
        timeout_seconds=45.0,
        check=False,
    )
    raise_if_probe_substrate_unavailable(
        code, stderr, boundary="OpenCode model catalog"
    )
    if code != 0:
        # Provider CLI diagnostics can include credential-sensitive context.
        # Exact-host evidence needs only the typed failure, never raw output.
        raise _catalog_unavailable("exact host OpenCode model catalog probe failed")
    try:
        payload = json.loads(stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise _catalog_unavailable(
            "exact host OpenCode model catalog probe returned invalid JSON"
        ) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise _catalog_unavailable(
            "exact host OpenCode model catalog probe returned an invalid catalog"
        )
    return payload


async def claude_serves_model(
    *,
    backend: DockerCommandBackend,
    container_name: str,
    rows: list[dict[str, Any]],
    selected_model: str,
) -> bool:
    """Ask the exact host's Claude harness whether it serves ``selected_model``."""

    probe = substrate_guarded_probe(
        "import json, sys; "
        + _CLAUDE_NATIVE_IMPORT
        + "rows = json.loads(sys.argv[1]); "
        "config = claude_native.resolve_native_claude_config(spec=None); "
        "print('served' if claude_native.claude_catalog_serves_model("
        "rows, sys.argv[2], config) else 'unserved')"
    )
    code, stdout, stderr = await backend.run(
        [
            "docker",
            "exec",
            container_name,
            "/opt/venv/bin/python",
            "-c",
            probe,
            json.dumps(rows),
            selected_model,
        ],
        timeout_seconds=45.0,
        check=False,
    )
    raise_if_probe_substrate_unavailable(code, stderr, boundary="Claude model catalog")
    if code != 0:
        raise _catalog_unavailable("exact host Claude model catalog probe failed")
    return stdout.strip() == "served"


async def run_runner_command(
    *,
    backend: DockerCommandBackend,
    container_name: str,
    argv: list[str],
    timeout_seconds: float = 30.0,
) -> tuple[int, str, str]:
    """Execute a probe through the stock host's runner environment builder."""

    runner_probe = substrate_guarded_probe(
        "import subprocess, sys; "
        + runner_env_statement("moonmind-attestation")
        + "result = subprocess.run(sys.argv[1:], env=env, text=True, "
        "capture_output=True); "
        "sys.stdout.write(result.stdout); sys.stderr.write(result.stderr); "
        "raise SystemExit(result.returncode)"
    )
    code, stdout, stderr = await backend.run(
        [
            "docker",
            "exec",
            container_name,
            "/opt/venv/bin/python",
            "-c",
            runner_probe,
            *argv,
        ],
        timeout_seconds=timeout_seconds,
        check=False,
    )
    raise_if_probe_substrate_unavailable(code, stderr, boundary="runner environment")
    return code, stdout, stderr


async def run_opencode_shell_command(
    *,
    backend: DockerCommandBackend,
    container_name: str,
    argv: list[str],
    timeout_seconds: float = 30.0,
) -> tuple[int, str, str]:
    """Execute through the runner, OpenCode filter, and MoonMind context shim."""

    filtered_child_probe = substrate_guarded_probe(
        "import subprocess, sys; from pathlib import Path; "
        + _OPENCODE_APP_SERVER_IMPORT
        + "env = opencode_app_server.filtered_server_env("
        "bridge_dir=Path('/tmp/moonmind-opencode-attestation'), "
        "auth_secret='moonmind-attestation'); "
        "result = subprocess.run("
        "['/home/app/.omnigent/moonmind/bin/moonmind-context', "
        "*sys.argv[1:]], "
        "env=env, text=True, capture_output=True); "
        "sys.stdout.write(result.stdout); sys.stderr.write(result.stderr); "
        "raise SystemExit(result.returncode)"
    )
    opencode_probe = substrate_guarded_probe(
        "import subprocess, sys; "
        + runner_env_statement("moonmind-attestation")
        + f"child = {filtered_child_probe!r}; "
        "result = subprocess.run([sys.executable, '-c', child, *sys.argv[1:]], "
        "env=env, text=True, "
        "capture_output=True); sys.stdout.write(result.stdout); "
        "sys.stderr.write(result.stderr); raise SystemExit(result.returncode)"
    )
    code, stdout, stderr = await backend.run(
        [
            "docker",
            "exec",
            container_name,
            "/opt/venv/bin/python",
            "-c",
            opencode_probe,
            *argv,
        ],
        timeout_seconds=timeout_seconds,
        check=False,
    )
    raise_if_probe_substrate_unavailable(
        code, stderr, boundary="OpenCode shell environment"
    )
    return code, stdout, stderr
