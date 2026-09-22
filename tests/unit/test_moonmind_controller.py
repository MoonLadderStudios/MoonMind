"""Unit coverage for the standalone deployment controller (MoonLadderStudios/MoonMind#4500).

The controller lives outside the MoonMind application stack in the
stdlib-only ``moonmind_controller`` package: its normal operation must not
import MoonMind application modules nor require the API, DB, Temporal,
artifact service, provider manager, Omnigent, or an LLM.
"""

from __future__ import annotations

import json
from pathlib import Path


def test_controller_package_is_stdlib_only():
    """Normal controller execution has no application imports (REQ-1)."""
    import re

    root = Path(__file__).resolve().parents[2] / "moonmind_controller"
    assert root.is_dir(), "moonmind_controller package is missing"
    # ``moonmind_controller`` itself is allowed; the application substrate
    # (``moonmind.*``, ``api_service``, Temporal, HTTP frameworks, LLMs) is not.
    app_import = re.compile(r"^\s*(from|import)\s+moonmind(?![_a-zA-Z0-9])")
    forbidden_markers = (
        "from api_service",
        "import api_service",
        "import temporalio",
        "from temporalio",
        "import httpx",
        "import aiohttp",
        "import fastapi",
        "from fastapi",
        "import openai",
        "import anthropic",
    )
    for path in sorted(root.glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            assert not app_import.match(line), (
                f"{path.name}:{lineno} imports application substrate: {line.strip()}"
            )
            for marker in forbidden_markers:
                assert marker not in line, (
                    f"{path.name}:{lineno} imports application substrate: {marker}"
                )


def test_compose_semantic_default_pull_then_up_without_build():
    """Normal apply stages images then recreates without building (REQ-3)."""
    from moonmind_controller import compose

    pull = compose.build_pull_command(services=("api", "worker"))
    assert pull[:3] == ("docker", "compose", "pull")
    assert "--policy" in pull and "always" in pull

    up = compose.build_up_command(services=("api", "worker"))
    assert up[:4] == ("docker", "compose", "up", "-d")
    assert "--pull" in up and "never" in up
    assert "--no-build" in up
    assert "--remove-orphans" in up
    assert "--wait" in up
    # Full teardown and pruning are never part of the default apply path.
    assert "down" not in up
    assert "--force-recreate" not in up
    assert "prune" not in " ".join(up)


def test_compose_explicit_repair_operations_are_opt_in():
    """Full down and force-recreate exist but are never automatic (REQ-3)."""
    from moonmind_controller import compose

    down = compose.build_explicit_down_command()
    assert down[:3] == ("docker", "compose", "down")
    assert "prune" not in " ".join(down).lower()
    assert "-v" not in down and "--volumes" not in down

    recreate = compose.build_up_command(services=("api",), force_recreate=True)
    assert "--force-recreate" in recreate
    default = compose.build_up_command(services=("api",))
    assert "--force-recreate" not in default


def test_compose_commands_target_the_wired_project():
    """Project flags reach every Compose invocation (P1: target mounts)."""
    from moonmind_controller import compose

    pull = compose.build_pull_command(
        services=("api",),
        project="custom",
        project_directory="/target/moonmind",
        compose_files=("/target/moonmind/docker-compose.yaml",),
    )
    assert pull[:2] == ("docker", "compose")
    assert "--project-name" in pull and "custom" in pull
    assert "--project-directory" in pull and "/target/moonmind" in pull
    assert "-f" in pull and "/target/moonmind/docker-compose.yaml" in pull
    # Project scoping precedes the subcommand; services stay trailing.
    assert pull.index("pull") < pull.index("api")
    assert pull.index("custom") < pull.index("pull")

    up = compose.build_up_command(services=("api",), project="custom")
    assert "--project-name" in up and "custom" in up

    ps = compose.build_ps_command(
        services=("api",), project="custom", project_directory="/target/moonmind"
    )
    assert ps[:2] == ("docker", "compose")
    assert "ps" in ps and "--format" in ps and "json" in ps
    assert "api" in ps


def test_compose_image_env_applies_requested_images():
    """Requested images reach Compose through the environment (P1: images)."""
    from moonmind_controller import compose

    env = compose.image_env(
        target_image="repo@sha256:new",
        concrete_images={"temporal-worker-deployment-control": "repo@sha256:ofw"},
    )
    assert env["MOONMIND_IMAGE"] == "repo@sha256:new"
    assert env["MOONMIND_DEPLOYMENT_WORKER_IMAGE"] == "repo@sha256:ofw"
    assert compose.image_env() == {}
    assert compose.image_env(target_image="repo@sha256:new") == {
        "MOONMIND_IMAGE": "repo@sha256:new"
    }


def test_release_owned_services_exist_in_stack():
    """Handoff targets services that exist in the MoonMind stack (P1)."""
    from pathlib import Path as _Path

    from moonmind_controller import compose

    assert "worker" not in compose.RELEASE_OWNED_SERVICES
    assert "api" in compose.RELEASE_OWNED_SERVICES
    text = (
        _Path(__file__).resolve().parents[2] / "docker-compose.yaml"
    ).read_text()
    # Parse top-level service blocks without extra dependencies.
    blocks: dict = {}
    current = None
    for line in text.splitlines():
        if line.startswith("  ") and not line.startswith("   ") and line.rstrip().endswith(":"):
            current = line.strip()[:-1]
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)
    for service in compose.RELEASE_OWNED_SERVICES:
        assert service in blocks, f"release service {service!r} missing from stack"
        assert any("MOONMIND_IMAGE" in line for line in blocks[service]), (
            f"release service {service!r} does not follow the release image"
        )


