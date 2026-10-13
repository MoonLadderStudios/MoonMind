"""Host-owned refresh through real Docker, root-owned state and private ingress."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = [pytest.mark.integration]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(args, *, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def test_host_refreshes_capable_controller_and_preserves_root_owned_work(tmp_path):
    if os.environ.get("MOONMIND_TEST_JOURNAL_TRANSITION") != "1":
        pytest.skip("Real Docker journey is enabled in controller-journey CI")
    candidate = os.environ["MOONMIND_TEST_JOURNAL_APP_IMAGE"]
    candidate_id = command(
        ["docker", "image", "inspect", "--format", "{{.Id}}", candidate]
    )
    project = f"moonmind-test-host-refresh-{uuid4().hex[:10]}"
    previous = f"{project}-previous:local"
    network = f"{project}_deployment-controller-network"
    repo = tmp_path / "deployment"
    repo.mkdir()
    state = repo / "deploy/state/controller"
    state.mkdir(parents=True)
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(f"FROM {candidate}\nLABEL moonmind.test.previous={project}\n")
    command(["docker", "build", "--network=none", "-t", previous, str(tmp_path)])
    previous_id = command(
        ["docker", "image", "inspect", "--format", "{{.Id}}", previous]
    )
    assert previous_id != candidate_id
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    bootstrap = load("refresh_bootstrap", ROOT / "deploy/controller/bootstrap.py")
    update = load(
        "refresh_update",
        ROOT / ".agents/skills/update-moonmind/scripts/update_release.py",
    )
    secret_file = bootstrap.ensure_secret(state)
    identity = bootstrap.ensure_identity(
        state, repo, port, target_project=project, target_network=network
    )
    identity["project"] = f"{project}-controller"
    (state / "controller-identity.json").write_text(json.dumps(identity))
    compose_file = bootstrap.render_compose_file(
        state_dir=state,
        repo=repo,
        image=previous_id,
        port=port,
        project=identity["project"],
        target_project=project,
        target_network=network,
    )
    compose = [
        "docker",
        "compose",
        "--project-name",
        identity["project"],
        "-f",
        str(compose_file),
    ]
    record = {"project": project, "image": candidate_id}
    url = f"http://127.0.0.1:{port}"
    secret = secret_file.read_text().strip()

    def container():
        return command([*compose, "ps", "-q", "controller"])

    def seed(operation_id, *, failed):
        command(
            [
                "docker",
                "exec",
                container(),
                "python",
                "-c",
                "import sys; sys.path.insert(0,'/app/deploy/controller'); "
                "import record; store=record.OperationStore('/var/lib/moonmind-controller'); "
                "op=store.begin(stack='moonmind', desired_image=sys.argv[2], "
                "source_revision='', operation_id=sys.argv[1]); "
                "[store.record_attempt_error(op['operationId'],error='original failure') "
                "for _ in range(record.MAX_AUTO_ATTEMPTS)] "
                "if sys.argv[3]=='failed' else None",
                operation_id,
                previous_id,
                "failed" if failed else "pending",
            ]
        )

    def saved_digest(operation_id):
        return command(
            [
                "docker",
                "exec",
                container(),
                "python",
                "-c",
                "import hashlib,sys; from pathlib import Path; "
                "p=Path('/var/lib/moonmind-controller/operations')/(sys.argv[1]+'.json'); "
                "print(hashlib.sha256(p.read_bytes()).hexdigest())",
                operation_id,
            ]
        )

    command(["docker", "network", "create", network])
    try:
        command([*compose, "up", "-d", "--pull", "never", "--wait"])
        before = container()
        _, health = update._controller_call(url, secret, "GET", "/v1/healthz")
        assert "active-journal-transition" in health["capabilities"]
        seed("preserved-failure", failed=True)
        assert (state / "operations/preserved-failure.json").stat().st_uid == 0
        failed_digest = saved_digest("preserved-failure")

        update._ensure_controller_journal_transition(
            record, repo, url, secret, refresh=True
        )

        after = container()
        assert before != after
        assert (
            command(["docker", "inspect", "--format", "{{.Image}}", after])
            == candidate_id
        )
        assert saved_digest("preserved-failure") == failed_digest
        assert json.loads((state / "controller-identity.json").read_text()) == identity
        assert secret_file.read_text().strip() == secret
        update._ensure_controller_journal_transition(
            record, repo, url, secret, refresh=True
        )
        assert container() == after

        # An open record belongs to the capable controller, even when a
        # later caller asks bootstrap for a different release.
        seed("active-work", failed=False)
        active_digest = saved_digest("active-work")
        update._ensure_controller_journal_transition(
            {"project": project, "image": previous_id}, repo, url, secret, refresh=True
        )
        assert container() == after
        assert saved_digest("active-work") == active_digest
        assert saved_digest("preserved-failure") == failed_digest
    finally:
        # Only this disposable test project and its fixture image are removed.
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
        command(["docker", "image", "rm", previous], check=False)
