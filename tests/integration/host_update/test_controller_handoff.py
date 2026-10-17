"""Controller handoff through real Docker: the requested release's controller
takes over an open operation, and a replacement that cannot run is reverted."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]
HANDOFF_PREFIX = "MOONMIND_CONTROLLER_HANDOFF="
# Records a staged operation inside the controller, as a submission does.
OPEN_OPERATION = (
    "import sys; sys.path.insert(0,'/app/deploy/controller'); import record; "
    "store=record.OperationStore('/var/lib/moonmind-controller'); "
    "store.begin(stack='moonmind', desired_image=sys.argv[2], source_revision='', "
    "operation_id=sys.argv[1], target={'project': sys.argv[3]}); "
    "store.mark_stage(sys.argv[1], stage='staged')"
)
# Holds the stack lock the way an applying controller does while it waits.
HOLD_STACK_LOCK = (
    "import fcntl,time; f=open('/var/lib/moonmind-controller/locks/moonmind.lock','a+b'); "
    "fcntl.flock(f, fcntl.LOCK_EX); time.sleep(600)"
)
pytestmark = [pytest.mark.integration]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(args, *, check=True, timeout=180):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def build(tag, base, tmp_path, *lines):
    context = tmp_path / tag.replace(":", "-").replace("/", "-")
    context.mkdir()
    (context / "Dockerfile").write_text("\n".join([f"FROM {base}", *lines]) + "\n")
    command(["docker", "build", "--network=none", "-t", tag, str(context)], timeout=600)
    return command(["docker", "image", "inspect", "--format", "{{.Id}}", tag])


def test_requested_release_controller_resumes_its_operation(tmp_path):
    if os.environ.get("MOONMIND_TEST_JOURNAL_TRANSITION") != "1":
        pytest.skip("Real Docker journey is enabled in controller-journey CI")
    candidate = os.environ["MOONMIND_TEST_JOURNAL_APP_IMAGE"]
    candidate_id = command(
        ["docker", "image", "inspect", "--format", "{{.Id}}", candidate]
    )
    project = f"moonmind-test-handoff-{uuid4().hex[:10]}"
    previous = f"{project}-previous:local"
    broken = f"{project}-broken:local"
    network = f"{project}_deployment-controller-network"
    previous_id = build(
        previous, candidate, tmp_path, f"LABEL moonmind.test.previous={project}"
    )
    broken_id = build(
        broken,
        candidate,
        tmp_path,
        "RUN printf 'raise SystemExit(3)\\n' > /app/deploy/controller/server.py",
    )
    repo = tmp_path / "deployment"
    state = repo / "deploy/state/controller"
    state.mkdir(parents=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    bootstrap = load("handoff_bootstrap", ROOT / "deploy/controller/bootstrap.py")
    secret = bootstrap.ensure_secret(state).read_text().strip()
    identity = bootstrap.ensure_identity(
        state, repo, port, target_project=project, target_network=network
    )
    compose_file = bootstrap.render_compose_file(
        state_dir=state,
        repo=repo,
        image=previous_id,
        port=port,
        project=identity["project"],
        target_project=project,
        target_network=network,
    )
    bootstrap.record_controller_image(state, requested=previous, pinned=previous_id)
    compose = [
        "docker",
        "compose",
        "--project-name",
        identity["project"],
        "-f",
        str(compose_file),
    ]

    def container():
        return command([*compose, "ps", "-q", "controller"], check=False)

    def running_image():
        return command(["docker", "inspect", "--format", "{{.Image}}", container()])

    def operation(operation_id):
        return json.loads(
            command(
                [
                    "docker",
                    "exec",
                    container(),
                    "cat",
                    f"/var/lib/moonmind-controller/operations/{operation_id}.json",
                ]
            )
        )

    def capabilities():
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/healthz",
            headers={"Authorization": f"Bearer {secret}"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=5) as response:
            return json.load(response)["capabilities"]

    def open_operation(operation_id):
        # A pre-apply refusal is the visible proof a controller resumed it.
        command(
            [
                "docker",
                "exec",
                container(),
                "python",
                "-c",
                OPEN_OPERATION,
                operation_id,
                candidate,
                project,
            ]
        )
        # The submitting controller holds the stack lock while it waits on
        # the handoff one-off, exactly as an apply does.
        command(
            [
                "docker",
                "exec",
                "--detach",
                container(),
                "python",
                "-c",
                HOLD_STACK_LOCK,
            ]
        )

    def handoff(operation_id, image):
        # The controller's release step: a labeled one-off of the requested
        # image with the Docker socket and the deployment state.
        output = command(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "0:0",
                "--label",
                f"com.docker.compose.project={project}",
                "--label",
                "com.docker.compose.service=temporal-worker-deployment-control",
                "--label",
                "com.docker.compose.oneoff=True",
                "--label",
                f"moonmind.controller.omnigent.operation={operation_id}",
                "--mount",
                "type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock",
                "--mount",
                f"type=bind,source={state},target=/workspace/deployment_state/controller",
                "--env",
                "MOONMIND_CONTROLLER_STATE_DIR=/workspace/deployment_state/controller",
                "--entrypoint",
                "python",
                candidate,
                "/app/deploy/controller/bootstrap.py",
                "handoff",
                "--state-dir",
                "/workspace/deployment_state/controller",
                "--operation",
                operation_id,
                "--image",
                image,
            ],
            timeout=300,
        )
        receipts = [
            line for line in output.splitlines() if line.startswith(HANDOFF_PREFIX)
        ]
        return json.loads(receipts[-1][len(HANDOFF_PREFIX) :])

    def eventually(check, timeout=90):
        deadline = time.monotonic() + timeout
        while True:
            try:
                if check():
                    return
            except (AssertionError, OSError, ValueError):
                # The replacement controller may still be starting.
                pass
            assert time.monotonic() < deadline, "condition never held"
            time.sleep(2)

    command(["docker", "network", "create", network])
    try:
        command([*compose, "up", "-d", "--pull", "never", "--wait"])
        eventually(lambda: "controller-handoff" in capabilities())
        assert running_image() == previous_id

        open_operation("ui-handed-off")
        old = container()
        receipt = handoff("ui-handed-off", candidate)
        assert receipt == {"status": "handed-off", "image": candidate_id}
        assert container() != old and running_image() == candidate_id
        assert bootstrap.load_controller_image(state)["pinned"] == candidate_id
        # The new controller waited for the one-off, then resumed the same
        # operation under the lock the replaced process released.
        eventually(lambda: operation("ui-handed-off")["attempts"])
        resumed = operation("ui-handed-off")
        assert resumed["controllerHandoff"]["from"] == previous_id
        assert "config" in resumed["attempts"][-1]["error"]
        eventually(lambda: "controller-handoff" in capabilities())
        assert handoff("ui-handed-off", candidate) == {
            "status": "current",
            "image": candidate_id,
        }

        # A replacement that cannot run is reverted; the previous controller
        # resumes the operation itself instead of leaving no owner.
        command(
            [
                "docker",
                "exec",
                container(),
                "rm",
                "-f",
                "/var/lib/moonmind-controller/operations/ui-handed-off.json",
            ]
        )
        open_operation("ui-reverted")
        receipt = handoff("ui-reverted", broken)
        assert receipt["status"] == "unavailable"
        assert "previous controller restored" in receipt["reason"]
        assert running_image() == candidate_id
        assert bootstrap.load_controller_image(state)["pinned"] == candidate_id
        eventually(lambda: operation("ui-reverted")["attempts"])
        assert operation("ui-reverted")["controllerHandoff"]["image"] == broken_id
        eventually(lambda: "controller-handoff" in capabilities())
    finally:
        # Only this disposable test project and its fixture images are removed.
        if container():
            command(
                [
                    "docker",
                    "exec",
                    container(),
                    "chown",
                    "-R",
                    f"{os.getuid()}:{os.getgid()}",
                    "/var/lib/moonmind-controller",
                ],
                check=False,
            )
        command([*compose, "down", "--remove-orphans"], check=False)
        command(["docker", "network", "rm", network], check=False)
        command(["docker", "image", "rm", previous, broken], check=False)