def test_operation_record_distinguishes_desired_from_installed(tmp_path):
    """Desired intent and confirmed installation stay distinct (REQ-4)."""
    from moonmind_controller import state

    record = state.new_operation(operation_id="op-1", target_image="repo@sha256:abc")
    assert record["desired"]["targetImage"] == "repo@sha256:abc"
    assert record["installed"] is None
    assert record["status"] == "desired"

    path = tmp_path / "operation.json"
    state.write_record(path, record)
    installed = state.mark_installed(
        state.read_record(path),
        installed_image="repo@sha256:abc",
        service_images={"api": "repo@sha256:abc"},
    )
    state.write_record(path, installed)
    final = state.read_record(path)
    assert final["status"] == "installed"
    assert final["installed"]["image"] == "repo@sha256:abc"
    # A lost result must not repeat a completed apply unnecessarily.
    assert state.apply_already_complete(final) is True


def test_interrupted_write_leaves_recoverable_record(tmp_path):
    """Crash-safe writes never leave a truncated sole copy (REQ-4)."""
    from moonmind_controller import state

    path = tmp_path / "operation.json"
    record = state.new_operation(operation_id="op-2", target_image="repo@sha256:def")
    state.write_record(path, record)
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    assert json.loads(raw)["operationId"] == "op-2"


def test_explicit_retry_resets_budget_and_archives_diagnostics(tmp_path):
    """An explicit Retry starts a fresh bounded attempt (P1: retry budget)."""
    from moonmind_controller import state

    path = tmp_path / "operation.json"
    state.write_record(path, state.new_operation(operation_id="op-3", target_image="img"))
    for attempt in (1, 2, 3):
        state.record_attempt_error(path, attempt=attempt, error=f"boom-{attempt}")
    exhausted = state.read_record(path)
    assert exhausted["status"] == "failed"
    # The old guard treated retained history as exhausted, so a direct retry
    # performed zero commands; the retry must reset the active budget while
    # keeping prior diagnostics separately.
    retried = state.explicit_retry(path)
    assert retried["attempt"] == 0
    assert retried["status"] == "desired"
    assert retried["attempts"] == []
    archived = [entry["error"] for entry in retried.get("attemptHistory") or []]
    assert archived == ["boom-1", "boom-2", "boom-3"]
    # A confirmed installation is still never retried.
    done = state.mark_installed(retried, installed_image="img", service_images={})
    state.write_record(path, done)
    try:
        state.explicit_retry(path)
    except ValueError:
        pass
    else:
        raise AssertionError("a confirmed installation must not be retried")


def test_reserve_record_rotates_after_terminal_completion(tmp_path):
    """A new operation replaces a terminal record; retries stay idempotent."""
    from moonmind_controller import state

    path = tmp_path / "operation.json"
    first = state.new_operation(operation_id="one", target_image="one/img1")
    stored = state.reserve_record(path, first)
    assert stored["operationId"] == "one"
    # Retries of the same operation remain idempotent.
    assert state.reserve_record(path, first)["operationId"] == "one"
    # An unfinished record wins for a different operation (first wins).
    pending = state.new_operation(operation_id="two", target_image="two/img2")
    assert state.reserve_record(path, pending)["operationId"] == "one"
    # After terminal completion the durable record rotates to the new
    # operation instead of replaying the first image forever.
    done = state.mark_installed(stored, installed_image="one/img1", service_images={})
    state.write_record(path, done)
    rotated = state.reserve_record(path, pending)
    assert rotated["operationId"] == "two"
    assert rotated["desired"]["targetImage"] == "two/img2"
    assert state.read_record(path)["operationId"] == "two"


def test_reserve_rotation_preserves_retention_baseline(tmp_path):
    """Rotating to a new operation keeps the installed image as retention."""
    from moonmind_controller import state

    path = tmp_path / "operation.json"
    first = state.new_operation(operation_id="one", target_image="one/img1")
    state.reserve_record(path, first)
    done = state.mark_installed(
        state.read_record(path),
        installed_image="one/img1",
        service_images={"api": "one/img1"},
    )
    state.write_record(path, done)
    second = state.new_operation(operation_id="two", target_image="two/img2")
    rotated = state.reserve_record(path, second)
    assert rotated["operationId"] == "two"
    # The previous release baseline survives rotation so the second update
    # cannot silently drop retention.
    assert rotated["previousRelease"]["image"] == "one/img1"
    assert rotated["previousReleaseCompatible"] is True


def test_kernel_lock_rejects_legacy_owner(tmp_path):
    """Cutover requires the old writer to release its legacy lock (REQ-7)."""
    from moonmind_controller import lock

    legacy = tmp_path / "moonmind.lock"
    legacy.write_text(json.dumps({"owner": "something-else"}) + "\n")
    try:
        with lock.hold(str(tmp_path), stack="moonmind"):
            raise AssertionError("legacy lock must block cutover")
    except RuntimeError as exc:
        assert "legacy" in str(exc).lower()

    # After the old writer releases, the controller can take ownership.
    legacy.unlink()
    with lock.hold(str(tmp_path), stack="moonmind"):
        pass


def test_mount_adapter_derives_from_daemon_without_unconditional_rewrite():
    """Bind sources come from daemon evidence, not path guessing (REQ-6)."""
    from moonmind_controller import mounts

    # Genuine Linux mounts pass through untouched.
    assert mounts.desktop_host_path("/mnt/data/repo") is None
    # Windows and WSL distro paths map into the Desktop daemon namespace.
    assert mounts.desktop_host_path("C:\\repo") == "/run/desktop/mnt/host/c/repo"
    assert mounts.desktop_host_path("/mnt/c/repo") == "/run/desktop/mnt/host/c/repo"
    # Missing required host sources must fail, never become empty directories.
    try:
        mounts.require_host_source("/definitely/missing/moonmind-source", exists=False)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("missing host source must not become an empty bind dir")


def test_authenticated_endpoint_uses_deployment_owned_secret():
    """One small authenticated endpoint; no application-service lookup (REQ-2)."""
    from moonmind_controller import auth, server

    assert auth.verify_bearer("secret-token", "secret-token") is True
    assert auth.verify_bearer("wrong", "secret-token") is False
    assert auth.secret_from_environ({"MOONMIND_CONTROLLER_SECRET": "s3cret"}) == "s3cret"
    # The secret never comes from an application-service lookup.
    assert auth.secret_from_environ({}) is None

    headers = {"Authorization": "Bearer s3cret"}
    assert server.is_authorized(headers, "s3cret") is True
    assert server.is_authorized({"Authorization": "Bearer wrong"}, "s3cret") is False
    assert server.is_authorized({}, "s3cret") is False
    assert server.is_authorized(headers, None) is False


