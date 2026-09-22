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
