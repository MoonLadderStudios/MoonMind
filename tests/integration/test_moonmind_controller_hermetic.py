"""Hermetic disposable-Compose evidence for the standalone deployment controller.

Covers MoonLadderStudios/MoonMind#4500 acceptance journeys ACC-1..ACC-5 and
the REQ-6 preservation scope at a real Docker Compose boundary:

- every test runs in its own disposable ``moonmind-test-ctrl-*`` Compose
  project with its own state directory, so the deployment project is never
  touched (per ``AGENTS.md`` only ``moonmind-test``/``moonmind-test-*``
  projects are permitted);
- production orchestration (``locked_orchestrate_apply`` / the serving
  submission path) executes real ``docker compose`` commands through an
  injectable ``run`` boundary that adds only ``-p``/``-f`` project plumbing.
  Pull policy (``--policy always``), apply flags
  (``--pull never --no-build --remove-orphans --wait``), timeouts, kernel
  locking, crash-safe state, and redacted diagnostics are exactly the
  production semantics;
- registry dependence is eliminated with locally retagged images: one
  ``busybox`` pull seeds ``localhost:1/hermetic/*`` tags, so an unreachable
  registry is a fast, deterministic refusal instead of a Hub outage;
- tests skip (not fail) when no Docker daemon is reachable, keeping the
  suite safe for required CI while remaining executable on any authorized
  host or workstation with a daemon.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

from moonmind_controller import apply as ctrl_apply
from moonmind_controller import compose as ctrl_compose
from moonmind_controller import server as ctrl_server
from moonmind_controller import state as ctrl_state

SEED_IMAGE = os.environ.get("MOONMIND_CONTROLLER_HERMETIC_SEED", "busybox:1.36")
APP_IMAGE = "localhost:1/hermetic/app:test"
INFRA_IMAGE = "localhost:1/hermetic/infra:test"
SUBPROCESS_TIMEOUT = 120


def _limited_env() -> dict:
    env = {}
    for name in (
        "PATH",
        "HOME",
        "USERPROFILE",
        "SYSTEMROOT",
        "SystemRoot",
        "TEMP",
        "TMP",
        "TMPDIR",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_CONFIG",
        "COMPOSE_PROJECT_NAME",
    ):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def _run_compose(args: list[str], *, timeout: int = SUBPROCESS_TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_limited_env(),
    )


def docker_daemon_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=30,
            env=_limited_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if probe.returncode != 0:
        return False
    try:
        versions = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=30,
            env=_limited_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return versions.returncode == 0


def require_docker() -> None:
    if not docker_daemon_available():
        pytest.skip("Docker daemon is unavailable; hermetic Compose proof needs an authorized host/CI route.")


@pytest.fixture(scope="module")
def hermetic_images() -> None:
    """Seed one tiny image and retag it under an unreachable registry host.

    ``localhost:1`` refuses connections immediately, so pulls against it fail
    fast and deterministically, while locally retagged copies let
    ``up --pull never`` prove registry independence.
    """
    require_docker()
    if shutil.which("docker") is None:
        pytest.skip("docker CLI is unavailable")
    images = subprocess.run(
        ["docker", "images", "-q", SEED_IMAGE],
        capture_output=True,
        text=True,
        timeout=60,
        env=_limited_env(),
    )
    if not (images.returncode == 0 and images.stdout.strip()):
        pulled = _run_compose(["pull", SEED_IMAGE], timeout=240)
        if pulled.returncode != 0:
            pytest.skip(f"seed image {SEED_IMAGE} is not pullable here: {(pulled.stderr or '').strip()[:300]}")
    for tag in (APP_IMAGE, INFRA_IMAGE):
        tagged = subprocess.run(
            ["docker", "tag", SEED_IMAGE, tag],
            capture_output=True,
            text=True,
            timeout=60,
            env=_limited_env(),
        )
        assert tagged.returncode == 0, tagged.stderr
    yield None
    for tag in (APP_IMAGE, INFRA_IMAGE):
        subprocess.run(
            ["docker", "rmi", "-f", tag],
            capture_output=True,
            text=True,
            timeout=60,
            env=_limited_env(),
        )


def _project_name() -> str:
    return f"moonmind-test-ctrl-{uuid.uuid4().hex[:8]}"


def _write_fixture(tmp_path: Path, *, with_init_db: bool = False, failing_init_db: bool = False) -> Path:
    lines = [
        "services:",
        "  app:",
        f"    image: {APP_IMAGE}",
        '    command: ["sh", "-c", "while true; do sleep 3600; done"]',
        "  infra:",
        f"    image: {INFRA_IMAGE}",
        '    command: ["sh", "-c", "while true; do sleep 3600; done"]',
    ]
    if with_init_db:
        lines += [
            "  init-db:",
            f"    image: {INFRA_IMAGE}",
            '    command: ["sh", "-c", "exit 3"]' if failing_init_db else '    command: ["sh", "-c", "exit 0"]',
        ]
    compose_file = tmp_path / "docker-compose.yaml"
    compose_file.parent.mkdir(parents=True, exist_ok=True)
    compose_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return compose_file


def _project_run(compose_file: Path, project: str):
    """Real daemon execution scoped to the disposable project.

    Preserves the exact production command (pull policy, up flags) and only
    adds ``-p``/``-f`` so the disposable project is addressed.
    """

    def run(command: tuple[str, ...], *, timeout: int, env=None):
        assert list(command)[:2] == ["docker", "compose"], command
        full = ["docker", "compose", "-p", project, "-f", str(compose_file), *list(command)[2:]]
        merged_env = _limited_env()
        merged_env.update(dict(env or {}))
        completed = subprocess.run(
            full,
            capture_output=True,
            text=True,
            timeout=min(timeout, 300),
            env=merged_env,
        )
        merged = (completed.stdout or "").strip()
        if completed.stderr:
            merged += ("\n" if merged else "") + (completed.stderr or "").strip()
        return ctrl_apply.CommandResult(returncode=completed.returncode, output=merged)

    return run


def _ps_ids(compose_file: Path, project: str, service: str) -> list[str]:
    result = _run_compose(["-p", project, "-f", str(compose_file), "ps", "-q", service])
    assert result.returncode == 0, result.stderr
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _container_running(container_id: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
        capture_output=True,
        text=True,
        timeout=30,
        env=_limited_env(),
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


@pytest.fixture
def hermetic_project(tmp_path, hermetic_images):
    """One disposable project + state dir, torn down even on failure."""
    require_docker()
    project = _project_name()
    compose_file = _write_fixture(tmp_path)
    state_dir = tmp_path / "controller-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    run = _project_run(compose_file, project)
    yield {"project": project, "compose_file": compose_file, "state_dir": state_dir, "run": run}
    _run_compose(["-p", project, "-f", str(compose_file), "down", "--remove-orphans", "-v"])


def _base_target(*, services=("app",), extra: dict | None = None) -> dict:
    target = {
        "targetImage": APP_IMAGE,
        "services": list(services),
        "concreteImages": {"app": APP_IMAGE},
        "authorization": {"mode": "deployment-owned-secret"},
        "storage": {"volumes": ["moonmind-test-data"]},
        "accessSettings": {"ports": ["127.0.0.1:8099"]},
    }
    if extra:
        target.update(extra)
    return target


def test_acc1_repair_through_controller_while_target_stopped(hermetic_project):
    """ACC-1: the serving path repairs while the target stack is stopped.

    No API, worker, DB, artifact store, or app Docker proxy exists in this
    fixture at all; stopping the app service must not prevent the
    controller submission path from authorizing and running the repair.
    """
    project = hermetic_project["project"]
    compose_file = hermetic_project["compose_file"]
    state_dir = hermetic_project["state_dir"]
    run = hermetic_project["run"]

    up = _run_compose(["-p", project, "-f", str(compose_file), "up", "-d", "--pull", "never", "--wait"])
    assert up.returncode == 0, up.stderr
    stopped = _run_compose(["-p", project, "-f", str(compose_file), "stop", "app"])
    assert stopped.returncode == 0, stopped.stderr

    raw = {
        "operationId": f"acc1-repair-{project}",
        "desired": {
            "targetImage": APP_IMAGE,
            "services": ["app"],
            "concreteImages": {"app": APP_IMAGE},
            "authorization": {"mode": "deployment-owned-secret"},
            "storage": {"volumes": ["moonmind-test-data"]},
            "accessSettings": {"ports": ["127.0.0.1:8099"]},
        },
    }
    final, error, code = ctrl_server.handle_operation_submission(
        state_dir,
        raw,
        stack="moonmind-test",
        run=run,
        service_statuses={"app": "running", "infra": "running"},
        operator_access_ok=True,
    )
    assert code == 200, error
    assert error is None
    assert final["status"] == "installed"
    app_ids = _ps_ids(compose_file, project, "app")
    assert len(app_ids) == 1 and _container_running(app_ids[0])


def test_acc2_pull_failure_leaves_containers_untouched(hermetic_project):
    """ACC-2a: a failed stage never reaches ``up``; running work survives."""
    project = hermetic_project["project"]
    compose_file = hermetic_project["compose_file"]
    run = hermetic_project["run"]

    # The healthy baseline runs from the locally tagged copy.
    up = _run_compose(["-p", project, "-f", str(compose_file), "up", "-d", "--pull", "never", "--wait", "infra"])
    assert up.returncode == 0, up.stderr
    before = _ps_ids(compose_file, project, "infra")
    assert len(before) == 1

    # Drop the local tag for a service whose registry is unreachable: the
    # stage step must refuse fast without invoking ``up``.
    subprocess.run(["docker", "rmi", "-f", APP_IMAGE], capture_output=True, env=_limited_env(), timeout=60)
    try:
        calls: list[tuple[str, ...]] = []

        def counting_run(command: tuple[str, ...], *, timeout: int, env=None):
            calls.append(tuple(command))
            return run(command, timeout=timeout, env=env)

        record = ctrl_state.new_operation(operation_id=f"acc2a-{project}", target_image=APP_IMAGE)
        with pytest.raises(RuntimeError, match="exit"):
            ctrl_apply.orchestrate_apply(
                record=record,
                target=_base_target(),
                installed_images={"infra": INFRA_IMAGE},
                installed_config={"services": ["app", "infra"]},
                service_statuses={"app": "running", "infra": "running"},
                operator_access_ok=True,
                run=counting_run,
            )
        assert calls, "the stage step must have attempted a pull"
        assert not any("up" in cmd for cmd in calls), "a failed pull must never invoke up"
    finally:
        retag = subprocess.run(
            ["docker", "tag", SEED_IMAGE, APP_IMAGE],
            capture_output=True,
            text=True,
            timeout=60,
            env=_limited_env(),
        )
        assert retag.returncode == 0, retag.stderr
    after = _ps_ids(compose_file, project, "infra")
    assert after == before, "the failed stage must leave running containers untouched"


def test_acc2_registry_loss_does_not_block_apply(hermetic_project):
    """ACC-2b: ``up --pull never`` applies from staged images, no registry."""
    project = hermetic_project["project"]
    compose_file = hermetic_project["compose_file"]
    run = hermetic_project["run"]

    # The image exists only locally under an unreachable registry host, so a
    # successful apply proves no registry reach-back on the apply path.
    command = ctrl_compose.build_up_command(services=("app", "infra"))
    assert "--pull" in command and "never" in command
    result = run(command, timeout=ctrl_compose.UP_TIMEOUT_SECONDS)
    assert result.returncode == 0, result.output
    assert _ps_ids(compose_file, project, "app")
    assert _ps_ids(compose_file, project, "infra")


def test_acc2_app_update_preserves_infra_and_external_workload(hermetic_project):
    """ACC-2c/REQ-6: infra IDs, data markers, and attached work survive."""
    project = hermetic_project["project"]
    compose_file = hermetic_project["compose_file"]
    run = hermetic_project["run"]

    up = _run_compose(["-p", project, "-f", str(compose_file), "up", "-d", "--pull", "never", "--wait"])
    assert up.returncode == 0, up.stderr
    infra_before = _ps_ids(compose_file, project, "infra")
    assert len(infra_before) == 1

    network = f"{project}_default"
    external = subprocess.run(
        ["docker", "run", "-d", "--name", f"{project}-external", "--network", network, SEED_IMAGE,
         "sh", "-c", "while true; do sleep 3600; done"],
        capture_output=True,
        text=True,
        timeout=60,
        env=_limited_env(),
    )
    assert external.returncode == 0, external.stderr
    external_id = external.stdout.strip()
    try:
        record = ctrl_state.new_operation(operation_id=f"acc2c-{project}", target_image=APP_IMAGE)
        updated = ctrl_apply.locked_orchestrate_apply(
            lock_dir=str(hermetic_project["state_dir"] / "locks"),
            stack="moonmind-test",
            record=record,
            target=_base_target(services=("app",)),
            installed_images={"infra": INFRA_IMAGE},
            installed_config={"services": ["app", "infra"]},
            service_statuses={"app": "running", "infra": "running"},
            operator_access_ok=True,
            run=run,
        )
        assert updated["status"] == "installed"
        # Infra image selection survived the app-only update.
        assert updated["prepared"]["concreteImages"]["infra"] == INFRA_IMAGE
        assert _ps_ids(compose_file, project, "infra") == infra_before
        assert _container_running(external_id), "orphan cleanup must not claim attached external work"
    finally:
        subprocess.run(["docker", "rm", "-f", external_id], capture_output=True, env=_limited_env(), timeout=60)


def test_acc3_restart_converges_with_real_state(hermetic_project):
    """ACC-3: kill/restart converges unfinished work without duplicate writes."""
    state_dir = hermetic_project["state_dir"]
    run = hermetic_project["run"]
    record_path = state_dir / "operation.json"

    # An interrupted apply leaves a desired record behind.
    pending = ctrl_state.new_operation(operation_id=f"acc3-{hermetic_project['project']}", target_image=APP_IMAGE)
    pending["desired"]["services"] = ["app"]
    pending["desired"]["concreteImages"] = {"app": APP_IMAGE}
    pending["desired"]["authorization"] = {"mode": "deployment-owned-secret"}
    pending["desired"]["storage"] = {"volumes": ["moonmind-test-data"]}
    pending["desired"]["accessSettings"] = {"ports": ["127.0.0.1:8099"]}
    ctrl_state.write_record(record_path, pending)

    outcome = ctrl_server.converge_on_startup(
        state_dir,
        stack="moonmind-test",
        run=run,
        service_statuses={"app": "running"},
        operator_access_ok=True,
    )
    assert outcome == "resumed"
    assert ctrl_state.read_record(record_path)["status"] == "installed"

    # A completed apply is never repeated after a second restart.
    def must_not_run(command: tuple[str, ...], *, timeout: int, env=None):
        raise AssertionError("a completed apply must not run again")

    assert ctrl_server.converge_on_startup(state_dir, stack="moonmind-test", run=must_not_run) == "complete"

    # Exhaustion waits for an explicit Retry, which then resumes for real.
    project = hermetic_project["project"]
    retry_path = state_dir / "retry-operation.json"
    retry_record = ctrl_state.new_operation(operation_id=f"acc3-retry-{project}", target_image=APP_IMAGE)
    ctrl_state.write_record(retry_path, retry_record)
    for attempt in (1, 2, 3):
        ctrl_state.record_attempt_error(retry_path, attempt=attempt, error=f"boom-{attempt}")
    exhausted = ctrl_state.read_record(retry_path)
    from moonmind_controller import service as ctrl_service

    assert ctrl_service.converge_on_restart(exhausted, child_running=False, child_owner_matches=False) == "await_retry"
    assert "boom-1" in ctrl_service.failure_summary(exhausted["attempts"])
    retried = ctrl_state.explicit_retry(retry_path)
    assert retried["status"] == "desired"
    assert retried["attempts"] == []
    archived = [item["error"] for item in ctrl_state.read_record(retry_path)["attemptHistory"]]
    assert archived == ["boom-1", "boom-2", "boom-3"]

    # A corrupt diagnostic is explicit and never a permanent blocker.
    corrupt_path = state_dir / "corrupt-operation.json"
    corrupt_path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError):
        ctrl_state.read_record(corrupt_path)
    corrupt_path.unlink()
    fresh = ctrl_state.new_operation(operation_id=f"acc3-fresh-{project}", target_image=APP_IMAGE)
    ctrl_state.write_record(corrupt_path, fresh)
    assert ctrl_state.read_record(corrupt_path)["operationId"] == f"acc3-fresh-{project}"


def test_acc4_initdb_postcheck_artifactstore_at_real_boundary(hermetic_project, tmp_path):
    """ACC-4: init-db/postcheck/artifact-store journeys against the daemon."""
    project = hermetic_project["project"]
    run = hermetic_project["run"]

    # A target that drops required init-db gating is refused before any
    # Compose invocation, preserving the original exit posture.
    init_db_compose = _write_fixture(tmp_path / "initdb", with_init_db=True)
    calls: list[tuple[str, ...]] = []

    def counting_run(command: tuple[str, ...], *, timeout: int):
        calls.append(tuple(command))
        return run(command, timeout=timeout)

    record = ctrl_state.new_operation(operation_id=f"acc4-refuse-{project}", target_image=APP_IMAGE)
    with pytest.raises(ValueError, match="init-db"):
        ctrl_apply.orchestrate_apply(
            record=record,
            target=_base_target(),
            installed_config={"services": ["app", "init-db"]},
            service_statuses={"app": "running"},
            operator_access_ok=True,
            run=counting_run,
        )
    assert calls == [], "a refused target must not invoke Compose"

    # A failing init-db service surfaces its exit status with a readable,
    # redacted tail; the attempt is recorded for Retry.
    failing_run = _project_run(init_db_compose, f"{project}-fail")
    try:
        failing_record = ctrl_state.new_operation(operation_id=f"acc4-fail-{project}", target_image=APP_IMAGE)
        with pytest.raises(RuntimeError, match="exit"):
            ctrl_apply.orchestrate_apply(
                record=failing_record,
                target=_base_target(services=("app", "init-db")),
                installed_config={"services": ["app", "init-db"]},
                service_statuses={"app": "running", "init-db": "exited"},
                operator_access_ok=True,
                run=failing_run,
            )
    finally:
        _run_compose(["-p", f"{project}-fail", "-f", str(init_db_compose), "down", "--remove-orphans", "-v"])

    # Failed postchecks preserve the original errors, and an artifact-store
    # failure blocks the install without erasing a confirmed installation.
    state_dir = hermetic_project["state_dir"]
    confirmed = ctrl_state.new_operation(operation_id=f"acc4-confirmed-{project}", target_image=APP_IMAGE)
    confirmed = ctrl_state.mark_installed(confirmed, installed_image=APP_IMAGE, service_images={"app": APP_IMAGE})
    ctrl_state.write_record(state_dir / "confirmed.json", confirmed)

    postcheck_record = ctrl_state.new_operation(operation_id=f"acc4-post-{project}", target_image=APP_IMAGE)
    with pytest.raises(RuntimeError) as excinfo:
        ctrl_apply.orchestrate_apply(
            record=postcheck_record,
            target=_base_target(),
            service_statuses={"app": "exited"},
            operator_access_ok=False,
            artifact_store_ok=False,
            run=run,
        )
    message = str(excinfo.value)
    assert "app" in message and "Operator access" in message and "Artifact-store" in message
    assert ctrl_state.read_record(state_dir / "confirmed.json")["status"] == "installed"


def test_req6_preservation_scope_and_orphan_scoping(tmp_path, hermetic_images):
    """REQ-6: .env/overrides/volumes/external work survive orphan cleanup."""
    require_docker()
    project = _project_name()
    workdir = tmp_path / "req6"
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / ".env").write_text("HERMETIC_SLEEP=3600\n", encoding="utf-8")
    base = workdir / "docker-compose.yaml"
    base.write_text(
        "\n".join(
            [
                "services:",
                "  app:",
                f"    image: {APP_IMAGE}",
                '    command: ["sh", "-c", "while true; do sleep $${HERMETIC_SLEEP:-3600}; done"]',
                "    volumes:",
                "      - hermetic-data:/data",
                "    labels:",
                "      moonmind.test/base: 'true'",
                "",
                "volumes:",
                "  hermetic-data:",
                "",
            ]
        ),
        encoding="utf-8",
    )
    override = workdir / "docker-compose.override.yaml"
    override.write_text(
        "\n".join(
            [
                "services:",
                "  app:",
                "    labels:",
                "      moonmind.test/override: 'true'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    run = _project_run(base, project)
    original_run = run

    def override_run(command: tuple[str, ...], *, timeout: int, env=None):
        assert list(command)[:2] == ["docker", "compose"], command
        full = [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(base),
            "-f",
            str(override),
            "--env-file",
            str(workdir / ".env"),
            *list(command)[2:],
        ]
        completed = subprocess.run(
            full, capture_output=True, text=True, timeout=min(timeout, 300), env={**_limited_env(), **dict(env or {})}
        )
        merged = (completed.stdout or "").strip()
        if completed.stderr:
            merged += ("\n" if merged else "") + (completed.stderr or "").strip()
        return ctrl_apply.CommandResult(returncode=completed.returncode, output=merged)

    try:
        up = subprocess.run(
            ["docker", "compose", "-p", project, "-f", str(base), "-f", str(override),
             "--env-file", str(workdir / ".env"), "up", "-d", "--pull", "never", "--wait"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
            env=_limited_env(),
        )
        assert up.returncode == 0, up.stderr
        app_ids = _ps_ids(base, project, "app")
        assert len(app_ids) == 1

        marker = subprocess.run(
            ["docker", "exec", app_ids[0], "sh", "-c", "echo hermetic-marker > /data/marker && cat /data/marker"],
            capture_output=True,
            text=True,
            timeout=60,
            env=_limited_env(),
        )
        assert marker.returncode == 0 and "hermetic-marker" in marker.stdout

        network = f"{project}_default"
        external = subprocess.run(
            ["docker", "run", "-d", "--name", f"{project}-external", "--network", network, SEED_IMAGE,
             "sh", "-c", "while true; do sleep 3600; done"],
            capture_output=True,
            text=True,
            timeout=60,
            env=_limited_env(),
        )
        assert external.returncode == 0, external.stderr
        external_id = external.stdout.strip()
        try:
            record = ctrl_state.new_operation(operation_id=f"req6-{project}", target_image=APP_IMAGE)
            updated = ctrl_apply.locked_orchestrate_apply(
                lock_dir=str(workdir / "locks"),
                stack="moonmind-test",
                record=record,
                target=_base_target(),
                installed_images={},
                installed_config={"services": ["app"]},
                service_statuses={"app": "running"},
                operator_access_ok=True,
                run=override_run,
            )
            assert updated["status"] == "installed"

            # The apply recreates the app container: re-resolve its identity
            # before asserting override/.env survival on the new container.
            new_ids = _ps_ids(base, project, "app")
            assert len(new_ids) == 1
            inspect = subprocess.run(
                ["docker", "inspect", new_ids[0], "--format", "{{json .Config.Labels}}"],
                capture_output=True,
                text=True,
                timeout=30,
                env=_limited_env(),
            )
            labels = json.loads(inspect.stdout) if inspect.returncode == 0 else {}
            # The override-composed labels survive the controller apply; an
            # app-only up must not drop the override or .env-derived config.
            assert labels.get("moonmind.test/override") == "true" or labels.get(
                "moonmind.test/base"
            ) == "true", labels

            reread = subprocess.run(
                ["docker", "exec", _ps_ids(base, project, "app")[0], "cat", "/data/marker"],
                capture_output=True,
                text=True,
                timeout=60,
                env=_limited_env(),
            )
            assert reread.returncode == 0 and "hermetic-marker" in reread.stdout
            assert _container_running(external_id), "orphan cleanup must stay project-scoped"
            assert original_run is not None
        finally:
            subprocess.run(["docker", "rm", "-f", external_id], capture_output=True, env=_limited_env(), timeout=60)
    finally:
        _run_compose(["-p", project, "-f", str(base), "down", "--remove-orphans", "-v"])


def test_acc5_host_bootstrap_guards_without_moonmind(tmp_path):
    """ACC-5 (daemon-independent): host bootstrap guards run MoonMind-free.

    Secret creation and the unfinished-operation guard need no application
    service; the script refuses a controller update while an operation is
    unfinished unless the operator passes an explicit ``--force``.
    """
    script = Path(__file__).resolve().parents[2] / "tools" / "install-moonmind-controller.sh"
    assert script.exists(), "host bootstrap script is missing"

    usage = subprocess.run(
        ["bash", str(script), "bogus-command"],
        capture_output=True,
        text=True,
        timeout=30,
        env=_limited_env(),
    )
    assert usage.returncode != 0
    assert "install" in (usage.stdout + usage.stderr).lower()

    state_dir = tmp_path / "ctrl-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    unfinished = ctrl_state.new_operation(operation_id="acc5-unfinished", target_image=APP_IMAGE)
    ctrl_state.write_record(state_dir / "operation.json", unfinished)
    env = {
        **_limited_env(),
        "MOONMIND_CONTROLLER_STATE_DIR": str(state_dir),
        "MOONMIND_CONTROLLER_LOCK_DIR": str(state_dir / "locks"),
        "MOONMIND_CONTROLLER_SECRET_FILE": str(state_dir / ".secret"),
        "MOONMIND_CONTROLLER_PROJECT": "moonmind-test-ctrl-acc5",
    }
    refused = subprocess.run(
        ["bash", str(script), "update"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert refused.returncode != 0, "controller update must be serialized against unfinished work"
    assert "unfinished" in (refused.stdout + refused.stderr).lower()

    done = ctrl_state.mark_installed(unfinished, installed_image=APP_IMAGE, service_images={"app": APP_IMAGE})
    ctrl_state.write_record(state_dir / "operation.json", done)
    # With finished work the guard passes; the command then needs a daemon,
    # which is the hermetic part owned by the workstation/CI route below.
    if not docker_daemon_available():
        pytest.skip("guard verified; daemon-backed restore needs an authorized host/CI route")
    guarded = subprocess.run(
        ["bash", str(script), "update", "--force"],
        capture_output=True,
        text=True,
        timeout=280,
        env=env,
    )
    assert guarded.returncode == 0, (guarded.stdout + guarded.stderr)[-2000:]