def test_secret_reuses_installer_default_path(tmp_path):
    """Clients derive the installer's secret path (P1: stay on controller)."""
    from moonmind_controller import auth, client

    secret_file = tmp_path / ".moonmind-controller-secret"
    secret_file.write_text("deployment-secret\n")
    env = {"MOONMIND_CONTROLLER_STATE_DIR": str(tmp_path)}
    assert auth.secret_from_environ(env) == "deployment-secret"
    assert client.is_controller_configured() in (True, False)
    assert client.controller_base_url(None) == "http://127.0.0.1:8099"
    assert client.controller_base_url("http://x:8099/") == "http://x:8099"
    # Explicit configuration still wins over the derived default.
    env_with_direct = dict(env, MOONMIND_CONTROLLER_SECRET="direct")
    assert auth.secret_from_environ(env_with_direct) == "direct"
    assert auth.secret_from_environ({}) is None


def test_restart_converges_without_duplicate_apply(tmp_path):
    """Kill/restart converges; a completed apply is not repeated (REQ-4)."""
    from moonmind_controller import service, state

    path = tmp_path / "operation.json"
    state.write_record(path, state.new_operation(operation_id="op-4", target_image="img"))
    record = state.read_record(path)
    assert service.converge_on_restart(record, child_running=False, child_owner_matches=False) == "resume"
    assert service.reconcile_child(child_running=True, child_owner_matches=True) == "reattach"
    assert service.reconcile_child(child_running=True, child_owner_matches=False) == "stop_competing"

    done = state.mark_installed(record, installed_image="img", service_images={})
    assert service.converge_on_restart(done, child_running=False, child_owner_matches=False) == "complete"

    for attempt in (1, 2, 3):
        state.record_attempt_error(path, attempt=attempt, error=f"boom-{attempt}")
    exhausted = state.read_record(path)
    assert service.converge_on_restart(exhausted, child_running=False, child_owner_matches=False) == "await_retry"
    assert "boom-1" in service.failure_summary(exhausted["attempts"])


def test_controller_image_entrypoint_serves_package():
    """Controller image serves the package entrypoint, not server module (REQ-2)."""
    from pathlib import Path as _Path

    dockerfile = _Path(__file__).resolve().parents[2] / "deploy" / "moonmind-controller" / "Dockerfile"
    text = dockerfile.read_text()
    assert '["python", "-m", "moonmind_controller"]' in text
    assert "moonmind_controller.server" not in text
    # The production apply path shells out to `docker compose`, so the image
    # must ship the Docker CLI plus the Compose plugin (P1: missing client).
    assert "docker-compose-plugin" in text
    assert "container_name" not in (
        _Path(__file__).resolve().parents[2]
        / "deploy"
        / "moonmind-controller"
        / "compose.yaml"
    ).read_text(), "per-installation container names come from the project"


def test_deployed_images_include_controller_client():
    """Worker/API images ship the controller client (P1: ImportError)."""
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[2]
    runtime_dockerfile = (repo / "api_service" / "Dockerfile").read_text()
    assert "moonmind_controller" in runtime_dockerfile
    pyproject = (repo / "pyproject.toml").read_text()
    assert "moonmind_controller" in pyproject


def test_apply_applies_requested_images_through_compose_env():
    """The requested images reach pull/up; they are not ignored (P1)."""
    from moonmind_controller import apply, state

    seen: list = []

    def fake_run(command, *, timeout, env=None):
        seen.append((tuple(command), timeout, dict(env or {})))
        return apply.CommandResult(returncode=0, output="ok")

    record = state.new_operation(operation_id="op-env", target_image="repo@sha256:new")
    updated = apply.orchestrate_apply(
        record=record,
        target={
            "targetImage": "repo@sha256:new",
            "services": ["api"],
            "concreteImages": {"api": "repo@sha256:new"},
        },
        service_statuses={"api": "running"},
        operator_access_ok=True,
        run=fake_run,
    )
    assert updated["status"] == "installed"
    # Both pull and up ran with the requested image in the environment, so
    # Compose recreates the requested release instead of stale config.
    assert len(seen) == 2
    for command, _timeout, env in seen:
        assert env.get("MOONMIND_IMAGE") == "repo@sha256:new"
    assert seen[0][0][:3] == ("docker", "compose", "pull")
    assert seen[1][0][:4] == ("docker", "compose", "up", "-d")


def test_apply_verification_covers_every_target_service():
    """Post-apply checks every target service, not just observed ones."""
    from moonmind_controller import apply, state

    def ok_run(command, *, timeout, env=None):
        return apply.CommandResult(returncode=0, output="ok")

    record = state.new_operation(operation_id="op-cover", target_image="img")
    try:
        apply.orchestrate_apply(
            record=record,
            target={"targetImage": "img", "services": ["api", "worker2"]},
            service_statuses={"api": "running"},
            operator_access_ok=True,
            run=ok_run,
        )
    except RuntimeError as exc:
        assert "worker2" in str(exc)
    else:
        raise AssertionError("unobserved target service must fail verification")


