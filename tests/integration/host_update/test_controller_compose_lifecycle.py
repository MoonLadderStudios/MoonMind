"""Actual Compose-owned controller startup, network discovery and restart.

Uses the controller service shipped in the candidate release, not a second
hand-written service. Every Docker resource belongs to a disposable project.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.integration]


def command(*args, check=True, timeout=180):
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_compose_owned_controller_first_start_dns_and_down_up(tmp_path):
    if os.environ.get("MOONMIND_TEST_JOURNAL_TRANSITION") != "1":
        pytest.skip("Real Docker journey is enabled in controller-journey CI")
    image = os.environ["MOONMIND_TEST_JOURNAL_APP_IMAGE"]
    project = f"moonmind-test-controller-lifecycle-{uuid4().hex[:8]}"
    network = f"{project}_deployment-controller-network"
    repo = tmp_path / "deployment"
    repo.mkdir()
    state = repo / "deploy/state/controller"
    # Both directories exist in an ordinary checkout via committed .gitkeep
    # files; preserve that host ownership boundary in this minimal fixture.
    state.mkdir(parents=True)
    (state.parent / ".gitkeep").touch()
    (state / ".gitkeep").touch()
    exported = json.loads(
        command(
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--entrypoint",
            "python",
            image,
            "-c",
            "import json,pathlib,yaml; print(json.dumps(yaml.safe_load(pathlib.Path('/app/release/docker-compose.yaml').read_text())))",
        ).stdout
    )
    controller = exported["services"]["controller"]
    controller["image"] = image
    # A test-owned random port makes parallel CI independent of host settings.
    controller["ports"] = ["127.0.0.1::8472"]
    config = {
        "services": {"controller": controller},
        "networks": {
            "default": {},
            "deployment-controller-network": {"name": network, "internal": True},
        },
    }
    compose_file = repo / "docker-compose.json"
    compose_file.write_text(json.dumps(config))
    compose = [
        "docker",
        "compose",
        "--env-file",
        os.devnull,
        "-p",
        project,
        "--project-directory",
        str(repo),
        "-f",
        str(compose_file),
    ]

    def up():
        result = command(
            *compose, "up", "-d", "--wait", "--wait-timeout", "90", check=False
        )
        if result.returncode:
            logs = command(*compose, "logs", "--no-color", "controller", check=False)
            pytest.fail(result.stdout + result.stderr + logs.stdout + logs.stderr)
        return command(*compose, "ps", "-q", "controller").stdout.strip()

    def read_state():
        # State is root-owned in production. Read through a test-only mount,
        # and return identity plus a hash, never the bearer secret itself.
        return json.loads(
            command(
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--user",
                "0:0",
                "--mount",
                f"type=bind,source={state},target=/state,readonly",
                "--entrypoint",
                "python",
                image,
                "-c",
                "import hashlib,json,pathlib; p=pathlib.Path('/state'); "
                "print(json.dumps({'identity':json.loads((p/'controller-identity.json').read_text()),"
                "'secretHash':hashlib.sha256((p/'secrets/controller-bearer').read_bytes()).hexdigest()}))",
            ).stdout
        )

    def client_health():
        # A different container resolves Docker DNS and authenticates exactly
        # through the API-visible alias, not the host's published endpoint.
        return json.loads(
            command(
                "docker",
                "run",
                "--rm",
                "--network",
                network,
                "--user",
                "0:0",
                "--mount",
                f"type=bind,source={state},target=/state,readonly",
                "--entrypoint",
                "python",
                image,
                "-c",
                "import json,pathlib,socket,urllib.request; socket.getaddrinfo('moonmind-controller',8472); "
                "secret=pathlib.Path('/state/secrets/controller-bearer').read_text().strip(); "
                "req=urllib.request.Request('http://moonmind-controller:8472/v1/healthz',headers={'Authorization':'Bearer '+secret}); "
                "print(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=5).read().decode())",
            ).stdout
        )

    def current_container():
        return command(*compose, "ps", "-q", "controller").stdout.strip()

    def eventually(predicate, timeout=90):
        deadline = time.monotonic() + timeout
        last_observation = None
        while time.monotonic() < deadline:
            try:
                last_observation = predicate()
                if last_observation:
                    return
            except (OSError, ValueError, AssertionError) as exc:
                # Replacement temporarily removes the container/health endpoint;
                # retry observation until the bounded lifecycle deadline.
                last_observation = str(exc)
            time.sleep(1)
        # Capture before finally removes the test-owned containers. Project
        # state and server logs exclude container environment/bearer contents.
        logs = command(
            *compose, "logs", "--no-color", "--tail", "60", "controller", check=False
        )
        pytest.fail(
            f"Controller lifecycle condition did not converge; last observation: {last_observation!r}\n"
            f"Controller logs:\n{logs.stdout}{logs.stderr}"
        )

    def open_operation(operation_id):
        command(
            "docker",
            "exec",
            current_container(),
            "python",
            "-c",
            "import os,sys; sys.path.insert(0,'/app/deploy/controller'); import record; "
            "s=record.OperationStore(os.environ['MOONMIND_CONTROLLER_STATE_DIR']); "
            "s.begin(stack='moonmind',desired_image=sys.argv[2],source_revision='',"
            "operation_id=sys.argv[1],target={'project':sys.argv[3]}); "
            "s.mark_stage(sys.argv[1],stage='staged')",
            operation_id,
            image,
            project,
        )
        command(
            "docker",
            "exec",
            "--detach",
            current_container(),
            "python",
            "-c",
            "import fcntl,os,pathlib,time; p=pathlib.Path(os.environ['MOONMIND_CONTROLLER_STATE_DIR']); "
            "f=(p/'locks/moonmind.lock').open('a+b'); fcntl.flock(f,fcntl.LOCK_EX); time.sleep(600)",
        )

        # Avoid racing the detached lock holder with the replacement helper.
        def locked():
            result = command(
                "docker",
                "exec",
                current_container(),
                "python",
                "-c",
                "import os,sys; sys.path.insert(0,'/app/deploy/controller'); import lock; "
                "sys.exit(0 if lock.StackLock(os.environ['MOONMIND_CONTROLLER_STATE_DIR'],'moonmind').probe() else 1)",
                check=False,
            )
            return result.returncode == 0

        eventually(locked)

    def handoff(operation_id, target_image):
        result = command(
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
            "--entrypoint",
            "python",
            image,
            "/app/deploy/controller/bootstrap.py",
            "handoff",
            "--state-dir",
            "/workspace/deployment_state/controller",
            "--operation",
            operation_id,
            "--image",
            target_image,
            timeout=300,
        )
        prefix = "MOONMIND_CONTROLLER_HANDOFF="
        return json.loads(
            [
                line[len(prefix) :]
                for line in result.stdout.splitlines()
                if line.startswith(prefix)
            ][-1]
        )

    built_images = []
    try:
        first = up()
        assert first
        # This is the ordinary CI host user, with no privileged read helper.
        bearer = (state / "secrets/controller-bearer").read_text().strip()
        assert bearer
        assert (state / "secrets/controller-bearer").stat().st_mode & 0o777 == 0o600
        submissions = state.parent / "release-submissions"
        submissions.mkdir()
        receipt = submissions / "fixture.json"
        receipt.write_text('{"fixture":true}')
        assert json.loads(receipt.read_text()) == {"fixture": True}
        initial = read_state()
        assert initial["identity"]["project"] == project
        assert initial["identity"]["targetProject"] == project
        assert initial["identity"]["targetNetwork"] == network
        assert initial["identity"]["port"] == 8472
        assert 0 < initial["identity"]["hostPort"] < 65536
        assert "compose-owned-controller" in client_health()["capabilities"]
        first_network = command(
            "docker", "network", "inspect", "--format", "{{.Id}}", network
        ).stdout.strip()
        command(*compose, "down", "--timeout", "10")
        assert command("docker", "inspect", first, check=False).returncode != 0
        assert (
            command("docker", "network", "inspect", network, check=False).returncode
            != 0
        )
        # Durable identity remains, but the old network/container no longer
        # exists. Ordinary up must reconstruct the endpoint without a script.
        second = up()
        assert second and second != first
        assert (
            command(
                "docker", "network", "inspect", "--format", "{{.Id}}", network
            ).stdout.strip()
            != first_network
        )
        restored = read_state()
        assert restored["secretHash"] == initial["secretHash"]
        assert restored["identity"]["project"] == project
        assert restored["identity"]["targetNetwork"] == network
        assert "compose-owned-controller" in client_health()["capabilities"]
        # Exercise the same-project external handoff, then its restoration
        # path. The failing target still imports/capability-probes correctly;
        # only starting its server fails, so this really tests replacement.
        images = {}
        for name, fail_start in (("replacement", False), ("broken", True)):
            tag = f"{project}-{name}:local"
            context = tmp_path / name
            context.mkdir()
            lines = [f"FROM {image}", f"LABEL moonmind.test.lifecycle={name}"]
            if fail_start:
                lines.append(
                    "RUN python -c \"from pathlib import Path; p=Path('/app/deploy/controller/server.py'); p.write_text(p.read_text().replace('raise SystemExit(main())','raise SystemExit(3)'))\""
                )
            (context / "Dockerfile").write_text("\n".join(lines) + "\n")
            command(
                "docker",
                "build",
                "--network=none",
                "--pull=false",
                "-t",
                tag,
                str(context),
                timeout=300,
            )
            built_images.append(tag)
            images[name] = command(
                "docker", "image", "inspect", "--format", "{{.Id}}", tag
            ).stdout.strip()

        def resumed(operation_id):
            # Retained operation records stay private/root-owned; only the
            # bootstrap credential is shared with the ordinary host operator.
            operation = json.loads(
                command(
                    "docker",
                    "exec",
                    current_container(),
                    "python",
                    "-c",
                    "import json,os,pathlib,sys; p=pathlib.Path(os.environ['MOONMIND_CONTROLLER_STATE_DIR']); "
                    "o=json.loads((p/'operations'/f'{sys.argv[1]}.json').read_text()); "
                    "print(json.dumps({'attempts':o['attempts'],'status':o['status']}))",
                    operation_id,
                ).stdout
            )
            # This lifecycle-only operation deliberately omits a deployment
            # target, so restart recovery must reach and record preflight
            # refusal, not deploy the full application. Refusal retains staged
            # status until its attempt budget is exhausted. This proves the
            # replacement invoked recovery, not end-to-end application update
            # completion; separate application recovery journeys cover that.
            assert operation["attempts"], f"No recovery attempt yet: {operation!r}"
            assert (
                operation["attempts"][-1]["error"] == "target configuration is invalid"
            ), operation
            assert operation["status"] == "staged", operation
            return True

        def finish_test_operation(operation_id):
            command(
                "docker",
                "exec",
                current_container(),
                "python",
                "-c",
                "import os,sys; sys.path.insert(0,'/app/deploy/controller'); import record; "
                "s=record.OperationStore(os.environ['MOONMIND_CONTROLLER_STATE_DIR']); "
                "s.supersede(sys.argv[1],reason='test-owned lifecycle probe completed')",
                operation_id,
            )

        open_operation("same-project-handoff")
        result = handoff("same-project-handoff", images["replacement"])
        assert result == {"status": "handed-off", "image": images["replacement"]}
        eventually(lambda: resumed("same-project-handoff"))
        finish_test_operation("same-project-handoff")
        eventually(
            lambda: "compose-owned-controller" in client_health()["capabilities"]
        )
        assert (
            command(
                "docker", "inspect", "--format", "{{.Image}}", current_container()
            ).stdout.strip()
            == images["replacement"]
        )
        assert read_state()["secretHash"] == initial["secretHash"]
        open_operation("same-project-restore")
        result = handoff("same-project-restore", images["broken"])
        assert result["status"] == "unavailable"
        assert "previous controller restored" in result["reason"]
        eventually(lambda: resumed("same-project-restore"))
        finish_test_operation("same-project-restore")
        eventually(
            lambda: "compose-owned-controller" in client_health()["capabilities"]
        )
        assert (
            command(
                "docker", "inspect", "--format", "{{.Image}}", current_container()
            ).stdout.strip()
            == images["replacement"]
        )
        assert read_state()["secretHash"] == initial["secretHash"]
    finally:
        command(*compose, "down", "--timeout", "10", check=False)
        if built_images:
            command("docker", "image", "rm", *built_images, check=False)


def test_compose_migration_waits_for_live_standalone_owner(tmp_path):
    """Candidate in the old separate-project shape, not a historical image.

    A real kernel lock and open durable operation prevent the new service
    from replacing the old owner. Only the test actor completes that work.
    """
    if os.environ.get("MOONMIND_TEST_JOURNAL_TRANSITION") != "1":
        pytest.skip("Real Docker journey is enabled in controller-journey CI")
    import socket

    image = os.environ["MOONMIND_TEST_JOURNAL_APP_IMAGE"]
    project = f"moonmind-test-controller-migration-{uuid4().hex[:8]}"
    legacy_project = f"{project}-legacy"
    network = f"{project}_deployment-controller-network"
    repo = tmp_path / "deployment"
    state = repo / "deploy/state/controller"
    state.mkdir(parents=True)
    (state.parent / ".gitkeep").touch()
    (state / ".gitkeep").touch()
    exported = json.loads(
        command(
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--entrypoint",
            "python",
            image,
            "-c",
            "import json,pathlib,yaml; print(json.dumps(yaml.safe_load(pathlib.Path('/app/release/docker-compose.yaml').read_text())))",
        ).stdout
    )
    controller = exported["services"]["controller"]
    controller["image"] = image
    controller["ports"] = ["127.0.0.1::8472"]
    compose_file = repo / "docker-compose.json"
    compose_file.write_text(
        json.dumps(
            {
                "services": {"controller": controller},
                "networks": {
                    "default": {},
                    "deployment-controller-network": {
                        "name": network,
                        "internal": True,
                    },
                },
            }
        )
    )
    compose = [
        "docker",
        "compose",
        "--env-file",
        os.devnull,
        "-p",
        project,
        "--project-directory",
        str(repo),
        "-f",
        str(compose_file),
    ]
    # Copy this before startup rewrites the generated manifest to the new
    # project's identity, so cleanup always has the legacy project's file.
    legacy_file = repo / "legacy-controller.json.yaml"
    legacy_compose = [
        "docker",
        "compose",
        "--env-file",
        os.devnull,
        "-p",
        legacy_project,
        "-f",
        str(legacy_file),
    ]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    image_id = command(
        "docker", "image", "inspect", "--format", "{{.Id}}", image
    ).stdout.strip()

    def state_evidence():
        return json.loads(
            command(
                "docker",
                "run",
                "--rm",
                "--network=none",
                "--user",
                "0:0",
                "--mount",
                f"type=bind,source={state},target=/state,readonly",
                "--entrypoint",
                "python",
                image,
                "-c",
                "import hashlib,json,pathlib; p=pathlib.Path('/state'); op=p/'operations/live-legacy.json'; "
                "print(json.dumps({'identity':json.loads((p/'controller-identity.json').read_text()),"
                "'secretHash':hashlib.sha256((p/'secrets/controller-bearer').read_bytes()).hexdigest(),"
                "'operation':json.loads(op.read_text()) if op.exists() else None}))",
            ).stdout
        )

    def eventually(predicate, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(1)
        pytest.fail("Legacy controller migration did not reach the expected state")

    try:
        command(
            "docker",
            "network",
            "create",
            "--internal",
            "--label",
            f"com.docker.compose.project={project}",
            "--label",
            "com.docker.compose.network=deployment-controller-network",
            network,
        )
        # Render via the real bootstrap, with explicit observed host sources.
        command(
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--user",
            "0:0",
            "--mount",
            f"type=bind,source={state},target=/state",
            "--entrypoint",
            "python",
            image,
            "-c",
            "import pathlib,sys; sys.path.insert(0,'/app/deploy/controller'); import bootstrap; "
            "s=pathlib.Path('/state'); repo=pathlib.Path(sys.argv[1]); "
            "bootstrap.ensure_secret(s); "
            "identity={'project':sys.argv[2],'targetProject':sys.argv[3],'repo':str(repo),'port':int(sys.argv[4]),'targetNetwork':sys.argv[5]}; "
            "import json; (s/'controller-identity.json').write_text(json.dumps(identity)); "
            "bootstrap.record_controller_image(s,requested=sys.argv[6],pinned=sys.argv[6]); "
            "bootstrap.render_compose_file(state_dir=s,repo=repo,image=sys.argv[6],port=int(sys.argv[4]),project=sys.argv[2],target_project=sys.argv[3],target_network=sys.argv[5],state_source=sys.argv[7],repo_source=str(repo))",
            str(repo),
            legacy_project,
            project,
            str(port),
            network,
            image_id,
            str(state),
        )
        legacy_file.write_bytes((state / "controller-compose.yaml").read_bytes())
        command(*legacy_compose, "up", "-d", "--pull", "never", "--wait")
        legacy_id = command(*legacy_compose, "ps", "-q", "controller").stdout.strip()
        eventually(
            lambda: (
                command(
                    "docker",
                    "exec",
                    legacy_id,
                    "python",
                    "-c",
                    "import os,pathlib,urllib.request; secret=pathlib.Path(os.environ['MOONMIND_CONTROLLER_SECRET_FILE']).read_text().strip(); "
                    "req=urllib.request.Request('http://127.0.0.1:'+os.environ['MOONMIND_CONTROLLER_PORT']+'/v1/healthz',headers={'Authorization':'Bearer '+secret}); "
                    "urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=2).read()",
                    check=False,
                ).returncode
                == 0
            )
        )
        # This actor runs inside the old owner and holds the same kernel lock
        # as a real update. It cannot finish until this test permits it.
        command(
            "docker",
            "exec",
            "--detach",
            legacy_id,
            "python",
            "-c",
            "import os,pathlib,sys,time; sys.path.insert(0,'/app/deploy/controller'); import lock,record; "
            "p=pathlib.Path(os.environ['MOONMIND_CONTROLLER_STATE_DIR']); s=record.OperationStore(p); "
            "guard=lock.StackLock(p,'moonmind'); "
            "with_context=guard.acquire(); with_context.__enter__(); "
            "s.begin(stack='moonmind',desired_image=sys.argv[1],source_revision='',operation_id='live-legacy',target={'project':sys.argv[2]}); "
            "s.mark_stage('live-legacy',stage='applying'); "
            "exec(\"while not (p/'fixture-release').exists():\\n time.sleep(0.1)\"); "
            "s.supersede('live-legacy',reason='test-owned actor completed its work'); with_context.__exit__(None,None,None)",
            image,
            project,
        )
        eventually(lambda: bool(state_evidence()["operation"]))
        before = state_evidence()
        assert before["operation"]["status"] == "applying"
        command(*compose, "up", "-d", "--pull", "never")
        # Require actual startup contention, not merely an absence of change
        # immediately after launching the new container.
        eventually(
            lambda: (
                "owned by another controller"
                in command(
                    *compose, "logs", "--no-color", "controller", check=False
                ).stdout
            )
        )
        assert (
            command(
                "docker", "inspect", "--format", "{{.State.Running}}", legacy_id
            ).stdout.strip()
            == "true"
        )
        blocked = state_evidence()
        assert blocked["identity"] == before["identity"]
        assert blocked["secretHash"] == before["secretHash"]
        assert blocked["operation"]["status"] == "applying"
        (state / "fixture-release").write_text("complete")
        result = command(
            *compose, "up", "-d", "--wait", "--wait-timeout", "90", check=False
        )
        if result.returncode:
            logs = command(*compose, "logs", "--no-color", "controller", check=False)
            pytest.fail(result.stdout + result.stderr + logs.stdout + logs.stderr)
        assert command("docker", "inspect", legacy_id, check=False).returncode != 0
        after = state_evidence()
        assert (
            after["identity"]["project"]
            == after["identity"]["targetProject"]
            == project
        )
        assert after["secretHash"] == before["secretHash"]
        assert after["operation"]["status"] == "superseded"
        assert (
            after["operation"]["supersededReason"]
            == "test-owned actor completed its work"
        )
        owners = command(
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            "label=com.docker.compose.service=controller",
        ).stdout.split()
        assert len(owners) == 1
    finally:
        # Release the test actor even on assertion failure before teardown.
        (state / "fixture-release").write_text("complete")
        command(*compose, "down", "--timeout", "10", check=False)
        if legacy_file.exists():
            command(*legacy_compose, "down", "--timeout", "10", check=False)
        command("docker", "network", "rm", network, check=False)
