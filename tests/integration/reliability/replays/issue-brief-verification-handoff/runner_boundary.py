"""Exercise the projected CLI; executable inside a pinned Omnigent host image.

The native entrypoint uses the actual host and Codex environment builders.
The unit caller supplies an empty child environment to test the stricter case.
No provider is contacted; the local HTTP fixture records container submission.
"""

from __future__ import annotations

import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch


def exercise_projection(root: Path, *, native: bool = False) -> None:
    repository = next(
        parent for parent in Path(__file__).parents if (parent / "moonmind").is_dir()
    )
    # The host deliberately has only Omnigent dependencies, so load this pure
    # script builder without importing MoonMind's application service package.
    module_spec = importlib.util.spec_from_file_location(
        "moonmind_runtime_scripts",
        repository / "moonmind/omnigent/host_services/runtime_scripts.py",
    )
    runtime_scripts = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(runtime_scripts)
    home = root / "home"
    skills = root / "skills"
    tools = root / "tools"
    control = root / "control"
    runtime_bin = home / ".omnigent/moonmind/bin"
    for directory in (home, skills, tools / "bin", control, runtime_bin):
        directory.mkdir(parents=True, exist_ok=True)
    # This is an inert host-launch sentinel, not a replacement runner.
    (runtime_bin / "omnigent").write_text("#!/bin/sh\nexit 0\n")
    (runtime_bin / "omnigent").chmod(0o700)
    shutil.copyfile(
        repository / "services/omnigent/scripts/moonmind-container-cli.py",
        tools / "bin/moonmind",
    )
    (tools / "bin/moonmind").chmod(0o555)
    (control / "container-jobs").write_text("fixture-scoped-capability")
    (control / "container-jobs").chmod(0o400)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert self.headers["Authorization"] == "Bearer fixture-scoped-capability"
            requests.append(request)
            if request["tool"] == "container.submit":
                result = {"jobId": "job-1"}
            elif request["tool"] == "container.status":
                result = {"state": "succeeded", "terminal": {"exitCode": 0}}
            else:
                result = {"items": [], "nextCursor": None}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"result": result}).encode())

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/mcp/container"
    expected = {
        "MOONMIND_URL": endpoint.removesuffix("/mcp/container"),
        "MOONMIND_AGENT_RUN_ID": "agent-run-1",
        "MOONMIND_TASK_WORKFLOW_ID": "workflow-1",
        "MOONMIND_STEP_ID": "step-1",
        "MOONMIND_RUNTIME_ID": "codex-native",
        "MOONMIND_CONTAINER_JOBS_BEARER_TOKEN_FILE": str(control / "container-jobs"),
        "MOONMIND_CONTAINER_JOBS_MCP_URL": endpoint,
        "MOONMIND_CONTAINER_JOBS_SOURCE_KIND": "omnigent",
        "MOONMIND_CONTAINER_JOBS_SESSION_ID": "lease-1",
        "MOONMIND_CONTAINER_JOBS_WORKSPACE_KIND": "sandbox",
        "MOONMIND_CONTAINER_JOBS_WORKSPACE_ID": "sandbox-1",
        "MOONMIND_CONTAINER_JOBS_WORKSPACE_RELATIVE_PATH": "repo",
    }
    (
        script,
        environment,
    ) = runtime_scripts.OmnigentRuntimeScriptService().build_entrypoint(
        credential_handles=[],
        skill_attachment={"targetPath": str(skills)},
        step_execution_id="workflow-1:run-1:node-1:execution:1",
        tool_attachments=(
            {
                "targetPath": str(tools),
                "tools": [{"name": "docker", "path": "bin/moonmind"}],
            },
        ),
        runtime_environment=expected,
    )
    script = script.replace("/home/app", str(home)).replace(
        "/opt/moonmind-tools", str(tools)
    )
    environment = {
        key: value.replace("/home/app", str(home)) for key, value in environment.items()
    }
    environment["HOME"] = str(home)
    try:
        setup = subprocess.run(
            ["/bin/sh", "-ceu", script, "--", "http://fixture"],
            env=environment,
            capture_output=True,
            text=True,
        )
        assert setup.returncode == 0, setup.stderr
        if native:
            from omnigent.host.connect import _build_runner_env
            from omnigent.inner.codex_executor import _clean_codex_env

            runner_environment = _build_runner_env(
                environment,
                server_url="http://fixture",
                runner_id="fixture",
                binding_token="fixture",
                workspace=str(root),
                parent_pid=os.getpid(),
            )
            with patch.dict(os.environ, runner_environment, clear=True):
                child_environment = _clean_codex_env()
        else:
            child_environment = {"HOME": str(home), "PATH": environment["PATH"]}
        assert not any(key.startswith("MOONMIND_") for key in child_environment)
        # Login commands recover both correlation and capability context.
        # The CLI wrapper also works when an explicit non-login shell is used.
        for shell_args in (["/bin/bash", "-lc"], ["/bin/sh", "-c"]):
            command = "moonmind container python-tests tests/unit/test_pytest_unit_workflow.py"
            if shell_args[-1] == "-lc":
                command = 'test "$MOONMIND_RUNTIME_ID" = codex-native && ' + command
            result = subprocess.run(
                [*shell_args, command],
                env=child_environment,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
        submissions = [
            item["arguments"] for item in requests if item["tool"] == "container.submit"
        ]
        assert len(submissions) == 2
        for submission in submissions:
            assert submission["source"]["stepId"] == "step-1"
            assert submission["source"]["workflowId"] == "workflow-1"
            assert submission["spec"]["workspaceRef"] == {
                "kind": "sandbox",
                "workspaceId": "sandbox-1",
                "relativePath": "repo",
            }
        for artifact in (home / ".omnigent/moonmind/runtime-context").iterdir():
            assert "fixture-scoped-capability" not in artifact.read_text()
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="moonmind-native-shell-") as directory:
        exercise_projection(Path(directory), native=True)
    print("Native Codex runner â†’ filtered child â†’ projected CLI boundary passed")