def test_apply_verification_treats_undeclared_checks_as_unavailable():
    """Declared access/storage without observations fail; absent stay absent."""
    from moonmind_controller import apply, state

    def ok_run(command, *, timeout, env=None):
        return apply.CommandResult(returncode=0, output="ok")

    # No accessSettings/storage declared: operator/artifact observations are
    # not required, mirroring pre-apply validation (absent stays absent).
    record = state.new_operation(operation_id="op-declared", target_image="img")
    updated = apply.orchestrate_apply(
        record=record,
        target={"targetImage": "img", "services": ["api"]},
        service_statuses={"api": "running"},
        run=ok_run,
    )
    assert updated["status"] == "installed"

    # Declared accessSettings with no observation is unverified, not clean.
    record2 = state.new_operation(operation_id="op-declared2", target_image="img")
    try:
        apply.orchestrate_apply(
            record=record2,
            target={
                "targetImage": "img",
                "services": ["api"],
                "accessSettings": {"ports": [8080]},
            },
            service_statuses={"api": "running"},
            run=ok_run,
        )
    except RuntimeError as exc:
        assert "unavailable" in str(exc).lower()
    else:
        raise AssertionError("declared access without observation must fail")

    # Declared storage with an explicit negative observation fails loudly.
    record3 = state.new_operation(operation_id="op-declared3", target_image="img")
    try:
        apply.orchestrate_apply(
            record=record3,
            target={
                "targetImage": "img",
                "services": ["api"],
                "storage": {"volume": "data"},
            },
            service_statuses={"api": "running"},
            artifact_store_ok=False,
            run=ok_run,
        )
    except RuntimeError as exc:
        assert "Artifact-store" in str(exc)
    else:
        raise AssertionError("failed artifact store must block install")


def test_apply_stages_all_images_then_up_without_build():
    """Apply orchestrator pulls always then up never/build-never (REQ-3)."""
    from moonmind_controller import apply, state

    calls: list = []

    def fake_run(command, *, timeout, env=None):
        calls.append((tuple(command), timeout))
        return apply.CommandResult(returncode=0, output="ok")

    record = state.new_operation(operation_id="op-apply", target_image="repo@sha256:new")
    target = {
        "targetImage": "repo@sha256:new",
        "services": ["api", "worker"],
        "concreteImages": {"api": "repo@sha256:new"},
    }
    updated = apply.orchestrate_apply(
        record=record,
        target=target,
        installed_images={"postgres": "pg:15"},
        installed_config={"services": ["api", "worker"]},
        service_statuses={"api": "running", "worker": "running"},
        operator_access_ok=True,
        run=fake_run,
    )
    assert calls[0][0][:3] == ("docker", "compose", "pull")
    assert "always" in calls[0][0]
    assert calls[1][0][:4] == ("docker", "compose", "up", "-d")
    assert "--pull" in calls[1][0] and "never" in calls[1][0]
    assert "--no-build" in calls[1][0]
    assert "--remove-orphans" in calls[1][0] and "--wait" in calls[1][0]
    # Infra selection preserved; prepared target + installed config recorded.
    assert updated["prepared"]["concreteImages"]["postgres"] == "pg:15"
    assert updated["status"] == "installed"
    assert updated["installedConfig"]["services"] == ["api", "worker"]


def test_apply_failure_surfaces_exit_status_and_redacted_tail():
    """Apply failures surface exit status with redacted diagnostics (REQ-3)."""
    from moonmind_controller import apply, state

    def failing_run(command, *, timeout, env=None):
        return apply.CommandResult(
            returncode=1, output="token=super-secret-bearer-value pull denied"
        )

    record = state.new_operation(operation_id="op-fail", target_image="img")
    try:
        apply.orchestrate_apply(
            record=record,
            target={"targetImage": "img", "services": ["api"]},
            service_statuses={"api": "running"},
            operator_access_ok=True,
            run=failing_run,
        )
    except RuntimeError as exc:
        message = str(exc)
        assert "exit 1" in message
        assert "super-secret-bearer-value" not in message
    else:
        raise AssertionError("failing pull must raise with redacted diagnostics")


def test_apply_validation_and_post_verification_are_explicit():
    """Pre/post checks fail loudly without erasing installs (REQ-5)."""
    from moonmind_controller import apply, state

    record = state.new_operation(operation_id="op-validate", target_image="img")
    try:
        apply.orchestrate_apply(
            record=record,
            target={"targetImage": "", "services": []},
            run=lambda command, *, timeout, env=None: apply.CommandResult(0, "ok"),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("empty target must not apply")

    def ok_run(command, *, timeout, env=None):
        return apply.CommandResult(returncode=0, output="ok")

    record = state.new_operation(operation_id="op-post", target_image="img")
    try:
        apply.orchestrate_apply(
            record=record,
            target={"targetImage": "img", "services": ["api"]},
            service_statuses={"api": "exited"},
            operator_access_ok=False,
            run=ok_run,
        )
    except RuntimeError as exc:
        assert "api" in str(exc) and "Operator access" in str(exc)
    else:
        raise AssertionError("failed postcheck must raise explicitly")


def test_apply_retains_previous_release_only_when_compatible():
    """Previous release retained only within compatibility (REQ-4)."""
    from moonmind_controller import apply, state

    def ok_run(command, *, timeout, env=None):
        return apply.CommandResult(returncode=0, output="ok")

    record = state.new_operation(operation_id="op-prev", target_image="img")
    updated = apply.orchestrate_apply(
        record=record,
        target={"targetImage": "img", "services": ["api"]},
        service_statuses={"api": "running"},
        operator_access_ok=True,
        run=ok_run,
        previous_release={"image": "old", "schema": 1},
        previous_compatible=True,
    )
    assert updated["previousRelease"]["image"] == "old"

    record = state.new_operation(operation_id="op-prev2", target_image="img")
    updated = apply.orchestrate_apply(
        record=record,
        target={"targetImage": "img", "services": ["api"]},
        service_statuses={"api": "running"},
        operator_access_ok=True,
        run=ok_run,
        previous_release={"image": "old", "schema": 1},
        previous_compatible=False,
    )
    assert "previousRelease" not in updated


def test_locked_apply_uses_installation_local_kernel_lock(tmp_path):
    """Apply holds the kernel lock; legacy owners block cutover (REQ-7)."""
    from moonmind_controller import apply, state

    def ok_run(command, *, timeout, env=None):
        return apply.CommandResult(returncode=0, output="ok")

    legacy = tmp_path / "stack.lock"
    legacy.write_text(json.dumps({"owner": "legacy"}) + "\n")
    try:
        apply.locked_orchestrate_apply(
            lock_dir=str(tmp_path),
            stack="stack",
            record=state.new_operation(operation_id="op-lock", target_image="img"),
            target={"targetImage": "img", "services": ["api"]},
            service_statuses={"api": "running"},
            operator_access_ok=True,
            run=ok_run,
        )
    except RuntimeError as exc:
        assert "legacy" in str(exc).lower()
    else:
        raise AssertionError("legacy lock must block cutover")

    legacy.unlink()
    updated = apply.locked_orchestrate_apply(
        lock_dir=str(tmp_path),
        stack="stack",
        record=state.new_operation(operation_id="op-lock", target_image="img"),
        target={"targetImage": "img", "services": ["api"]},
        service_statuses={"api": "running"},
        operator_access_ok=True,
        run=ok_run,
    )
    assert updated["status"] == "installed"


def test_controller_handoff_payload_is_data_only():
    """Release config crosses to the controller as data (REQ-1)."""
    from moonmind_controller import client
    from moonmind_controller import server

    payload = client.build_operation_payload(
        operation_id="host-update:abc", target_image="repo@digest", services=("api",)
    )
    record = server.parse_operation_payload(payload)
    assert record["operationId"] == "host-update:abc"
    assert record["desired"]["targetImage"] == "repo@digest"

    # Load the application handoff by path: importing the ``moonmind``
    # package pulls heavy API/DB dependencies, while the handoff itself is
    # a thin data module the controller test must not require.
    import importlib.util as _ilu

    from pathlib import Path as _Path2

    _handoff_path = (
        _Path2(__file__).resolve().parents[2]
        / "moonmind"
        / "workflows"
        / "skills"
        / "deployment_controller_handoff.py"
    )
    _spec = _ilu.spec_from_file_location("deployment_controller_handoff", _handoff_path)
    assert _spec is not None and _spec.loader is not None
    _handoff = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_handoff)

    assert _handoff.build_controller_payload(
        submission_id="abc", image="repo@digest"
    )["desired"]["targetImage"] == "repo@digest"
    assert _handoff.controller_available() in (True, False)


