"""Unit tests for the standalone deployment controller (#4500).

The controller package is stdlib-only: these tests load it directly from
``deploy/controller`` so no ``moonmind.*`` import can sneak in (the
import-boundary test fails the suite if it does).
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

CONTROLLER_SRC = Path(__file__).resolve().parents[3] / "deploy" / "controller"

sys.path.insert(0, str(CONTROLLER_SRC))

from mm_controller import auth, compose_plan, lifecycle, mounts  # noqa: E402
from mm_controller.compose_plan import PlanRequest  # noqa: E402
from mm_controller.controller import Controller, ControllerError, extract_release_config  # noqa: E402
from mm_controller.kernel_lock import KernelLockManager, LegacyOwnerActive, LockUnavailable  # noqa: E402
from mm_controller.redact import bound_tail, redact_text, redact_value  # noqa: E402
from mm_controller.store import OperationRecord, OperationStore  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return OperationStore(tmp_path / "state")


class FakeRunner:
    """Injected Docker boundary: no daemon needed."""

    def __init__(self, *, pull=(0, "pulled"), up=(0, "up ok")):
        self.pull_result = pull
        self.up_result = up
        self.commands: list[tuple[str, ...]] = []
        self.reconciled = False
        self.images: dict[str, str] = {}

    def pull(self, command):
        self.commands.append(command)
        return self.pull_result

    def up(self, command):
        self.commands.append(command)
        return self.up_result

    def running_images(self):
        return dict(self.images)

    def reconcile_child(self):
        self.reconciled = True
        return "none"


def allowed_pre(image="ghcr.io/moonladderstudios/moonmind:v1"):
    return lifecycle.validate_before_apply(
        config=lifecycle.TargetConfig(target_image=image),
        authorized=True,
        compose_valid=True,
        storage_ready=True,
        access_preserved=True,
    )


# -- R1: import boundary -------------------------------------------------

def test_controller_modules_import_no_moonmind_application_packages():
    for module_file in sorted((CONTROLLER_SRC / "mm_controller").glob("*.py")):
        tree = ast.parse(module_file.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        forbidden = imports.intersection({
            "moonmind", "api_service", "temporal", "omnigents", "omnigent",
        })
        assert not forbidden, f"{module_file.name} imports {sorted(forbidden)}"


def test_release_config_extracted_as_data_not_application_bootstrap():
    config = extract_release_config({
        "targetImage": "ghcr.io/moonladderstudios/moonmind:v2",
        "services": ["api", "worker"],
        "projectName": "moonmind",
        "pythonBootstrap": "should-be-ignored",
    })
    assert config["targetImage"].endswith(":v2")
    assert "pythonBootstrap" not in config
    with pytest.raises(ControllerError):
        extract_release_config({"services": []})


# -- R3: pull-then-apply semantics ----------------------------------------

def test_default_plan_is_staged_pull_then_changed_service_up():
    plan = compose_plan.build_plan(services=("api",))
    assert plan.pull_command[:4] == ("docker", "compose", "pull", "--policy")
    assert "always" in plan.pull_command
    up = plan.up_command
    assert up[:3] == ("docker", "compose", "up")
    for flag in ("-d", "--pull", "never", "--no-build", "--remove-orphans", "--wait"):
        assert flag in up
    assert "down" not in plan.pull_command and "down" not in up


def test_down_and_force_recreate_require_explicit_repair():
    errors = compose_plan.validate_request(
        PlanRequest(target_image="img:v1", mode="down"))
    assert any("explicit repair" in e for e in errors)
    errors = compose_plan.validate_request(
        PlanRequest(target_image="img:v1", mode="force_recreate"))
    assert any("explicit repair" in e for e in errors)
    assert compose_plan.validate_request(
        PlanRequest(target_image="img:v1", mode="down", explicit_repair=True)) == []


def test_ordinary_update_excludes_untouched_infrastructure():
    selected = compose_plan.changed_services_for_target(
        configured_services=("api", "worker", "postgres", "temporal", "minio"),
        image_services=("api", "worker"),
    )
    assert selected == ("api", "worker")
    errors = compose_plan.validate_request(
        PlanRequest(target_image="img:v1", services=("api", "postgres")))
    assert any("infrastructure" in e for e in errors)


def test_pull_failure_leaves_containers_untouched(store):
    runner = FakeRunner(pull=(1, "registry unreachable"))
    with pytest.raises(ControllerError) as excinfo:
        Controller(store=store, runner=runner).update(
            PlanRequest(target_image="img:v1", services=("api",)), pre=allowed_pre())
    assert excinfo.value.exit_code == 1
    # Pull failed: no up command was ever issued, no prepared/installed state.
    assert all(cmd[2] != "up" for cmd in runner.commands)
    assert store.read_prepared() is None
    assert store.read_installed() is None


def test_apply_failure_surfaces_exit_status_and_redacted_tail(store):
    runner = FakeRunner(up=(1, "compose up failed token=abc123"))
    with pytest.raises(ControllerError) as excinfo:
        Controller(store=store, runner=runner).update(
            PlanRequest(target_image="img:v1", services=("api",)), pre=allowed_pre("img:v1"))
    assert excinfo.value.exit_code == 1
    assert "abc123" not in excinfo.value.log_tail
    record = store.load_operation()
    assert record is not None and record.status == "FAILED"
    assert store.read_installed() is None  # failure never confirms installation


# -- R4: operation record / restart / retry --------------------------------

def test_successful_update_records_prepared_then_installed(store):
    ctrl = Controller(store=store, runner=FakeRunner())
    result = ctrl.update(
        PlanRequest(target_image="img:v1", services=("api",)), pre=allowed_pre("img:v1"))
    assert result.status == "SUCCEEDED"
    assert store.read_prepared()["targetImage"] == "img:v1"
    assert store.read_installed()["targetImage"] == "img:v1"


def test_restart_after_completed_apply_does_not_repeat_apply(store):
    runner = FakeRunner()
    ctrl = Controller(store=store, runner=runner)
    ctrl.update(PlanRequest(target_image="img:v1", services=("api",)), pre=allowed_pre("img:v1"))
    up_calls = sum(1 for c in runner.commands if c[2] == "up")
    assert ctrl.recover_on_restart() == "settled"
    assert sum(1 for c in runner.commands if c[2] == "up") == up_calls  # no repeat


def test_exhaustion_then_explicit_retry_keeps_history(store):
    runner = FakeRunner(up=(1, "boom"))
    ctrl = Controller(store=store, runner=runner)
    for _ in range(3):
        with pytest.raises(ControllerError):
            ctrl.update(PlanRequest(target_image="img:v1", services=("api",)), pre=allowed_pre("img:v1"))
    with pytest.raises(ControllerError) as excinfo:
        ctrl.update(PlanRequest(target_image="img:v1", services=("api",)), pre=allowed_pre("img:v1"))
    assert "explicit Retry" in str(excinfo.value)
    history = len(store.load_operation().attempts)
    record = store.explicit_retry(target_image="img:v1")
    assert len(record.attempts) > history - 1  # prior diagnostics retained
    assert record.status == "PENDING"


def test_failed_verification_does_not_erase_confirmed_install(store):
    runner = FakeRunner()
    ctrl = Controller(store=store, runner=runner)
    ctrl.update(PlanRequest(target_image="img:v1", services=("api",)), pre=allowed_pre("img:v1"))
    before = store.read_installed()
    result = ctrl.update(
        PlanRequest(target_image="img:v2", services=("api",)), pre=allowed_pre("img:v2"),
        post_checks=(lifecycle.CheckResult("svc", False, True, detail="probe failed"),),
    )
    assert result.status == "FAILED"
    assert store.read_installed() == before  # reporting path kept confirmed install


# -- R5: validation / verification ------------------------------------------

def test_old_health_is_diagnostic_not_admission():
    verdict = lifecycle.validate_before_apply(
        config=lifecycle.TargetConfig(target_image="img:v1"),
        authorized=True, compose_valid=True, storage_ready=True, access_preserved=True,
        old_health=lifecycle.CheckResult("old_api", False, True, detail="old API down"),
    )
    assert verdict.allowed  # repair proceeds with the old stack down


def test_missing_mandatory_evidence_never_succeeds():
    verdict = lifecycle.verify_after_apply(checks=(
        lifecycle.CheckResult("svc", True, True),
        lifecycle.CheckResult("dispatch", False, True, detail="unavailable: store down"),
    ))
    assert verdict.status == "PARTIALLY_VERIFIED"


def test_redaction_covers_logs_and_state():
    assert "hunter2" not in redact_text("login with password=hunter2 ok")
    assert "hunter2" not in redact_value({"env": "token=hunter2"})
    assert redact_value({"password": "x"}, "password") == "[REDACTED]"
    long_text = "start\n" + "x" * 10000 + "\nRuntimeError: boom\n"
    assert "RuntimeError: boom" in bound_tail(long_text)


# -- R7: kernel lock ----------------------------------------------------------

def test_kernel_lock_contract_and_legacy_protection(tmp_path):
    manager = KernelLockManager(lock_dir=str(tmp_path))
    with manager.acquire("moonmind", wait_seconds=0):
        with pytest.raises(LockUnavailable):
            manager.acquire("moonmind", wait_seconds=0)
    # Legacy payload blocks cutover until the old writer releases ownership.
    lock_file = tmp_path / "moonmind.lock"
    lock_file.write_text(json.dumps({"owner": "legacy-controller"}), encoding="utf-8")
    with pytest.raises(LegacyOwnerActive):
        manager.acquire("moonmind", wait_seconds=0)
    # Installation-local: a second installation on the same daemon is independent.
    other = KernelLockManager(lock_dir=str(tmp_path / "other-install"))
    with other.acquire("moonmind", wait_seconds=0):
        pass


# -- R6: mounts ----------------------------------------------------------------

def test_desktop_host_paths_and_wsl_rules():
    assert mounts.docker_desktop_host_path("C:\\repo\\x") == "/run/desktop/mnt/host/c/repo/x"
    assert mounts.docker_desktop_host_path("/mnt/c/repo") == "/run/desktop/mnt/host/c/repo"
    assert mounts.docker_desktop_host_path("/mnt/data") is None  # genuine Linux mount
    assert mounts.docker_desktop_host_path("/srv/repo") is None
    # Daemon evidence always wins over shape inference.
    assert mounts.resolve_bind_source(
        configured="/mnt/c/repo", daemon_evidence="/mnt/c/repo") == "/mnt/c/repo"
    # Confirmed non-Desktop daemon keeps POSIX for bare /mnt/<drive>.
    assert mounts.resolve_bind_source(
        configured="/mnt/c/repo", daemon_evidence=None, desktop_daemon=False) == "/mnt/c/repo"
    # Unknown platform rewrites so a miss fails loudly, never empty-mounts.
    assert mounts.resolve_bind_source(
        configured="/mnt/c/repo", daemon_evidence=None, desktop_daemon=None
    ).startswith("/run/desktop/mnt/host/")
    spec = mounts.bind_spec(source="/srv/x", target="/app")
    assert spec["bind"] == {"create_host_path": False}
    scoped = mounts.orphan_cleanup_scoped(project_name="moonmind", protected_labels=("job",))
    assert scoped["globalPrune"] is False


# -- R2: auth -------------------------------------------------------------------

def test_deployment_owned_secret_roundtrip_and_hmac(tmp_path, monkeypatch):
    monkeypatch.delenv("MOONMIND_CONTROLLER_SECRET", raising=False)
    first = auth.load_or_create_secret(tmp_path)
    assert auth.load_or_create_secret(tmp_path) == first  # stable, deployment-owned
    body = b'{"targetImage":"img:v1"}'
    signature = auth.sign(first, body)
    assert auth.verify(first, body, signature)
    assert not auth.verify(first, body, "bogus")
    assert not auth.verify(first, b"tampered", signature)


# -- R2: endpoint ---------------------------------------------------------------

def test_authenticated_endpoint_serves_update_while_target_stack_is_down(tmp_path):
    import json as _json
    import threading
    import urllib.request as _request
    from mm_controller import server as _server

    from mm_controller.store import OperationStore as _Store

    state = tmp_path / "endpoint-state"
    secret = auth.load_or_create_secret(state)
    store = _Store(state)
    ctrl = Controller(store=store, runner=FakeRunner())
    httpd = _server.serve(
        host="127.0.0.1", port=0, secret=secret, store=store,
        controller=ctrl, pre_builder=lambda payload: allowed_pre(
            str(payload.get("targetImage") or "")),
    )
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        def post(path, payload, key):
            body = _json.dumps(payload).encode()
            req = _request.Request(
                f"http://127.0.0.1:{port}{path}", data=body,
                headers={"Authorization": "Bearer " + auth.sign(key, body)},
            )
            return _json.load(_request.urlopen(req))

        import urllib.error as _error
        try:
            post("/update", {"targetImage": "img:v9"}, "wrong-secret")
            raise AssertionError("unauthorized request must not succeed")
        except _error.HTTPError as exc:
            assert exc.code == 401
        result = post("/update", {"targetImage": "img:v9", "services": ["api"]}, secret)
        assert result["status"] == "SUCCEEDED"
        with _request.urlopen(f"http://127.0.0.1:{port}/status") as response:
            status = _json.load(response)
        assert status["installed"]["targetImage"] == "img:v9"
    finally:
        httpd.shutdown()
        thread.join(timeout=10)


# -- A1: repair with the application stack down ----------------------------------

def test_repair_proceeds_without_application_services(store):
    """The controller needs no API/DB/Temporal/artifact service: the fake
    runner stands in for Docker while every application dependency is absent."""
    runner = FakeRunner()
    result = Controller(store=store, runner=runner).update(
        PlanRequest(target_image="img:v1", services=("api",)), pre=allowed_pre("img:v1"))
    assert result.status == "SUCCEEDED"
    assert runner.reconciled  # competing child reconciled before launch
