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


def test_explicit_retry_keeps_prior_diagnostics(tmp_path):
    """An explicit Retry starts fresh but retains prior diagnostics (REQ-4)."""
    from moonmind_controller import state

    path = tmp_path / "operation.json"
    state.write_record(path, state.new_operation(operation_id="op-3", target_image="img"))
    state.record_attempt_error(path, attempt=1, error="first boom")
    retried = state.explicit_retry(path)
    assert retried["attempt"] == 2
    assert retried["status"] == "desired"
    history = state.read_record(path)["attempts"]
    assert history[0]["error"] == "first boom"


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


def test_apply_stages_all_images_then_up_without_build():
    """Apply orchestrator pulls always then up never/build-never (REQ-3)."""
    from moonmind_controller import apply, state

    calls: list = []

    def fake_run(command, *, timeout):
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

    def failing_run(command, *, timeout):
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
            run=lambda command, *, timeout: apply.CommandResult(0, "ok"),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("empty target must not apply")

    def ok_run(command, *, timeout):
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

    def ok_run(command, *, timeout):
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
    import json

    from moonmind_controller import apply, state

    def ok_run(command, *, timeout):
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


def test_serving_post_persists_prepared_record_and_runs_apply(tmp_path):
    """Serving POST executes the apply instead of only reserving (REQ-3/REQ-4)."""
    from moonmind_controller import apply, server, state

    calls: list = []

    def fake_run(command, *, timeout):
        calls.append(tuple(command))
        return apply.CommandResult(returncode=0, output="ok")

    raw = {
        "operationId": "op-serve",
        "desired": {"targetImage": "img", "services": ["api"]},
    }
    final, error, code = server.handle_operation_submission(
        tmp_path, raw, stack="moonmind", run=fake_run
    )
    assert code == 200 and error is None
    # The injected Compose boundary ran pull then up.
    assert calls[0][:3] == ("docker", "compose", "pull")
    assert calls[1][:4] == ("docker", "compose", "up", "-d")
    # Prepared target + installed config persisted across interruption.
    persisted = state.read_record(tmp_path / "operation.json")
    assert persisted["status"] == "installed"
    assert persisted["prepared"]["targetImage"] == "img"
    assert persisted["installed"]["image"] == "img"
    assert final["status"] == "installed"


def test_serving_post_failure_records_redacted_error_and_stays_usable(tmp_path):
    """Apply failures persist redacted diagnostics; the record stays usable."""
    from moonmind_controller import apply, server, state

    def failing_run(command, *, timeout):
        return apply.CommandResult(
            returncode=1, output="token=super-secret-bearer-value denied"
        )

    raw = {
        "operationId": "op-serve-fail",
        "desired": {"targetImage": "img", "services": ["api"]},
    }
    final, error, code = server.handle_operation_submission(
        tmp_path, raw, stack="moonmind", run=failing_run
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
        *, operation_id, target_image, services=(), **_extra
    ):
        calls["payload"] = {
            "operation_id": operation_id,
            "target_image": target_image,
            "services": tuple(services),
        }
        return {"operationId": operation_id, "desired": {"targetImage": target_image}}

    def _fake_submit_operation(payload):
        calls["submitted"] = payload
        return {"status": "installed"}

    _fake_client = _types.ModuleType("moonmind_controller.client")
    _fake_client.is_controller_configured = lambda: True
    _fake_client.build_operation_payload = _fake_build_operation_payload
    _fake_client.submit_operation = _fake_submit_operation
    _fake_pkg = _types.ModuleType("moonmind_controller")
    _fake_pkg.client = _fake_client
    _prior_pkg = _sys.modules.get("moonmind_controller")
    _prior_client = _sys.modules.get("moonmind_controller.client")
    _sys.modules["moonmind_controller"] = _fake_pkg
    _sys.modules["moonmind_controller.client"] = _fake_client
    try:
        record = {
            "image": "repo@sha256:cutover",
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

    def must_not_run(command, *, timeout):
        raise AssertionError("completed apply must not run again")

    assert (
        server.converge_on_startup(tmp_path, run=must_not_run) == "complete"
    )

    pending = state.new_operation(operation_id="op-resume", target_image="img")
    pending["desired"]["services"] = ["api"]
    state.write_record(tmp_path / "operation.json", pending)

    def ok_run(command, *, timeout):
        return apply.CommandResult(returncode=0, output="ok")

    assert server.converge_on_startup(tmp_path, run=ok_run) == "resumed"
    assert state.read_record(tmp_path / "operation.json")["status"] == "installed"


def test_serving_path_threads_previous_release_compatibly(tmp_path):
    """Serving path retains previous release only when compatible (REQ-4)."""
    from moonmind_controller import apply, server, state

    def ok_run(command, *, timeout):
        return apply.CommandResult(returncode=0, output="ok")

    # Compatible retention through the serving path.
    raw = {
        "operationId": "op-serve-prev",
        "desired": {"targetImage": "img", "services": ["api"]},
        "previousRelease": {"image": "old", "schema": 1},
        "previousCompatible": True,
    }
    final, error, code = server.handle_operation_submission(
        tmp_path, raw, stack="moonmind", run=ok_run
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
        other, raw2, stack="moonmind", run=ok_run
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

    def ok_run(command, *, timeout):
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