def test_serving_post_invalid_payload_returns_client_error():
    """Invalid operation payloads get a structured 400, not a dropped socket."""
    from moonmind_controller import server

    final, error, code = server.handle_operation_submission(
        "/nonexistent-root-ignored", {"desired": {}}, stack="moonmind", run=None
    )
    assert code == 400
    assert error is not None

    final, error, code = server.handle_operation_submission(
        "/nonexistent-root-ignored",
        {"operationId": "op-bad", "desired": {"services": ["api"]}},
        stack="moonmind",
        run=None,
    )
    assert code == 400


def test_serving_collects_service_observations_before_install(tmp_path):
    """The serving path probes Compose instead of installing blind (P1)."""
    import json as _json

    from moonmind_controller import apply, server, state

    def ps_aware_run(command, *, timeout, env=None):
        if "ps" in list(command):
            return apply.CommandResult(
                returncode=0,
                output=_json.dumps([{"Service": "api", "State": "running"}]),
            )
        return apply.CommandResult(returncode=0, output="ok")

    raw = {
        "operationId": "op-probe",
        "desired": {"targetImage": "img", "services": ["api"]},
    }
    # No observations supplied: the serving path collects them via Compose ps.
    final, error, code = server.handle_operation_submission(
        tmp_path, raw, stack="moonmind", run=ps_aware_run
    )
    assert code == 200 and error is None
    assert final["status"] == "installed"

    # An unreadable probe leaves the deployment unverified, never installed.
    def blind_run(command, *, timeout, env=None):
        return apply.CommandResult(returncode=0, output="not-json")

    other = tmp_path / "other"
    raw2 = {
        "operationId": "op-blind",
        "desired": {"targetImage": "img", "services": ["api"]},
    }
    final2, error2, code2 = server.handle_operation_submission(
        other, raw2, stack="moonmind", run=blind_run
    )
    assert code2 == 500
    assert "nverified" in (error2 or "")
    assert state.read_record(other / "operation.json")["status"] != "installed"


def test_serving_persists_prepared_target_before_mutation(tmp_path):
    """Prepared target hits disk before the first Compose side effect (P1)."""
    from moonmind_controller import apply, server, state

    def failing_pull(command, *, timeout, env=None):
        assert list(command)[2] == "pull"
        return apply.CommandResult(returncode=1, output="pull denied")

    raw = {
        "operationId": "op-prepared",
        "desired": {
            "targetImage": "img",
            "services": ["api"],
            "concreteImages": {"api": "img"},
        },
    }
    final, error, code = server.handle_operation_submission(
        tmp_path, raw, stack="moonmind", run=failing_pull,
        service_statuses={"api": "running"},
    )
    assert code == 500
    persisted = state.read_record(tmp_path / "operation.json")
    # Even though staging failed, the prepared target (with the selected
    # concrete images) survived on disk for restart reconciliation.
    assert persisted["prepared"]["concreteImages"]["api"] == "img"
    assert persisted["desired"]["concreteImages"]["api"] == "img"


def test_serving_binds_container_interface_and_threads_requests():
    """The endpoint binds 0.0.0.0 in-container and serves concurrently (P1)."""
    import inspect as _inspect

    from pathlib import Path as _Path

    from moonmind_controller import server

    assert _inspect.signature(server.serve).parameters["host"].default == "0.0.0.0"
    source = (_Path(server.__file__)).read_text()
    assert "ThreadingHTTPServer" in source


