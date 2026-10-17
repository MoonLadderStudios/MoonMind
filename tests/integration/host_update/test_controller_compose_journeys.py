"""Real disposable-Compose repair journeys through the deploy/controller owner.

Each journey owns a ``moonmind-test-ctrl-*`` project, controller state and a
local registry, and submits through the controller's own WSGI application
with its production applier. No MoonMind API, worker, database, artifact
store or Docker proxy exists in the target, so every repair here runs with
the application unavailable. Only the seed images come from Docker Hub.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]
SEED_IMAGE = "busybox:1.36"
REGISTRY_IMAGE = "registry:2"
SECRET = "journey-secret"
STACK = "moonmind"
pytestmark = [pytest.mark.integration]


def docker(*args, check=True, timeout=180):
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture(scope="module")
def controller():
    if os.environ.get("MOONMIND_TEST_JOURNAL_TRANSITION") != "1":
        pytest.skip("Real Docker journey is enabled in controller-journey CI")
    for image in (SEED_IMAGE, REGISTRY_IMAGE):
        if docker("image", "inspect", image, check=False).returncode:
            docker("pull", image, timeout=300)
    sys.path.insert(0, str(ROOT / "deploy" / "controller"))
    import lock
    import record
    import server

    return {"lock": lock, "record": record, "server": server}


@pytest.fixture()
def journey(controller, tmp_path):
    """One registry, deployment checkout and controller state per journey."""
    server = controller["server"]
    registry = f"moonmind-test-registry-{uuid4().hex[:8]}"
    docker("run", "-d", "--name", registry, "-p", "127.0.0.1::5000", REGISTRY_IMAGE)
    host = docker("port", registry, "5000/tcp").stdout.strip().splitlines()[0]
    deadline = time.monotonic() + 30
    while True:
        try:
            with urllib.request.urlopen(f"http://{host}/v2/", timeout=2):
                break
        except OSError:
            assert time.monotonic() < deadline, "local registry never answered"
            time.sleep(0.5)
    project = f"moonmind-test-ctrl-{uuid4().hex[:8]}"
    repo = tmp_path / "deployment"
    repo.mkdir()
    store = controller["record"].OperationStore(tmp_path / "controller-state")
    built = []

    def publish(name, marker, *, keep_local=False):
        """Push a distinct image; drop the local tag so only a pull stages it."""
        ref = f"{host}/hermetic/{name}"
        context = tmp_path / f"image-{len(built)}"
        context.mkdir()
        (context / "Dockerfile").write_text(
            f"FROM {SEED_IMAGE}\nLABEL moonmind.test.marker={marker}\n"
        )
        docker(
            "build",
            "--pull=false",
            "--network=none",
            "-t",
            ref,
            str(context),
            timeout=300,
        )
        docker("push", ref, timeout=300)
        built.append(ref)
        if not keep_local:
            docker("rmi", ref)
        return ref

    def write_compose(*, init_db_exit=0):
        loop = ["sh", "-c", "while true; do sleep 1; done"]
        config = {
            "services": {
                "app": {
                    "image": "${MOONMIND_IMAGE:-" + f"{host}/hermetic/app:old" + "}",
                    "command": loop,
                    "stop_grace_period": "1s",
                    "environment": {"HERMETIC_SETTING": "${HERMETIC_SETTING:-lost}"},
                    "volumes": ["app-data:/data"],
                    "depends_on": {
                        "init-db": {"condition": "service_completed_successfully"}
                    },
                },
                "init-db": {
                    "image": f"{host}/hermetic/infra:1",
                    "command": ["sh", "-c", f"exit {init_db_exit}"],
                },
                "infra": {
                    "image": f"{host}/hermetic/infra:1",
                    "command": loop,
                    "stop_grace_period": "1s",
                },
            },
            "volumes": {"app-data": {}},
        }
        (repo / "docker-compose.yaml").write_text(json.dumps(config, indent=2))
        # Operator-owned configuration the controller must keep applying.
        (repo / ".env").write_text("HERMETIC_SETTING=from-dotenv\n")
        override = {"services": {"app": {"labels": {"moonmind.test.override": "kept"}}}}
        (repo / "docker-compose.override.yaml").write_text(json.dumps(override))

    def compose(*args, check=True):
        return docker(
            "compose",
            "--project-name",
            project,
            "--project-directory",
            str(repo),
            "-f",
            str(repo / "docker-compose.yaml"),
            "-f",
            str(repo / "docker-compose.override.yaml"),
            *args,
            check=check,
        )

    def container(service):
        return compose("ps", "--all", "-q", service).stdout.strip()

    def inspect(container_id, template):
        return docker("inspect", "--format", template, container_id).stdout.strip()

    def marker(service):
        """Which published image the service's container runs (store-independent)."""
        return inspect(
            container(service), '{{index .Config.Labels "moonmind.test.marker"}}'
        )

    app = server.build_app(
        store=store,
        secret=SECRET,
        legacy_writer_probe=server.reconcile_retired_shell_controller,
        target_resolver=lambda stack: server.default_target(
            stack, repo=str(repo), recorded_project=project
        ),
    )

    def submit(image):
        raw = json.dumps({"stack": STACK, "desiredImage": image}).encode()
        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/v1/operations",
            "CONTENT_LENGTH": str(len(raw)),
            "wsgi.input": io.BytesIO(raw),
            "HTTP_AUTHORIZATION": f"Bearer {SECRET}",
        }
        answer = {}
        body = b"".join(
            app(environ, lambda status, headers: answer.update(status=status))
        )
        return answer["status"], json.loads(body)

    publish("app:old", "old", keep_local=True)
    publish("infra:1", "infra", keep_local=True)
    write_compose()
    yield {
        "registry": registry,
        "host": host,
        "project": project,
        "repo": repo,
        "store": store,
        "publish": publish,
        "write_compose": write_compose,
        "compose": compose,
        "container": container,
        "inspect": inspect,
        "marker": marker,
        "submit": submit,
    }
    compose("down", "--volumes", "--remove-orphans", "--timeout", "1", check=False)
    docker("rm", "-f", "-v", registry, check=False)
    for ref in built:
        docker("rmi", "-f", ref, check=False)