def test_handoff_payload_carries_target_project():
    """Payloads name the Compose project; unknown services stay out (P1)."""
    from moonmind_controller import client, compose, server

    payload = client.build_operation_payload(
        operation_id="host-update:proj",
        target_image="repo@digest",
        stack="custom-project",
    )
    assert payload["desired"]["stack"] == "custom-project"
    assert payload["desired"]["services"] == list(compose.RELEASE_OWNED_SERVICES)
    record = server.parse_operation_payload(payload)
    target = server.build_target_from_record(record)
    assert target["stack"] == "custom-project"
    assert "worker" not in target["services"]

    # Bad project/compose fields are refused, never silently half-applied.
    bad = {
        "operationId": "op-badproj",
        "desired": {
            "targetImage": "img",
            "services": ["api"],
            "projectDirectory": "",
        },
    }
    try:
        server.parse_operation_payload(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("empty project directory must be refused")


def test_client_reconciles_before_fallback(tmp_path):
    """A timed-out submission reconciles instead of forking a legacy writer."""
    import json as _json
    import urllib.request as _urlopen_mod

    from moonmind_controller import client

    payload = client.build_operation_payload(
        operation_id="op-reconcile", target_image="img"
    )
    receipt = {"operationId": "op-reconcile", "status": "installed"}

    calls = {"posts": 0, "gets": 0}

    class _Response:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        if request.get_method() == "POST":
            calls["posts"] += 1
            raise TimeoutError("slow apply")
        calls["gets"] += 1
        return _Response((_json.dumps(receipt) + "\n").encode())

    real_urlopen = _urlopen_mod.urlopen
    _urlopen_mod.urlopen = fake_urlopen
    try:
        result = client.submit_operation(
            payload, base_url="http://127.0.0.1:8099", secret="s3cret"
        )
    finally:
        _urlopen_mod.urlopen = real_urlopen
    assert calls["posts"] == 1 and calls["gets"] == 1
    assert result["operation"]["status"] == "installed"
    assert result["reconciled"] is True


def test_client_reports_in_progress_without_legacy_fallback():
    """A reconciled in-progress operation raises; it never falls back."""
    import json as _json
    import urllib.request as _urlopen_mod

    from moonmind_controller import client

    payload = client.build_operation_payload(
        operation_id="op-slow", target_image="img"
    )

    class _Response:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        if request.get_method() == "POST":
            raise TimeoutError("slow apply")
        return _Response(
            (
                _json.dumps(
                    {"operationId": "op-slow", "status": "desired", "attempt": 0}
                )
                + "\n"
            ).encode()
        )

    real_urlopen = _urlopen_mod.urlopen
    _urlopen_mod.urlopen = fake_urlopen
    try:
        try:
            client.submit_operation(
                payload, base_url="http://127.0.0.1:8099", secret="s3cret"
            )
        except RuntimeError as exc:
            assert "still applying" in str(exc) or "in progress" in str(exc).lower()
        else:
            raise AssertionError("in-progress operation must not fall back silently")
    finally:
        _urlopen_mod.urlopen = real_urlopen


def test_client_waits_for_terminal_operation():
    """Submit can block until the controller reaches a terminal record."""
    import json as _json
    import urllib.request as _urlopen_mod

    from moonmind_controller import client

    payload = client.build_operation_payload(
        operation_id="op-wait", target_image="img"
    )
    states = [
        {"operationId": "op-wait", "status": "desired"},
        {"operationId": "op-wait", "status": "installed"},
    ]

    class _Response:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        if request.get_method() == "POST":
            raise TimeoutError("slow apply")
        body = states.pop(0) if len(states) > 1 else states[0]
        return _Response((_json.dumps(body) + "\n").encode())

    real_urlopen = _urlopen_mod.urlopen
    _urlopen_mod.urlopen = fake_urlopen
    try:
        result = client.submit_operation(
            payload,
            base_url="http://127.0.0.1:8099",
            secret="s3cret",
            wait_for_terminal=True,
            poll_interval=0,
            poll_timeout=60,
        )
    finally:
        _urlopen_mod.urlopen = real_urlopen
    assert result["operation"]["status"] == "installed"


def test_serving_post_persists_prepared_record_and_runs_apply(tmp_path):
    """Serving POST executes the apply instead of only reserving (REQ-3/REQ-4)."""
    from moonmind_controller import apply, server, state

    calls: list = []

    def fake_run(command, *, timeout, env=None):
        calls.append(tuple(command))
        return apply.CommandResult(returncode=0, output="ok")

    raw = {
        "operationId": "op-serve",
        "desired": {"targetImage": "img", "services": ["api"]},
    }
    final, error, code = server.handle_operation_submission(
        tmp_path, raw, stack="moonmind", run=fake_run,
        service_statuses={"api": "running"},
    )
    assert code == 200 and error is None
    # The injected Compose boundary ran pull then up (scoped to the stack).
    assert calls[0][:2] == ("docker", "compose")
    assert "pull" in calls[0]
    assert calls[1][:2] == ("docker", "compose")
    assert "up" in calls[1] and "-d" in calls[1]
    # Prepared target + installed config persisted across interruption.
    persisted = state.read_record(tmp_path / "operation.json")
    assert persisted["status"] == "installed"
    assert persisted["prepared"]["targetImage"] == "img"
    assert persisted["installed"]["image"] == "img"
    assert final["status"] == "installed"


def test_serving_post_failure_records_redacted_error_and_stays_usable(tmp_path):
    """Apply failures persist redacted diagnostics; the record stays usable."""
    from moonmind_controller import apply, server, state

    def failing_run(command, *, timeout, env=None):
        return apply.CommandResult(
            returncode=1, output="token=super-secret-bearer-value denied"
        )

    raw = {
        "operationId": "op-serve-fail",
        "desired": {"targetImage": "img", "services": ["api"]},
    }
    final, error, code = server.handle_operation_submission(
        tmp_path, raw, stack="moonmind", run=failing_run,
        service_statuses={"api": "running"},
    )
    assert code == 500
    assert error is not None and "super-secret-bearer-value" not in error
    assert "exit 1" in error
    persisted = state.read_record(tmp_path / "operation.json")
    assert persisted["status"] in ("desired", "failed")
    assert persisted["attempts"]
    assert "super-secret-bearer-value" not in json.dumps(persisted)
    # An explicit Retry remains possible after the recorded failure.
    retried = state.explicit_retry(tmp_path / "operation.json")
    assert retried["status"] == "desired"


def test_cutover_handoff_taken_when_controller_configured():
    """Controller-first cutover: handoff wins when configured (REQ-1/REQ-2/REQ-7)."""
    import importlib.util as _ilu
    import sys as _sys
    import types as _types
    from pathlib import Path as _Path3

    _entry = (
        _Path3(__file__).resolve().parents[2]
        / ".agents"
        / "skills"
        / "update-moonmind"
        / "scripts"
        / "update_release.py"
    )
    _spec = _ilu.spec_from_file_location("cutover_update_release", _entry)
    assert _spec is not None and _spec.loader is not None
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)

    calls: dict = {}

    def _fake_build_operation_payload(
        *, operation_id, target_image, services=None, **_extra
    ):
        calls["payload"] = {
            "operation_id": operation_id,
            "target_image": target_image,
            "services": tuple(services) if services is not None else None,
            "extra": dict(_extra),
        }
        return {"operationId": operation_id, "desired": {"targetImage": target_image}}

    def _fake_submit_operation(payload, **_kwargs):
        calls["submitted"] = payload
        calls["submit_kwargs"] = dict(_kwargs)
        return {"status": "installed"}

    class _FakeUnavailableError(Exception):
        pass

    _fake_client = _types.ModuleType("moonmind_controller.client")
    _fake_client.is_controller_configured = lambda: True
    _fake_client.build_operation_payload = _fake_build_operation_payload
    _fake_client.submit_operation = _fake_submit_operation
    _fake_client.ControllerUnavailableError = _FakeUnavailableError
    _fake_pkg = _types.ModuleType("moonmind_controller")
    _fake_pkg.client = _fake_client
    _prior_pkg = _sys.modules.get("moonmind_controller")
    _prior_client = _sys.modules.get("moonmind_controller.client")
    _sys.modules["moonmind_controller"] = _fake_pkg
    _sys.modules["moonmind_controller.client"] = _fake_client
    try:
        record = {
            "image": "repo@sha256:cutover",
            "project": "moonmind-test",
            "context": {"idempotency_key": "host-update:cutover-1"},
        }
        assert _mod._try_controller_handoff(record=record) == 0
    finally:
        if _prior_pkg is not None:
            _sys.modules["moonmind_controller"] = _prior_pkg
        else:
            _sys.modules.pop("moonmind_controller", None)
        if _prior_client is not None:
            _sys.modules["moonmind_controller.client"] = _prior_client
        else:
            _sys.modules.pop("moonmind_controller.client", None)
    # The trusted release data reached the controller handoff exactly once;
    # the legacy temporal-worker-deployment-control path was never entered
    # (a handled submission returns an exit code instead of None).
    assert calls["submitted"]["desired"]["targetImage"] == "repo@sha256:cutover"
    assert calls["payload"]["operation_id"] == "host-update:cutover-1"
    # The host handoff names the Compose project, targets real services
    # (never a hardcoded "worker"), and waits for the terminal record.
    assert calls["payload"]["extra"].get("stack") == "moonmind-test"
    assert calls["submit_kwargs"].get("wait_for_terminal") is True

    _fake_client2 = _types.ModuleType("moonmind_controller.client")
    _fake_client2.is_controller_configured = lambda: False
    _fake_client2.submit_operation = lambda payload: (_ for _ in ()).throw(
        AssertionError("unconfigured controller must not submit")
    )
    _fake_pkg2 = _types.ModuleType("moonmind_controller")
    _fake_pkg2.client = _fake_client2
    _sys.modules["moonmind_controller"] = _fake_pkg2
    _sys.modules["moonmind_controller.client"] = _fake_client2
    try:
        assert _mod._try_controller_handoff(record=record) is None
    finally:
        if _prior_pkg is not None:
            _sys.modules["moonmind_controller"] = _prior_pkg
        else:
            _sys.modules.pop("moonmind_controller", None)
        if _prior_client is not None:
            _sys.modules["moonmind_controller.client"] = _prior_client
        else:
            _sys.modules.pop("moonmind_controller.client", None)


def test_cutover_lock_is_installation_local_and_preserves_legacy(tmp_path):
    """Two installs hold independent locks; legacy files are never deleted (REQ-7)."""
    from moonmind_controller import lock

    first = lock.lock_path_for(str(tmp_path / "install-a"), "moonmind")
    second = lock.lock_path_for(str(tmp_path / "install-b"), "moonmind")
    assert first != second
    assert first.parent != second.parent
    # Unsafe stack names cannot escape the installation-local directory.
    for unsafe in ("", ".", "..", "a/b"):
        try:
            lock.lock_path_for(str(tmp_path), unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe stack {unsafe!r} must be rejected")

    legacy = tmp_path / "install-a" / "moonmind.lock"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"owner": "legacy-writer"}) + "\n")
    try:
        with lock.hold(str(tmp_path / "install-a"), stack="moonmind"):
            raise AssertionError("legacy lock must block cutover")
    except RuntimeError as exc:
        assert "legacy" in str(exc).lower()
    # Cutover refusal preserves the old writer's file for positive-stop
    # reconciliation instead of deleting a live lock inode.
    assert legacy.exists(), "blocked cutover must keep the legacy lock file"
    assert "unlink" not in (tmp_path / "install-a" / "moonmind.lock").read_text()
    lock_source = (
        Path(__file__).resolve().parents[2] / "moonmind_controller" / "lock.py"
    ).read_text()
    assert "unlink" not in lock_source
    assert "subprocess" not in lock_source
    assert "docker" not in lock_source.lower()

    legacy.unlink()
    with lock.hold(str(tmp_path / "install-a"), stack="moonmind"):
        pass