def test_repair_with_target_stopped_preserves_unchanged_work(journey):
    compose, container, inspect = (
        journey["compose"],
        journey["container"],
        journey["inspect"],
    )
    compose("up", "-d", "--wait")
    app_id = container("app")
    infra_id = container("infra")
    docker("exec", app_id, "sh", "-c", "echo kept > /data/marker")
    attached = docker(
        "run",
        "-d",
        "--name",
        f"{journey['project']}-attached",
        "--network",
        f"{journey['project']}_default",
        SEED_IMAGE,
        "sh",
        "-c",
        "while true; do sleep 1; done",
    ).stdout.strip()
    try:
        compose("stop", "app")
        new_ref = journey["publish"]("app:new", "new")

        status, operation = journey["submit"](new_ref)

        assert status.startswith("202"), operation
        assert operation["status"] == "succeeded", operation
        assert operation["installed"]["image"] == new_ref
        repaired = container("app")
        assert inspect(repaired, "{{.State.Running}}") == "true"
        assert journey["marker"]("app") == "new"
        # .env and the override survive into the recreated service.
        assert (
            inspect(repaired, '{{index .Config.Labels "moonmind.test.override"}}')
            == "kept"
        )
        assert "HERMETIC_SETTING=from-dotenv" in inspect(
            repaired, "{{json .Config.Env}}"
        )
        # Unchanged infrastructure is not recreated; data and attached work stay.
        assert container("infra") == infra_id
        assert inspect(infra_id, "{{.State.Running}}") == "true"
        assert docker("exec", repaired, "cat", "/data/marker").stdout.strip() == "kept"
        assert inspect(attached, "{{.State.Running}}") == "true"
    finally:
        docker("rm", "-f", attached, check=False)


def test_pull_failure_leaves_running_containers_untouched(journey):
    compose, container, inspect = (
        journey["compose"],
        journey["container"],
        journey["inspect"],
    )
    compose("up", "-d", "--wait")
    before = {
        service: (
            container(service),
            inspect(container(service), "{{.State.StartedAt}}"),
        )
        for service in ("app", "infra")
    }

    status, operation = journey["submit"](
        f"{journey['host']}/hermetic/app:never-pushed"
    )

    assert status.startswith("202"), operation
    assert operation["status"] == "failed", operation
    assert all("staging failed" in item["error"] for item in operation["attempts"])
    for service, (container_id, started) in before.items():
        assert container(service) == container_id
        assert inspect(container_id, "{{.State.Running}}") == "true"
        assert inspect(container_id, "{{.State.StartedAt}}") == started


def test_staged_apply_survives_registry_loss(journey, controller, monkeypatch):
    server = controller["server"]
    compose = journey["compose"]
    compose("up", "-d", "--wait")
    new_ref = journey["publish"]("app:new", "new")
    staged_floor = server.require_routing_floor

    def registry_lost_after_staging(*args, **kwargs):
        # Runs after every image is staged and before Compose recreates anything.
        docker("stop", journey["registry"])
        return staged_floor(*args, **kwargs)

    monkeypatch.setattr(server, "require_routing_floor", registry_lost_after_staging)

    status, operation = journey["submit"](new_ref)

    assert status.startswith("202"), operation
    assert operation["status"] == "succeeded", operation
    assert journey["marker"]("app") == "new"
    assert docker("pull", new_ref, check=False).returncode != 0


def test_restart_recovery_resumes_unfinished_work_once(journey, controller):
    server = controller["server"]
    store, compose, container = (
        journey["store"],
        journey["compose"],
        journey["container"],
    )
    compose("up", "-d", "--wait")
    compose("stop", "app")
    new_ref = journey["publish"]("app:new", "new")
    target = server.default_target(
        STACK, repo=str(journey["repo"]), recorded_project=journey["project"]
    )
    # The previous controller recorded and staged the request, then died.
    interrupted = store.begin(
        stack=STACK, desired_image=new_ref, source_revision="", target=target
    )
    store.mark_stage(interrupted["operationId"], stage="staged")

    def apply(operation):
        return server.production_apply(store, operation)

    first = server.resume_open_operations(
        store, apply, legacy_writer_probe=server.reconcile_retired_shell_controller
    )
    repaired = container("app")
    second = server.resume_open_operations(
        store, apply, legacy_writer_probe=server.reconcile_retired_shell_controller
    )

    assert first["converged"] == [interrupted["operationId"]], first
    assert store.load(interrupted["operationId"])["status"] == "succeeded"
    assert journey["marker"]("app") == "new"
    assert second["converged"] == [], second
    assert container("app") == repaired


def test_failed_init_db_gates_the_apply_with_readable_diagnostics(journey):
    compose, container, inspect = (
        journey["compose"],
        journey["container"],
        journey["inspect"],
    )
    journey["write_compose"](init_db_exit=3)
    compose("up", "-d", "--wait", "infra")
    new_ref = journey["publish"]("app:new", "new")

    status, operation = journey["submit"](new_ref)

    assert status.startswith("202"), operation
    assert operation["status"] == "failed", operation
    assert operation["installed"] is None
    assert "init-db" in operation["attempts"][-1]["error"], operation["attempts"]
    app = container("app")
    assert not app or inspect(app, "{{.State.Running}}") == "false"
    assert inspect(container("infra"), "{{.State.Running}}") == "true"