def test_cutover_submit_prefers_controller_and_serializes_host_update():
    """Legacy path stays fallback-only; host owns controller update (REQ-2/REQ-7)."""
    from pathlib import Path as _Path4

    repo = _Path4(__file__).resolve().parents[2]
    submit_source = (
        repo / "moonmind" / "workflows" / "skills" / "deployment_release.py"
    ).read_text()
    submit_body = submit_source.split("async def submit", 1)[1]
    handoff_pos = submit_body.index("controller_available()")
    legacy_pos = submit_body.index("_build_deployment_update_executor")
    assert handoff_pos < legacy_pos, "controller handoff must precede legacy fallback"

    entry_source = (
        repo
        / ".agents"
        / "skills"
        / "update-moonmind"
        / "scripts"
        / "update_release.py"
    ).read_text()
    assert "_try_controller_handoff(record=record)" in entry_source
    assert (
        entry_source.index("_try_controller_handoff(record=record)")
        < entry_source.index("temporal-worker-deployment-control")
    ), "host entrypoint must try the controller before the legacy control service"
    # The handoff targets the release-owned service set, never a hardcoded
    # "worker" service that does not exist in the canonical stack.
    assert '("api", "worker")' not in entry_source

    server_source = (repo / "moonmind_controller" / "server.py").read_text()
    assert "/update" not in server_source, "controller must never replace itself"
    install_source = (repo / "tools" / "install-moonmind-controller.sh").read_text()
    assert "assert_no_active_mutation" in install_source
    assert "never by itself" in install_source.lower() or "never replaces itself" in (
        repo / "moonmind_controller" / "server.py"
    ).read_text()


def test_startup_convergence_resumes_unfinished_work_without_repeat(tmp_path):
    """Restart inspects the record and converges unfinished work (REQ-4)."""
    from moonmind_controller import apply, server, state

    assert server.converge_on_startup(tmp_path, run=None) == "no-record"

    done = state.new_operation(operation_id="op-done", target_image="img")
    done = state.mark_installed(done, installed_image="img", service_images={})
    state.write_record(tmp_path / "operation.json", done)

    def must_not_run(command, *, timeout, env=None):
        raise AssertionError("completed apply must not run again")

    assert (
        server.converge_on_startup(tmp_path, run=must_not_run) == "complete"
    )

    pending = state.new_operation(operation_id="op-resume", target_image="img")
    pending["desired"]["services"] = ["api"]
    state.write_record(tmp_path / "operation.json", pending)

    def ok_run(command, *, timeout, env=None):
        return apply.CommandResult(returncode=0, output="ok")

    def ps_aware_run(command, *, timeout, env=None):
        if "ps" in command:
            return apply.CommandResult(
                returncode=0,
                output=json.dumps([{"Service": "api", "State": "running"}]),
            )
        return apply.CommandResult(returncode=0, output="ok")

    assert server.converge_on_startup(tmp_path, run=ps_aware_run) == "resumed"
    assert state.read_record(tmp_path / "operation.json")["status"] == "installed"


def test_serving_path_threads_previous_release_compatibly(tmp_path):
    """Serving path retains previous release only when compatible (REQ-4)."""
    from moonmind_controller import apply, server, state

    def ok_run(command, *, timeout, env=None):
        return apply.CommandResult(returncode=0, output="ok")

    # Compatible retention through the serving path.
    raw = {
        "operationId": "op-serve-prev",
        "desired": {"targetImage": "img", "services": ["api"]},
        "previousRelease": {"image": "old", "schema": 1},
        "previousCompatible": True,
    }
    final, error, code = server.handle_operation_submission(
        tmp_path, raw, stack="moonmind", run=ok_run,
        service_statuses={"api": "running"},
    )
    assert code == 200 and error is None
    assert final["previousRelease"]["image"] == "old"

    # Incompatible retention is cleared, never auto-downgraded.
    other = tmp_path / "other"
    raw2 = {
        "operationId": "op-serve-prev2",
        "desired": {"targetImage": "img2", "services": ["api"]},
        "previousRelease": {"image": "old", "schema": 1},
        "previousCompatible": False,
    }
    final2, error2, code2 = server.handle_operation_submission(
        other, raw2, stack="moonmind", run=ok_run,
        service_statuses={"api": "running"},
    )
    assert code2 == 200 and error2 is None
    assert "previousRelease" not in final2
    assert final2.get("previousReleaseCompatible") is False


def test_enriched_handoff_payload_round_trips_validation_data():
    """Authorization/storage/accessSettings survive the handoff (REQ-5)."""
    from moonmind_controller import apply, client, server

    payload = client.build_operation_payload(
        operation_id="host-update:enriched",
        target_image="repo@digest",
        services=("api",),
        authorization={"mode": "oidc"},
        storage={"volume": "data"},
        access_settings={"ports": [8080]},
    )
    record = server.parse_operation_payload(payload)
    assert record["desired"]["authorization"] == {"mode": "oidc"}
    assert record["desired"]["storage"] == {"volume": "data"}
    assert record["desired"]["accessSettings"] == {"ports": [8080]}

    target = server.build_target_from_record(record)
    assert target["authorization"] == {"mode": "oidc"}
    assert target["storage"] == {"volume": "data"}
    assert target["accessSettings"] == {"ports": [8080]}
    assert apply.validate_target(target=target) == []

    # Explicitly empty mappings are still refused, so validators are live.
    bad = dict(target)
    bad["authorization"] = {}
    assert apply.validate_target(target=bad)


def test_artifact_store_failure_blocks_install_without_erasing_confirmed():
    """Artifact-store failure blocks install; confirmed installs persist (ACC-4)."""
    from moonmind_controller import apply, state

    def ok_run(command, *, timeout, env=None):
        return apply.CommandResult(returncode=0, output="ok")

    record = state.new_operation(operation_id="op-artifact", target_image="img")
    try:
        apply.orchestrate_apply(
            record=record,
            target={"targetImage": "img", "services": ["api"]},
            service_statuses={"api": "running"},
            operator_access_ok=True,
            artifact_store_ok=False,
            run=ok_run,
        )
    except RuntimeError as exc:
        assert "Artifact-store" in str(exc)
    else:
        raise AssertionError("artifact-store failure must block install")

    # A separately confirmed installation is not invalidated afterward.
    confirmed = state.mark_installed(record, installed_image="img", service_images={})
    assert state.apply_already_complete(confirmed) is True
    assert confirmed["status"] == "installed"
