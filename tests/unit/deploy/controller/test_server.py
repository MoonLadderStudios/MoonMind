"""Authenticated local endpoint + restart convergence (REQ-02, REQ-04)."""

import json
import threading
import urllib.request
from pathlib import Path
from wsgiref.simple_server import make_server

import pytest
from conftest import load


class _Harness:
    def __init__(self, controller_path, tmp_path):
        self.server_mod = load("server")
        self.record = load("record")
        self.store = self.record.OperationStore(tmp_path / "state")
        self.app = self.server_mod.build_app(
            store=self.store,
            secret="test-secret",
            applier=self.fake_applier,
        )
        self.applied = []
        self.httpd = make_server("127.0.0.1", 0, self.app)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def fake_applier(self, operation):
        self.applied.append(operation["operationId"])
        self.store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )
        return {"status": "succeeded"}

    def call(self, method, path, body=None, secret=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body or {}).encode() if body is not None else None,
            method=method,
            headers=(
                {"Authorization": f"Bearer {secret}"} if secret is not None else {}
            ),
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    def close(self):
        self.httpd.shutdown()
        self.thread.join(timeout=10)


def test_endpoint_rejects_unauthenticated_requests(controller_path, tmp_path):
    harness = _Harness(controller_path, tmp_path)
    try:
        status, _ = harness.call("POST", "/v1/operations", body={"stack": "x"})
        assert status == 401
        status, _ = harness.call(
            "POST", "/v1/operations", body={"stack": "x"}, secret="wrong"
        )
        assert status == 401
    finally:
        harness.close()


def test_submit_status_and_retry_round_trip(controller_path, tmp_path):
    harness = _Harness(controller_path, tmp_path)
    try:
        status, created = harness.call(
            "POST",
            "/v1/operations",
            body={
                "stack": "moonmind",
                "desiredImage": "ghcr.io/org/app@sha256:abc",
                "sourceRevision": "abc123",
            },
            secret="test-secret",
        )
        assert status == 202, created
        op_id = created["operationId"]
        assert harness.applied == [op_id]
        status, fetched = harness.call(
            "GET", f"/v1/operations/{op_id}", secret="test-secret"
        )
        assert status == 200
        assert fetched["installed"]["image"] == "ghcr.io/org/app@sha256:abc"
        status, refused = harness.call(
            "POST", f"/v1/operations/{op_id}/retry", secret="test-secret"
        )
        # A succeeded operation is never re-applied through Retry.
        assert status == 409, refused
        assert harness.applied == [op_id]
        assert harness.store.load(op_id)["attemptGroup"] == 1
    finally:
        harness.close()


def test_submit_defers_while_legacy_writer_may_be_active(controller_path, tmp_path):
    server_mod = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")
    app = server_mod.build_app(
        store=store,
        secret="test-secret",
        applier=lambda operation: None,
        legacy_writer_probe=lambda: True,
    )
    harness_httpd = make_server("127.0.0.1", 0, app)
    port = harness_httpd.server_address[1]
    thread = threading.Thread(target=harness_httpd.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/operations",
            data=json.dumps(
                {"stack": "moonmind", "desiredImage": "img", "sourceRevision": "r"}
            ).encode(),
            method="POST",
            headers={"Authorization": "Bearer test-secret"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10):
                raise AssertionError("expected a 409 while the legacy writer is active")
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
    finally:
        harness_httpd.shutdown()
        thread.join(timeout=10)


def test_internal_errors_do_not_expose_exception_detail(controller_path, tmp_path):
    server_mod = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")

    def boom(operation):
        raise RuntimeError("secret stack trace boom")

    app = server_mod.build_app(store=store, secret="test-secret", applier=boom)
    harness_httpd = make_server("127.0.0.1", 0, app)
    port = harness_httpd.server_address[1]
    thread = threading.Thread(target=harness_httpd.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/operations",
            data=json.dumps(
                {"stack": "moonmind", "desiredImage": "img", "sourceRevision": "r"}
            ).encode(),
            method="POST",
            headers={"Authorization": "Bearer test-secret"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10):
                raise AssertionError("expected a 500 from the failing applier")
        except urllib.error.HTTPError as exc:
            assert exc.code == 500
            payload = json.loads(exc.read().decode() or "{}")
            assert "boom" not in json.dumps(payload)
            assert payload.get("error") == "internal error"
    finally:
        harness_httpd.shutdown()
        thread.join(timeout=10)


def test_restart_converges_unfinished_work_without_duplicate_apply(
    controller_path, tmp_path
):
    server_mod = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")
    op = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
    )
    applied = []

    def applier(operation):
        applied.append(operation["operationId"])
        store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    server_mod.converge_on_restart(store, applier=applier)
    assert applied == [op["operationId"]]
    # A lost result must not repeat a completed apply unnecessarily.
    server_mod.converge_on_restart(store, applier=applier)
    assert applied == [op["operationId"]]


def test_restart_applies_only_the_newest_open_per_stack(
    controller_path, tmp_path
):
    server_mod = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")
    stale = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:aaa",
        source_revision="aaa",
    )
    current = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:bbb",
        source_revision="bbb",
    )
    applied = []

    def applier(operation):
        applied.append(operation["operationId"])
        store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    result = server_mod.converge_on_restart(store, applier=applier)
    assert applied == [current["operationId"]]
    assert store.load(stale["operationId"])["status"] == "superseded"
    assert result["superseded"] == [stale["operationId"]]


def test_restart_never_replays_a_stale_target_over_confirmed_install(
    controller_path, tmp_path
):
    server_mod = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")
    stale = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:aaa",
        source_revision="aaa",
    )
    installed = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:bbb",
        source_revision="bbb",
    )
    store.confirm_installed(installed["operationId"], image="ghcr.io/org/app@sha256:bbb")
    # Backdate the stale open so it is unambiguously older than the install.
    stale_path = tmp_path / "state" / "operations" / f"{stale['operationId']}.json"
    payload = json.loads(stale_path.read_text())
    payload["createdAt"] = "2020-01-01T00:00:00+00:00"
    stale_path.write_text(json.dumps(payload))
    applied = []

    def applier(operation):
        applied.append(operation["operationId"])
        store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    result = server_mod.converge_on_restart(store, applier=applier)
    assert applied == []
    assert store.load(stale["operationId"])["status"] == "superseded"
    assert result["superseded"] == [stale["operationId"]]
    assert store.load(installed["operationId"])["status"] == "succeeded"


def test_submit_retries_transient_failures_within_a_bounded_budget(
    controller_path, tmp_path
):
    # Load engine before server so both share one StageError identity.
    engine_mod = load("engine")
    server_mod = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")
    calls = {"count": 0}

    def flaky(operation):
        calls["count"] += 1
        if calls["count"] < 3:
            store.record_attempt_error(
                operation["operationId"], error=f"transient {calls['count']}"
            )
            raise engine_mod.StageError("pull", 1, "transient")
        store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    app = server_mod.build_app(store=store, secret="test-secret", applier=flaky)
    harness_httpd = make_server("127.0.0.1", 0, app)
    port = harness_httpd.server_address[1]
    thread = threading.Thread(target=harness_httpd.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/operations",
            data=json.dumps(
                {"stack": "moonmind", "desiredImage": "img", "sourceRevision": "r"}
            ).encode(),
            method="POST",
            headers={"Authorization": "Bearer test-secret"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 202
            created = json.loads(response.read().decode() or "{}")
        assert created["status"] == "succeeded"
        assert calls["count"] == 3
    finally:
        harness_httpd.shutdown()
        thread.join(timeout=10)


def test_submit_reaches_terminal_failed_after_the_retry_budget(
    controller_path, tmp_path
):
    # Load engine before server so both share one ApplyError identity.
    engine_mod = load("engine")
    server_mod = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")

    def always_failing(operation):
        store.record_attempt_error(operation["operationId"], error="still failing")
        raise engine_mod.ApplyError("up", 1, "still failing")

    app = server_mod.build_app(
        store=store, secret="test-secret", applier=always_failing
    )
    harness_httpd = make_server("127.0.0.1", 0, app)
    port = harness_httpd.server_address[1]
    thread = threading.Thread(target=harness_httpd.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/operations",
            data=json.dumps(
                {"stack": "moonmind", "desiredImage": "img", "sourceRevision": "r"}
            ).encode(),
            method="POST",
            headers={"Authorization": "Bearer test-secret"},
        )
        # A recorded terminal failure is the operation's result, not an
        # internal error: the caller receives it and can request a Retry.
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 202
            created = json.loads(response.read().decode() or "{}")
        assert created["status"] == "failed"
        assert created["errorSummary"].startswith("attempt 1: still failing")
        operations = store.list_terminal(stack="moonmind")
        assert len(operations) == 1
        assert operations[0]["status"] == "failed"
        assert len(operations[0]["attempts"]) == record.MAX_AUTO_ATTEMPTS
    finally:
        harness_httpd.shutdown()
        thread.join(timeout=10)


def _post_operation(port, body):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/operations",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Authorization": "Bearer test-secret"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def test_submit_rejects_unsafe_compose_targets(controller_path, tmp_path):
    server_mod = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")
    app = server_mod.build_app(
        store=store, secret="test-secret", applier=lambda operation: None
    )
    harness_httpd = make_server("127.0.0.1", 0, app)
    port = harness_httpd.server_address[1]
    thread = threading.Thread(target=harness_httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, _ = _post_operation(
            port,
            {
                "stack": "moonmind",
                "desiredImage": "img",
                "target": {
                    "project": "moonmind",
                    "composeFiles": ["../evil.yaml"],
                    "services": ["api"],
                },
            },
        )
        assert status == 400
        status, _ = _post_operation(
            port,
            {
                "stack": "moonmind",
                "desiredImage": "img",
                "target": {
                    "project": "moonmind",
                    "composeFiles": ["docker-compose.yaml"],
                    "services": ["api; rm -rf /"],
                },
            },
        )
        assert status == 400
        status, _ = _post_operation(
            port,
            {
                "stack": "moonmind",
                "desiredImage": "img",
                "target": {
                    "project": "../escape",
                    "composeFiles": ["docker-compose.yaml"],
                },
            },
        )
        assert status == 400
        assert store.list_open() == []
    finally:
        harness_httpd.shutdown()
        thread.join(timeout=10)


def test_submit_accepts_relative_compose_subpaths(controller_path, tmp_path):
    server_mod = load("server")
    record = load("record")
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    store = record.OperationStore(tmp_path / "state")
    applied = []

    def applier(operation):
        applied.append(operation["operationId"])
        store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    app = server_mod.build_app(store=store, secret="test-secret", applier=applier)
    harness_httpd = make_server("127.0.0.1", 0, app)
    port = harness_httpd.server_address[1]
    thread = threading.Thread(target=harness_httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, created = _post_operation(
            port,
            {
                "stack": "moonmind",
                "desiredImage": "img",
                "target": {
                    "project": "moonmind",
                    "projectDir": str(project_dir),
                    "composeFiles": ["deploy/extra.yaml"],
                    "services": ["api"],
                },
            },
        )
        assert status == 202, created
        assert applied == [created["operationId"]]
    finally:
        harness_httpd.shutdown()
        thread.join(timeout=10)


def test_env_files_layer_deployment_env_under_the_overlay(controller_path, tmp_path):
    server_mod = load("server")
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / ".env").write_text("AUTH_PROVIDER=disabled\n")
    release_dir = project_dir / "deploy" / "state"
    release_dir.mkdir(parents=True)
    (release_dir / ".env.deploy").write_text("OMNIGENT_IMAGE_REF=server@sha256:old\n")
    layered = server_mod._env_files_for_apply(
        {"projectDir": str(project_dir)}, "/state/image-overlays/op.env"
    )
    assert layered == [
        str(project_dir / ".env"),
        str(release_dir / ".env.deploy"),
        "/state/image-overlays/op.env",
    ]
    # Compose's default also applies to an explicitly empty setting.
    (project_dir / ".env").write_text("MOONMIND_DEPLOYMENT_DESIRED_STATE_ENV_FILE=\n")
    assert (
        server_mod._env_files_for_apply(
            {"projectDir": str(project_dir)}, "/state/image-overlays/op.env"
        )
        == layered
    )
    explicit = server_mod._env_files_for_apply(
        {"projectDir": str(project_dir), "envFile": "/custom/dep.env"},
        "/state/image-overlays/op.env",
    )
    assert explicit == [
        "/custom/dep.env",
        str(release_dir / ".env.deploy"),
        "/state/image-overlays/op.env",
    ]
    bare_dir = tmp_path / "bare"
    bare_dir.mkdir()
    assert server_mod._env_files_for_apply(
        {"projectDir": str(bare_dir)}, "/state/image-overlays/op.env"
    ) == ["/state/image-overlays/op.env"]


def test_legacy_probe_ignores_the_always_on_service_container(controller_path):
    server_mod = load("server")
    # The steady-state service reports no one-off label: not an active writer.
    assert (
        server_mod.parse_legacy_mutation_ps(
            "moonmind-temporal-worker-deployment-control-1\t\n"
        )
        is False
    )
    assert server_mod.parse_legacy_mutation_ps("") is False
    # A running one-off updater container is an owned mutation in progress.
    assert (
        server_mod.parse_legacy_mutation_ps(
            "moonmind-temporal-worker-deployment-control-1\t\n"
            "moonmind-temporal-worker-deployment-control-run-9a8b\ttrue\n"
        )
        is True
    )


def _verification_runner(engine, ps_state="running", health=""):
    import json as _json

    class _Runner:
        def __init__(self):
            self.commands = []

        def run(self, args, timeout_seconds):
            self.commands.append(tuple(args))
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": ""}
            if "ps" in args:
                return {
                    "exit": 0,
                    "output": _json.dumps({"Service": "api", "State": ps_state, "Health": health}),
                }
            return {"exit": 0, "output": "ok"}

    return _Runner()


def test_journal_preparation_uses_installed_source_for_rollback_and_restart(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    target = {"project": "moonmind-test", "projectDir": str(tmp_path),
              "composeFiles": ["docker-compose.yaml"],
              "services": ["temporal-worker-agent-runtime", "temporal-worker-artifacts"]}
    op = _begin_op(record, store, target)
    calls = []

    class Runner:
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": "writer\nsweeper"}
            assert args[:2] == ("docker", "inspect")
            return {"exit": 0, "output": "\n".join(json.dumps(
                {"Image": "sha256:installed-new", "Config": {"Env": [], "Labels": {
                    "com.docker.compose.project": target["project"],
                    "com.docker.compose.service": name}}}
                )
                for name in target["services"]
            )}

    def helper(store, operation, **kwargs):
        calls.append((kwargs["helper_image"], kwargs["phase"]))
        assert store.load(op["operationId"])["journalTransition"]["sourceImages"] == ["sha256:installed-new"]
        return {"status": "prepared", "compacted": 1}

    monkeypatch.setattr(server, "run_omnigent_step", helper)
    server.prepare_controller_journals(store, op, runner=Runner(), overlay="overlay")

    class UnavailableRunner:
        def run(self, *args, **kwargs):
            pytest.fail("restart must retain source image after partial recreation")

    server.prepare_controller_journals(store, op, runner=UnavailableRunner(), overlay="overlay")
    assert calls == [("sha256:installed-new", "journal-prepare")] * 2


def test_journal_inventory_survives_bounded_subprocess_diagnostics(
    controller_path, tmp_path, monkeypatch
):
    import subprocess

    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    target = {"project": "moonmind-test", "services": [
        "custom-agent", "custom-artifacts",
    ]}
    operation = _begin_op(record, store, target)
    rows = [{"Image": f"sha256:installed-{fleet}", "Config": {
        "Env": [f"TEMPORAL_WORKER_FLEET={fleet}"], "Labels": {
            "com.docker.compose.project": target["project"],
            "com.docker.compose.service": service,
        },
    }} for fleet, service in zip(("agent_runtime", "artifacts"), target["services"])]
    captured = []

    def command(args, **kwargs):
        args = tuple(args)
        captured.append(args)
        if args[:2] == ("docker", "ps"):
            output = "writer\nsweeper\n"
        else:
            assert args[:2] == ("docker", "inspect")
            if "--format" in args:
                # Docker projects only requested fields before the command
                # runner bounds and redacts the diagnostic stream.
                output = "\n".join(json.dumps(row) for row in rows)
            else:
                output = json.dumps([
                    {**row, "Mounts": [{"Source": "/large/" + "x" * 5000}]}
                    for row in rows
                ])
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    monkeypatch.setattr(server.engine.subprocess, "run", command)
    monkeypatch.setattr(server, "run_omnigent_step", lambda *args, **kwargs: {
        "status": "prepared", "compacted": 1,
    })
    result = server.prepare_controller_journals(
        store, operation, runner=server.engine.subprocess_runner(), overlay="overlay"
    )
    assert result["compacted"] == 1
    assert store.load(operation["operationId"])["journalTransition"]["sourceImages"] == [
        row["Image"] for row in rows
    ]
    inspection = next(args for args in captured if args[:2] == ("docker", "inspect"))
    assert "--format" in inspection


def test_journal_inventory_reads_every_row_of_a_full_project(
    controller_path, tmp_path, monkeypatch
):
    """A normal stack has ~25 project containers; projected rows still
    exceed the runner's diagnostic tail, which must not truncate the data."""
    import subprocess

    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    writers = ["temporal-worker-agent-runtime", "temporal-worker-artifacts"]
    others = [f"service-{index:02d}" for index in range(23)]
    target = {"project": "moonmind-test", "services": [*writers, *others]}
    operation = _begin_op(record, store, target)
    rows = [{"Image": f"sha256:{service}-{'0' * 64}", "Config": {"Env": [""], "Labels": {
        "com.docker.compose.project": target["project"],
        "com.docker.compose.service": service,
    }}} for service in [*writers, *others]]

    def command(args, **kwargs):
        args = tuple(args)
        if args[:2] == ("docker", "ps"):
            output = "\n".join(f"id-{index:02d}" for index in range(len(rows)))
        else:
            assert args[:2] == ("docker", "inspect")
            output = "\n".join(json.dumps(row) for row in rows)
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    monkeypatch.setattr(server.engine.subprocess, "run", command)
    monkeypatch.setattr(server, "run_omnigent_step", lambda *args, **kwargs: {
        "status": "prepared",
    })
    server.prepare_controller_journals(
        store, operation, runner=server.engine.subprocess_runner(), overlay="overlay"
    )
    assert store.load(operation["operationId"])["journalTransition"]["sourceImages"] == [
        row["Image"] for row in rows[:2]
    ]


def test_journal_preparation_falls_back_to_capable_staged_target(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    op = _begin_op(record, store, {"project": "moonmind-test"})
    store.record_journal_transition(op["operationId"], {"sourceImages": ["sha256:old"]})
    images = []

    def helper(store, operation, **kwargs):
        images.append(kwargs["helper_image"])
        return {"status": "unsupported" if len(images) == 1 else "prepared"}

    monkeypatch.setattr(server, "run_omnigent_step", helper)
    server.prepare_controller_journals(store, op, runner=None, overlay="overlay")
    assert images == ["sha256:old", op["desired"]["image"]]


def test_subsequent_rollback_retains_bootstrapped_application_helper(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    op = _begin_op(record, store, {"project": "moonmind-test"})
    store.record_journal_transition(op["operationId"], {"sourceImages": ["sha256:old"]})
    (store.state_dir / "controller-image.json").write_text(json.dumps({
        "pinned": "sha256:controller-app-source", "verified": True,
    }))

    def helper(store, operation, **kwargs):
        return {"status": "prepared" if kwargs["helper_image"] == "sha256:controller-app-source" else "unsupported"}

    monkeypatch.setattr(server, "run_omnigent_step", helper)
    result = server.prepare_controller_journals(store, op, runner=None, overlay="overlay")
    assert result["status"] == "prepared"
    assert store.load(op["operationId"])["journalTransition"]["helperImage"] == "sha256:controller-app-source"


def test_partial_recreation_requiesces_and_recompacts_before_retry(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    services = ["temporal-worker-agent-runtime", "temporal-worker-artifacts", "postgres", "minio"]
    op = _begin_op(record, store, {"project": "moonmind-test",
        "projectDir": str(tmp_path), "composeFiles": ["docker-compose.yaml"],
        "services": services})
    store.record_journal_transition(op["operationId"], {"sourceImages": ["sha256:source"]})
    phases = []
    attempts = 0
    storage_running = False

    class Runner:
        def run(self, args, timeout_seconds):
            nonlocal attempts, storage_running
            if "pull" in args:
                phases.append("pull")
            if "up" in args:
                if "--no-recreate" in args:
                    assert args[-2:] == ("postgres", "minio")
                    storage_running = True
                    phases.append("storage")
                    return {"exit": 0, "output": ""}
                assert phases[-1] == "journal-prepare"
                phases.append("up")
                attempts += 1
                if attempts == 1:
                    return {"exit": 1, "output": "only writer recreated"}
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": ""}  # No Temporal workers to cut over.
            if "ps" in args:
                return {"exit": 0, "output": json.dumps([
                    {"Service": name, "State": "running"} for name in services])}
            return {"exit": 0, "output": ""}

    def helper(store, operation, **kwargs):
        assert storage_running
        assert kwargs["helper_image"] == "sha256:source"
        phases.append(kwargs["phase"])
        return {"status": "prepared" if kwargs["phase"] == "journal-prepare" else "quiesced"}

    monkeypatch.setattr(server.engine, "subprocess_runner", Runner)
    monkeypatch.setattr(server, "run_omnigent_step", helper)
    result = server._apply_with_bounded_retries(store, op["operationId"],
        lambda operation: server.production_apply(store, operation))
    assert result["status"] == "succeeded"
    assert phases == ["pull", "storage", "journal-prepare", "up", "journal-quiesce",
                      "pull", "storage", "journal-prepare", "up"]
    assert "only writer recreated" in result["attempts"][0]["error"]


def _exhausted_apply(server, record, store, tmp_path, monkeypatch, *, recovery_exit=0):
    services = ["temporal-worker-agent-runtime", "temporal-worker-artifacts", "postgres", "minio"]
    op = _begin_op(record, store, {"project": "moonmind-test",
        "projectDir": str(tmp_path), "composeFiles": ["docker-compose.yaml"],
        "services": services})
    store.record_journal_transition(op["operationId"], {"sourceImages": ["sha256:source"]})
    events = []

    class Runner:
        def run(self, args, timeout_seconds):
            if "up" in args and "--no-recreate" not in args:
                if args[-2:] == ("temporal-worker-agent-runtime", "temporal-worker-artifacts"):
                    events.append(("recover", args))
                    return {"exit": recovery_exit, "output": "sweeper unhealthy"}
                events.append(("up", args))
                return {"exit": 1, "output": "container moonmind-api-1 is unhealthy"}
            return {"exit": 0, "output": ""}

    def helper(store, operation, **kwargs):
        events.append((kwargs["phase"], None))
        return {"status": "prepared" if kwargs["phase"] == "journal-prepare" else "quiesced"}

    monkeypatch.setattr(server.engine, "subprocess_runner", Runner)
    monkeypatch.setattr(server, "run_omnigent_step", helper)
    result = server._apply_recording_failure(store, op["operationId"],
        lambda operation: server.production_apply(store, operation))
    return result, events


def test_exhausted_apply_restarts_consumers_on_the_applied_release(
    controller_path, tmp_path, monkeypatch
):
    # No retry follows the last attempt, so fenced writers and sweepers must
    # not stay stopped: Compose recreates their services on the applied
    # release (one-offs stay stopped) instead of leaving agent launches dead.
    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    result, events = _exhausted_apply(server, record, store, tmp_path, monkeypatch)

    assert result["status"] == "failed"
    assert result["autoAttemptsExhausted"] is True
    assert "moonmind-api-1 is unhealthy" in result["attempts"][-1]["error"]
    assert result["reportingFailures"] == []
    phases = [phase for phase, _args in events]
    assert phases.count("journal-quiesce") == 3
    assert phases.count("recover") == 1
    assert phases[-2:] == ["journal-quiesce", "recover"]
    recover = events[-1][1]
    assert {"--no-deps", "--pull", "never", "--no-build", "--wait"} <= set(recover)
    assert "postgres" not in recover


def test_failed_consumer_recovery_is_recorded_beside_the_apply_error(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    result, _events = _exhausted_apply(
        server, record, store, tmp_path, monkeypatch, recovery_exit=1,
    )

    assert result["status"] == "failed"
    assert "moonmind-api-1 is unhealthy" in result["attempts"][-1]["error"]
    assert len(result["reportingFailures"]) == 1
    assert "journal consumer recovery after failed apply" in str(
        result["reportingFailures"][0]
    )
    assert "sweeper unhealthy" in str(result["reportingFailures"][0])


def test_journal_oneoff_runs_recorded_source_with_controller_transport(
    controller_path, tmp_path
):
    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    op = _begin_op(record, store, {"project": "moonmind-test",
        "projectDir": str(tmp_path), "composeFiles": ["docker-compose.yaml"]})

    class Runner:
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": ""}
            assert "--no-deps" in args
            assert args[args.index("--pull") + 1] == "never"
            overlay = [args[i + 1] for i, arg in enumerate(args) if arg == "-f"][-1]
            helper = json.loads(Path(overlay).read_text())["services"][server.LEGACY_CONTROL_SERVICE]
            assert helper["image"] == "sha256:recorded-source"
            assert helper["environment"]["DOCKER_HOST"] == "unix:///var/run/docker.sock"
            assert args[-2] == "journal-prepare"
            assert "controller_journal_cli" in args[-3]
            return {"exit": 0, "output": "MOONMIND_OMNIGENT_RESULT={\"status\":\"prepared\"}"}

    assert server.run_omnigent_step(store, op, runner=Runner(), overlay="overlay",
        phase="journal-prepare", helper_image="sha256:recorded-source") == {"status": "prepared"}


def test_failed_selection_leads_with_helper_error_not_pull_progress(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server = load("server")
    engine = server.engine
    store = record.OperationStore(tmp_path / "state")
    target = {"project": "moonmind-test", "projectDir": str(tmp_path),
              "composeFiles": ["docker-compose.yaml"], "services": ["omnigent"]}
    op = _begin_op(record, store, target)
    refusal = ("RuntimeError: Refusing Omnigent select: the installed controller "
               "does not provide unversioned-routing-cutover.")
    seen = []

    class Runner:
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": ""}
            seen.append(args)
            progress = "\n".join(f" {i:012x} Pull complete 0B" for i in range(300))
            raise engine.CommandError(args[2], 1, f"{progress}\nTraceback:\n{refusal}")

    monkeypatch.setattr(engine, "subprocess_runner", lambda: Runner())
    with pytest.raises(engine.StageError) as failure:
        server.production_apply(store, op, omnigent_selector=lambda operation:
            server.run_omnigent_step(store, operation, runner=Runner(),
                                     overlay="overlay", phase="select"))

    assert "--quiet-pull" in seen[0]
    error = store.load(op["operationId"])["errorSummary"]
    assert error.startswith(
        f"attempt 1: Omnigent selection failed: omnigent-select failed (exit 1) {refusal}"
    )
    assert refusal in str(failure.value.output)[:2000]


@pytest.mark.parametrize("receipt", ["replaced", "current", "unsupported", "unavailable"])
def test_managed_controller_hands_off_before_changing_anything(
    controller_path, tmp_path, monkeypatch, receipt
):
    """The requested release's controller applies it; others keep applying."""
    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    (tmp_path / ".env").write_text("OMNIGENT_IMAGE=example/omnigent\n")
    op = _begin_op(record, store, {"project": "moonmind", "projectDir": str(tmp_path),
                                   "composeFiles": ["docker-compose.yaml"],
                                   "services": ["api"]})
    monkeypatch.setenv("MOONMIND_CONTROLLER_MANAGED", "1")
    runner = _verification_runner(server.engine)
    monkeypatch.setattr(server.engine, "subprocess_runner", lambda: runner)

    class Replaced(BaseException):
        """The handoff ends this controller process."""

    def step(store, operation, **kwargs):
        assert kwargs["phase"] == "controller-handoff"
        if receipt == "replaced":
            raise Replaced
        if receipt == "unavailable":
            return {"status": "unavailable", "reason": "other deployment work is open"}
        return {"status": receipt}

    monkeypatch.setattr(server, "run_omnigent_step", step)
    selections = []
    options = {
        "dispatch_probe": lambda: True,
        "omnigent_selector": lambda operation: selections.append(1) or {"status": "skipped"},
        "omnigent_migrator": lambda summary: {"status": "aligned"},
    }
    if receipt == "replaced":
        with pytest.raises(Replaced):
            server.production_apply(store, op, **options)
        # Only the previous owner's release steps were observed.
        assert selections == []
        assert [command[:2] for command in runner.commands] == [("docker", "ps")]
        assert store.load(op["operationId"])["status"] == "staged"
        return
    server.production_apply(store, op, **options)
    assert selections == [1]
    handoff = store.load(op["operationId"]).get("controllerHandoff") or {}
    assert handoff.get("unavailable") == (
        "other deployment work is open" if receipt == "unavailable" else None
    )


def test_resumed_controller_confirms_the_handoff_once(controller_path, tmp_path, monkeypatch):
    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    op = _begin_op(record, store, {"project": "moonmind-test"})
    store.record_controller_handoff(
        op["operationId"], {"image": "sha256:new", "from": "sha256:old", "attemptGroup": 1}
    )
    monkeypatch.setattr(server, "run_omnigent_step", lambda *a, **k: {"status": "current"})
    server.hand_off_to_requested_controller(store, op, runner=None, overlay="overlay")
    confirmed = store.load(op["operationId"])["controllerHandoff"]
    assert confirmed["confirmedAt"] and confirmed["from"] == "sha256:old"
    server.hand_off_to_requested_controller(store, op, runner=None, overlay="overlay")
    assert store.load(op["operationId"])["controllerHandoff"] == confirmed


def test_handoff_oneoff_runs_the_requested_image_with_controller_transport(
    controller_path, tmp_path
):
    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    op = _begin_op(record, store, {"project": "moonmind-test",
        "projectDir": str(tmp_path), "composeFiles": ["docker-compose.yaml"]})

    class Runner:
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": ""}
            assert args[args.index("--pull") + 1] == "always"
            overlay = [args[i + 1] for i, arg in enumerate(args) if arg == "-f"][-1]
            helper = json.loads(Path(overlay).read_text())["services"][server.LEGACY_CONTROL_SERVICE]
            assert helper["image"] == op["desired"]["image"]
            assert helper["environment"]["DOCKER_HOST"] == "unix:///var/run/docker.sock"
            assert args[-2] == "handoff" and "controller_handoff_cli" in args[-3]
            return {"exit": 0, "output": 'MOONMIND_OMNIGENT_RESULT={"status":"current"}'}

    assert server.run_omnigent_step(store, op, runner=Runner(), overlay="overlay",
        phase="controller-handoff") == {"status": "current"}


def test_restart_resumes_handed_off_work_after_the_previous_oneoff_exits(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server = load("server")
    lock = load("lock")
    store = record.OperationStore(tmp_path / "state")
    op = _begin_op(record, store, {"project": "moonmind-test", "services": ["api"]})
    polls = []

    class Runner:
        def run(self, args, timeout_seconds):
            assert args[:3] == ("docker", "ps", "--quiet")
            assert args[-1] == f"label={server.OMNIGENT_OPERATION_LABEL}={op['operationId']}"
            assert "label=com.docker.compose.project=moonmind-test" in args
            polls.append(args)
            return {"exit": 0, "output": "handoff-oneoff\n" if len(polls) < 3 else ""}

    monkeypatch.setattr(server, "ONEOFF_EXIT_POLL_SECONDS", 0)
    applied = []

    def applier(operation):
        # The one-off that started this controller is not a competing writer.
        assert len(polls) == 3
        assert lock.StackLock(store.state_dir, "moonmind").probe()
        applied.append(operation["operationId"])
        store.confirm_installed(operation["operationId"], image=operation["desired"]["image"])

    result = server.resume_open_operations(store, applier, runner=Runner())
    assert applied == [op["operationId"]] == result["converged"]
    assert store.load(op["operationId"])["status"] == "succeeded"


@pytest.mark.parametrize("case", ["submission-owns-stack", "unexpected-error"])
def test_restart_recovery_never_competes_or_crash_loops(
    controller_path, tmp_path, monkeypatch, case
):
    record = load("record")
    server = load("server")
    lock = load("lock")
    store = record.OperationStore(tmp_path / "state")
    op = _begin_op(record, store, {"project": "moonmind-test", "services": ["api"]})

    class Runner:
        def run(self, args, timeout_seconds):
            return {"exit": 0, "output": ""}

    def applier(operation):
        if case == "submission-owns-stack":
            pytest.fail("restart recovery competed with the stack owner")
        raise ValueError("journal consumer inventory is incomplete")

    if case == "submission-owns-stack":
        monkeypatch.setattr(server, "STACK_LOCK_WAIT_SECONDS", 0)
        with lock.StackLock(store.state_dir, "moonmind").acquire():
            server.resume_open_operations(store, applier, runner=Runner())
        assert store.load(op["operationId"])["attempts"] == []
        return
    server.resume_open_operations(store, applier, runner=Runner())
    error = store.load(op["operationId"])["attempts"][-1]["error"]
    assert error == "restart recovery failed: journal consumer inventory is incomplete"


def test_restart_recovery_waits_for_the_restore_that_started_it(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server = load("server")
    lock = server.lock_mod
    store = record.OperationStore(tmp_path / "state")
    op = _begin_op(record, store, {"project": "moonmind-test", "services": ["api"]})
    acquire = lock.StackLock.acquire
    busy = []

    def held_by_restore(self):
        # A bootstrap restore holds the stack until `compose up --wait` returns,
        # after the controller it started has already begun recovery.
        if len(busy) < 2:
            busy.append(self.stack)
            raise lock.LockBusyError("restore owns the stack")
        return acquire(self)

    monkeypatch.setattr(lock.StackLock, "acquire", held_by_restore)
    monkeypatch.setattr(server, "ONEOFF_EXIT_POLL_SECONDS", 0)

    class Runner:
        def run(self, args, timeout_seconds):
            return {"exit": 0, "output": ""}

    applied = []

    def applier(operation):
        applied.append(operation["operationId"])
        store.confirm_installed(operation["operationId"], image=operation["desired"]["image"])

    server.resume_open_operations(store, applier, runner=Runner())
    assert busy == ["moonmind", "moonmind"]
    assert applied == [op["operationId"]]
    assert store.load(op["operationId"])["status"] == "succeeded"


def _begin_op(record, store, target):
    return store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:abc",
        source_revision="abc123",
        target=target,
    )


def test_production_apply_verifies_release_before_success(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server_mod = load("server")
    store = record.OperationStore(tmp_path / "state")
    target = {
        "project": "moonmind",
        "projectDir": str(tmp_path),
        "composeFiles": ["docker-compose.yaml"],
        "services": ["api"],
    }
    op = _begin_op(record, store, target)
    monkeypatch.setattr(
        server_mod.engine,
        "subprocess_runner",
        lambda: _verification_runner(server_mod.engine),
    )
    result = server_mod.production_apply(
        store,
        op,
        dispatch_probe=lambda: True,
        omnigent_migrator=lambda summary: {"status": "aligned"},
    )
    assert result["status"] == "succeeded", result
    loaded = store.load(op["operationId"])
    assert loaded["status"] == "succeeded"
    names = {check["name"] for check in loaded["verification"]}
    assert "service:api" in names
    assert "dispatch" in names
    assert "omnigent-migration" in names
    assert all(check["status"] == "passed" for check in loaded["verification"])


def test_slow_start_waits_after_one_apply_before_routing_cutover(
    controller_path, tmp_path, monkeypatch
):
    import types

    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    operation = _begin_op(
        record,
        store,
        {
            "project": "moonmind-test",
            "projectDir": str(tmp_path),
            "composeFiles": ["docker-compose.yaml"],
            "services": ["api"],
        },
    )
    runner = _verification_runner(server.engine)
    monkeypatch.setattr(server.engine, "subprocess_runner", lambda: runner)
    elapsed = [0.0]
    monkeypatch.setattr(
        server.engine,
        "time",
        types.SimpleNamespace(
            monotonic=lambda: elapsed[0],
            sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
        ),
    )

    def observe(*args, **kwargs):
        ready = elapsed[0] >= 600
        return {
            "services": {"api": ready},
            "health": {"api": "healthy" if ready else "unhealthy"},
        }

    monkeypatch.setattr(server.engine, "observe_services", observe)
    cutovers = []

    def cutover(*args, **kwargs):
        assert elapsed[0] >= 600
        assert store.load(operation["operationId"])["installed"]
        cutovers.append(True)

    monkeypatch.setattr(server, "converge_routing_cutover", cutover)
    result = server.production_apply(store, operation)

    assert result["status"] == "succeeded"
    assert len([command for command in runner.commands if "up" in command]) == 1
    assert len([command for command in runner.commands if "pull" in command]) == 1
    assert cutovers == [True]
    assert store.load(operation["operationId"])["attempts"] == []


def test_readiness_restart_uses_installed_time_without_resetting_budget(
    controller_path, tmp_path, monkeypatch
):
    import types

    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    operation = _begin_op(
        record,
        store,
        {
            "project": "moonmind-test",
            "projectDir": str(tmp_path),
            "composeFiles": ["docker-compose.yaml"],
            "services": ["api"],
        },
    )
    store.confirm_installed(
        operation["operationId"],
        image=operation["desired"]["image"],
        verification_pending=True,
    )
    operation = store.load(operation["operationId"])
    operation["installed"]["confirmedAt"] = "2026-10-19T23:06:00+00:00"
    store._write(operation)
    elapsed = [0.0]
    # 14 minutes of the original 15-minute budget already elapsed before restart.
    monkeypatch.setattr(
        server, "time", types.SimpleNamespace(time=lambda: 1792452000 + elapsed[0])
    )
    monkeypatch.setattr(
        server.engine,
        "time",
        types.SimpleNamespace(
            monotonic=lambda: elapsed[0],
            sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
        ),
    )
    runner = _verification_runner(server.engine, health="unhealthy")
    monkeypatch.setattr(server.engine, "subprocess_runner", lambda: runner)
    monkeypatch.setattr(
        server,
        "converge_routing_cutover",
        lambda *a, **kw: pytest.fail("unready workers cannot cut over"),
    )

    result = server.production_apply(store, operation)

    assert elapsed[0] == 60
    assert result["status"] == "partially_verified"
    assert not any("up" in command or "pull" in command for command in runner.commands)
    final = store.load(operation["operationId"])
    assert final["installed"] == operation["installed"]
    assert any(
        c["name"] == "service:api"
        and c["status"] == "failed"
        and "unhealthy" in c["detail"]
        for c in final["verification"]
    )


def test_production_apply_accepts_completed_one_shot_services(
    controller_path, tmp_path, monkeypatch
):
    import json as _json

    record = load("record")
    server_mod = load("server")
    store = record.OperationStore(tmp_path / "state")
    target = {
        "project": "moonmind",
        "projectDir": str(tmp_path),
        "composeFiles": ["docker-compose.yaml"],
        "services": ["api", "init-db", "codex-auth-init"],
    }
    op = _begin_op(record, store, target)

    class _Runner:
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": ""}
            if "ps" in args:
                rows = [{"Service": "api", "State": "running"}]
                if "--all" in args:  # Compose hides exited containers otherwise.
                    rows.append({"Service": "init-db", "State": "exited", "ExitCode": 0})
                    rows.append(
                        {"Service": "codex-auth-init", "State": "exited", "ExitCode": 0}
                    )
                return {"exit": 0, "output": "\n".join(_json.dumps(r) for r in rows)}
            return {"exit": 0, "output": "ok"}

    monkeypatch.setattr(server_mod.engine, "subprocess_runner", lambda: _Runner())
    result = server_mod.production_apply(store, op, dispatch_probe=lambda: True)
    assert result["status"] == "succeeded", result
    loaded = store.load(op["operationId"])
    assert loaded["status"] == "succeeded"
    checks = {check["name"]: check for check in loaded["verification"]}
    assert checks["service:init-db"]["status"] == "passed"
    assert checks["service:init-db"]["detail"] == "completed (exit 0)"
    assert checks["service:api"]["detail"] == "running"


def test_production_apply_keeps_installation_open_until_verification_finishes(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    operation = _begin_op(record, store, {
        "project": "moonmind-test", "projectDir": str(tmp_path),
        "composeFiles": ["docker-compose.yaml"], "services": ["api"],
    })
    runner = _verification_runner(server.engine)
    monkeypatch.setattr(server.engine, "subprocess_runner", lambda: runner)
    verify = server._verify_applied_release
    observed = []

    def observe_before_verifying(*args, **kwargs):
        observed.append(store.load(operation["operationId"]))
        return verify(*args, **kwargs)

    monkeypatch.setattr(server, "_verify_applied_release", observe_before_verifying)
    result = server.production_apply(store, operation)

    assert observed[0]["installed"]["image"] == operation["desired"]["image"]
    assert observed[0]["status"] == "applying"
    assert observed[0]["verification"] == []
    final = store.load(operation["operationId"])
    assert final["installed"] == observed[0]["installed"]
    assert final["status"] == result["status"] == "succeeded"


@pytest.mark.parametrize("service_state,health", [
    ("running", ""), ("exited", ""), ("running", "healthy"),
    ("running", "unhealthy"), ("running", "starting"),
])
def test_restart_resumes_installed_verification_without_reapplying(
    controller_path, tmp_path, monkeypatch, service_state, health
):
    record = load("record")
    server = load("server")
    monkeypatch.setattr(server, "SERVICE_READINESS_TIMEOUT_SECONDS", 0)
    store = record.OperationStore(tmp_path / "state")
    operation = _begin_op(record, store, {
        "project": "moonmind-test", "projectDir": str(tmp_path),
        "composeFiles": ["docker-compose.yaml"], "services": ["api"],
    })
    runner = _verification_runner(server.engine, ps_state=service_state, health=health)
    monkeypatch.setattr(server.engine, "subprocess_runner", lambda: runner)
    verify = server._verify_applied_release

    def interrupted(*args, **kwargs):
        raise OSError("verification interrupted")

    monkeypatch.setattr(server, "_verify_applied_release", interrupted)
    with pytest.raises(OSError, match="verification interrupted"):
        server.production_apply(store, operation)
    installed = store.load(operation["operationId"])["installed"]
    assert installed["image"] == operation["desired"]["image"]
    assert store.load(operation["operationId"])["status"] == "applying"
    store.note_reporting_failure(operation["operationId"], error="report unavailable")
    runner.commands.clear()

    monkeypatch.setattr(server, "_verify_applied_release", verify)
    result = server.resume_open_operations(
        store, lambda current: server.production_apply(store, current), runner=runner
    )

    final = store.load(operation["operationId"])
    assert result["converged"] == [operation["operationId"]]
    assert final["installed"] == installed
    assert final["reportingFailures"] == ["report unavailable"]
    assert final["status"] == (
        "succeeded" if service_state == "running" and health in ("", "healthy")
        else "partially_verified"
    )
    assert final["verification"]
    assert not any("pull" in command or "up" in command for command in runner.commands)
    assert store.list_open() == []


@pytest.mark.parametrize("newer_completed", [False, True])
def test_restart_does_not_verify_a_superseded_installation(
    controller_path, tmp_path, newer_completed
):
    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    older = store.begin(stack="moonmind", desired_image="img:old", source_revision="")
    older["createdAt"] = "2025-01-01T00:00:00Z"
    store._write(older)
    store.confirm_installed(older["operationId"], image="img:old", verification_pending=True)
    installed = store.load(older["operationId"])["installed"]
    newer = store.begin(stack="moonmind", desired_image="img:new", source_revision="")
    if newer_completed:
        store.confirm_installed(newer["operationId"], image="img:new")
    applied = []

    def apply(operation):
        applied.append(operation["operationId"])
        store.confirm_installed(operation["operationId"], image=operation["desired"]["image"])

    server.converge_on_restart(store, apply)

    assert applied == ([] if newer_completed else [newer["operationId"]])
    final = store.load(older["operationId"])
    assert final["status"] == "superseded"
    assert final["installed"] == installed
    assert store.list_open() == []


def test_unavailable_service_observation_never_finishes_as_success(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server = load("server")
    monkeypatch.setattr(server, "SERVICE_READINESS_TIMEOUT_SECONDS", 0)
    store = record.OperationStore(tmp_path / "state")
    operation = _begin_op(record, store, {
        "project": "moonmind-test", "projectDir": str(tmp_path),
        "composeFiles": ["docker-compose.yaml"], "services": ["api"],
    })
    monkeypatch.setattr(server.engine, "subprocess_runner", lambda: _verification_runner(server.engine))

    def unavailable(*args, **kwargs):
        raise OSError("service observation unavailable")

    monkeypatch.setattr(server.engine, "observe_services", unavailable)
    result = server.production_apply(store, operation)

    final = store.load(operation["operationId"])
    assert final["installed"]["image"] == operation["desired"]["image"]
    assert final["status"] == result["status"] == "partially_verified"
    assert any(check["name"] == "service-observation" and check["status"] == "unavailable"
               for check in final["verification"])


def test_production_apply_still_fails_a_one_shot_that_exited_non_zero(
    controller_path, tmp_path, monkeypatch
):
    import json as _json

    record = load("record")
    server_mod = load("server")
    monkeypatch.setattr(server_mod, "SERVICE_READINESS_TIMEOUT_SECONDS", 0)
    store = record.OperationStore(tmp_path / "state")
    target = {
        "project": "moonmind",
        "projectDir": str(tmp_path),
        "composeFiles": ["docker-compose.yaml"],
        "services": ["api", "init-db"],
    }
    op = _begin_op(record, store, target)

    class _Runner:
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": ""}
            if "ps" in args:
                rows = [
                    {"Service": "api", "State": "running"},
                    {"Service": "init-db", "State": "exited", "ExitCode": 3},
                ]
                return {"exit": 0, "output": "\n".join(_json.dumps(r) for r in rows)}
            return {"exit": 0, "output": "ok"}

    monkeypatch.setattr(server_mod.engine, "subprocess_runner", lambda: _Runner())
    result = server_mod.production_apply(store, op, dispatch_probe=lambda: True)
    assert result["status"] == "partially_verified", result
    checks = {
        check["name"]: check for check in store.load(op["operationId"])["verification"]
    }
    assert checks["service:init-db"]["status"] == "failed"


def test_production_apply_partially_verifies_failed_operator_access(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server_mod = load("server")
    store = record.OperationStore(tmp_path / "state")
    target = {
        "project": "moonmind",
        "projectDir": str(tmp_path),
        "composeFiles": ["docker-compose.yaml"],
        "services": ["api"],
        # Nothing listens here: the access check must fail explicitly.
        "operatorUrls": ["http://127.0.0.1:1"],
    }
    op = _begin_op(record, store, target)
    monkeypatch.setattr(
        server_mod.engine,
        "subprocess_runner",
        lambda: _verification_runner(server_mod.engine),
    )
    result = server_mod.production_apply(store, op)
    assert result["status"] == "partially_verified", result
    loaded = store.load(op["operationId"])
    # Installation stays confirmed; missing mandatory verification cannot
    # become silent success.
    assert loaded["installed"]["image"] == "ghcr.io/org/app@sha256:abc"
    assert loaded["status"] == "partially_verified"
    access = [
        check
        for check in loaded["verification"]
        if check["name"].startswith("operator-access:")
    ]
    assert access and access[0]["status"] == "failed"


def test_production_apply_delegates_omnigent_selection_and_migration(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server_mod = load("server")
    (tmp_path / ".env").write_text("OMNIGENT_IMAGE_TAG=latest\n", encoding="utf-8")
    store = record.OperationStore(tmp_path / "controller-state")
    target = {
        "project": "moonmind-test-recorded-project",
        "projectDir": str(tmp_path),
        "composeFiles": ["docker-compose.yaml", "docker-compose.override.yaml"],
        "services": ["api", "omnigent"],
    }
    op = _begin_op(record, store, target)
    commands = []
    phase = []
    release_env = tmp_path / "deploy" / "state" / ".env.deploy"

    class Runner:
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": ""}
            commands.append(tuple(args))
            assert 0 < timeout_seconds <= 900
            if "run" in args:
                assert args[args.index("--project-name") + 1] == target["project"]
                assert "--no-deps" in args and "--rm" in args and "-T" in args
                assert args[args.index("--entrypoint") + 1] == "python"
                assert (
                    f"moonmind.controller.omnigent.operation={op['operationId']}"
                    in args
                )
                assert "temporal-worker-deployment-control" in args
                helper_files = [
                    args[i + 1] for i, part in enumerate(args) if part == "-f"
                ]
                helper = json.loads(Path(helper_files[-1]).read_text())["services"][
                    "temporal-worker-deployment-control"
                ]
                assert helper["image"] == op["desired"]["image"]
                # Selection must work while the target project's proxy and
                # workers are stopped. Only this trusted one-off receives
                # the controller-owned daemon transport.
                assert (
                    helper["environment"]["DOCKER_HOST"]
                    == "unix:///var/run/docker.sock"
                )
                assert (
                    helper["environment"]["SYSTEM_DOCKER_HOST"]
                    == "unix:///var/run/docker.sock"
                )
                assert (
                    "/var/run/docker.sock:/var/run/docker.sock:ro" in helper["volumes"]
                )
                assert args[-3] == "moonmind.workflows.skills.deployment_release"
                payload = json.loads(args[-1])
                assert payload["operationId"] == op["operationId"]
                assert payload["target"] == target
                assert payload["moonmindImage"] == op["desired"]["image"]
                assert payload["controllerCapabilities"] == [
                    "active-journal-transition", "request-identity",
                    "unversioned-routing-cutover", "controller-handoff",
                ]
                if args[-2] == "--omnigent-select":
                    assert "up" not in phase
                    phase.append("select")
                    release_env.parent.mkdir(parents=True, exist_ok=True)
                    release_env.write_text("OMNIGENT_IMAGE_REF=server@sha256:new\n")
                    receipt = {"status": "advanced", "revision": 7}
                else:
                    assert args[-2] == "--omnigent-migrate"
                    assert phase == ["select", "pull", "up"]
                    assert payload["selectedRevision"] == 7
                    phase.append("migrate")
                    receipt = {"status": "converged", "revision": 7}
                return {
                    "exit": 0,
                    "output": "migration log\nMOONMIND_OMNIGENT_RESULT="
                    + json.dumps(receipt),
                }
            if "pull" in args or "up" in args:
                phase.append("pull" if "pull" in args else "up")
                assert str(release_env) in args
                files = [
                    args[i + 1] for i, part in enumerate(args) if part == "--env-file"
                ]
                assert files[0] == str(tmp_path / ".env")
                assert files[1] == str(release_env)
                assert files[-1].endswith(f"{op['operationId']}.env")
            if "ps" in args:
                return {
                    "exit": 0,
                    "output": json.dumps(
                        [
                            {"Service": name, "State": "running"}
                            for name in target["services"]
                        ]
                    ),
                }
            return {"exit": 0, "output": "ok"}

    monkeypatch.setattr(server_mod.engine, "subprocess_runner", lambda: Runner())
    result = server_mod.production_apply(store, op)
    assert result["status"] == "succeeded", result
    assert phase == ["select", "pull", "up", "migrate"]
    stored = store.load(op["operationId"])
    assert stored["omnigentSelection"]["revision"] == 7
    assert stored["status"] == "succeeded"
    assert all(check["status"] == "passed" for check in stored["verification"])


def test_legacy_probe_only_excludes_recorded_controller_one_offs(controller_path):
    server_mod = load("server")
    known = {"operation-1": "moonmind-test-owned"}
    own = "controller-step\ttrue\tmoonmind-test-owned\toperation-1\n"
    assert (
        server_mod.parse_legacy_mutation_ps(own, controller_operations=known) is False
    )
    assert server_mod.parse_legacy_mutation_ps(own, controller_operations={}) is True
    assert (
        server_mod.parse_legacy_mutation_ps(
            "foreign-step\ttrue\tmoonmind-test-other\toperation-1\n",
            controller_operations=known,
        )
        is True
    )
    assert (
        server_mod.parse_legacy_mutation_ps(
            own + "legacy-step\ttrue\tmoonmind-test-owned\t\n",
            controller_operations=known,
        )
        is True
    )


def test_default_compose_omnigent_service_requires_migration_without_env(
    controller_path,
):
    server_mod = load("server")
    assert server_mod.omnigent_channels_for_target({"services": ["api", "omnigent"]})


def test_conflict_bodies_never_expose_error_detail(
    controller_path, tmp_path
):
    server_mod = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")
    app = server_mod.build_app(
        store=store,
        secret="test-secret",
        applier=lambda operation: None,
        legacy_writer_probe=lambda: True,
    )
    from wsgiref.simple_server import make_server

    httpd = make_server("127.0.0.1", 0, app)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        import urllib.error

        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/operations",
            data=json.dumps(
                {"stack": "moonmind", "desiredImage": "img", "sourceRevision": "r"}
            ).encode(),
            method="POST",
            headers={"Authorization": "Bearer test-secret"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10):
                raise AssertionError("expected a 409 for the legacy writer")
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
            body = json.loads(exc.read().decode() or "{}")
            assert body == {
                "error": (
                    "a legacy writer (an application-owned updater or a "
                    "controller from the retired shell installer) may still be "
                    "active; stop or reconcile it before the controller takes "
                    "over (REQ-07)"
                )
            }
    finally:
        httpd.shutdown()
        thread.join(timeout=10)


def test_restart_orders_tied_timestamps_by_record_mtime(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server_mod = load("server")
    frozen = "2026-09-24T00:00:00.000+00:00"
    monkeypatch.setattr(record, "_utc_now", lambda: frozen)
    monkeypatch.setattr(server_mod.record_mod, "_utc_now", lambda: frozen)
    store = record.OperationStore(tmp_path / "state")
    stale = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:aaa",
        source_revision="aaa",
    )
    current = store.begin(
        stack="moonmind",
        desired_image="ghcr.io/org/app@sha256:bbb",
        source_revision="bbb",
    )
    assert stale["createdAt"] == current["createdAt"]
    applied = []

    def applier(operation):
        applied.append(operation["operationId"])
        store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    result = server_mod.converge_on_restart(store, applier=applier)
    assert applied == [current["operationId"]]
    assert result["superseded"] == [stale["operationId"]]


class _ThreadedHarness:
    """Serve the app through the production (threaded) HTTP server."""

    def __init__(self, tmp_path, applier):
        self.server_mod = load("server")
        self.record = load("record")
        self.store = self.record.OperationStore(tmp_path / "state")
        self.app = self.server_mod.build_app(
            store=self.store, secret="test-secret", applier=applier
        )
        self.httpd = self.server_mod.make_http_server("127.0.0.1", 0, self.app)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def call(self, method, path, body=None, timeout=10):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Authorization": "Bearer test-secret"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    def close(self):
        self.httpd.shutdown()
        self.thread.join(timeout=10)


def test_lost_ack_and_duplicate_submissions_reattach_to_one_writer(
    controller_path, tmp_path
):
    release = threading.Event()
    applied = []
    holder = {}

    def slow_applier(operation):
        applied.append(operation["operationId"])
        holder["store"].mark_stage(operation["operationId"], stage="applying")
        assert release.wait(timeout=30)
        holder["store"].confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    harness = _ThreadedHarness(tmp_path, slow_applier)
    holder["store"] = harness.store
    body = {
        "stack": "moonmind",
        "desiredImage": "ghcr.io/org/app@sha256:abc",
        "operationId": "ui-op-1",
    }
    first = {}

    def submit_first():
        first["result"] = harness.call("POST", "/v1/operations", body, timeout=30)

    submitter = threading.Thread(target=submit_first, daemon=True)
    try:
        submitter.start()
        # The caller's own identity is observable while the apply runs: a
        # client that lost its acknowledgment polls instead of resubmitting.
        observed = None
        for _ in range(100):
            status, observed = harness.call("GET", "/v1/operations/ui-op-1")
            if status == 200 and observed.get("status") == "applying":
                break
            threading.Event().wait(0.05)
        assert observed and observed["status"] == "applying", observed
        # A resubmission after a lost acknowledgment, and a duplicate from a
        # second client with its own id, both reattach to the running apply.
        status, again = harness.call("POST", "/v1/operations", body)
        assert (status, again["operationId"]) == (202, "ui-op-1")
        status, duplicate = harness.call(
            "POST", "/v1/operations", {**body, "operationId": "host-op-2"}
        )
        assert (status, duplicate["operationId"]) == (202, "ui-op-1")
        # A changed target is new intent: it is refused while the stack is
        # owned, naming the operation that owns it, and records nothing.
        status, conflict = harness.call(
            "POST",
            "/v1/operations",
            {"stack": "moonmind", "desiredImage": "ghcr.io/org/app@sha256:def"},
        )
        assert status == 409
        assert conflict == {
            "error": "stack is owned by another writer",
            "activeOperationId": "ui-op-1",
        }
        release.set()
        submitter.join(timeout=30)
        assert first["result"][0] == 202
        assert first["result"][1]["status"] == "succeeded"
        assert applied == ["ui-op-1"]
        assert len(list((tmp_path / "state" / "operations").glob("*.json"))) == 1
        # After completion the same identity still observes the one result.
        status, final = harness.call("POST", "/v1/operations", body)
        assert (status, final["status"], applied) == (202, "succeeded", ["ui-op-1"])
    finally:
        release.set()
        harness.close()


def test_each_update_request_for_latest_pulls_and_applies_again(
    controller_path, tmp_path
):
    applied = []
    holder = {}

    def applier(operation):
        applied.append(operation["operationId"])
        holder["store"].confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    harness = _ThreadedHarness(tmp_path, applier)
    holder["store"] = harness.store
    body = {
        "stack": "moonmind",
        "desiredImage": "ghcr.io/org/app:latest",
        "operationId": "ui-1",
    }
    try:
        status, first = harness.call("POST", "/v1/operations", body)
        assert (status, first["operationId"], first["status"]) == (
            202, "ui-1", "succeeded"
        )
        # The next update to `latest` runs its own apply (which pulls the
        # tag again) instead of answering with the earlier success.
        status, second = harness.call(
            "POST", "/v1/operations", {**body, "operationId": "ui-2"}
        )
        assert (status, second["operationId"], second["status"]) == (
            202, "ui-2", "succeeded"
        )
        assert applied == ["ui-1", "ui-2"]
        # Submitters require this advertisement before trusting a 202.
        status, health = harness.call("GET", "/v1/healthz")
        assert status == 200
        assert "request-identity" in health["capabilities"]
    finally:
        harness.close()


def test_operation_id_naming_a_different_request_is_refused(
    controller_path, tmp_path
):
    harness = _ThreadedHarness(tmp_path, lambda operation: None)
    try:
        harness.store.begin(
            stack="moonmind",
            desired_image="ghcr.io/org/app@sha256:abc",
            source_revision="",
            operation_id="op-a",
        )
        status, body = harness.call(
            "POST",
            "/v1/operations",
            {
                "stack": "moonmind",
                "desiredImage": "ghcr.io/org/app@sha256:def",
                "operationId": "op-a",
            },
        )
        assert status == 409
        assert body == {"error": "operation id names a different request"}
        status, _ = harness.call(
            "POST",
            "/v1/operations",
            {"stack": "moonmind", "desiredImage": "img", "operationId": "../x"},
        )
        assert status == 400
    finally:
        harness.close()


def test_list_operations_is_bounded_newest_first_and_redacted(
    controller_path, tmp_path
):
    harness = _ThreadedHarness(tmp_path, lambda operation: None)
    try:
        store = harness.store
        older = store.begin(
            stack="moonmind", desired_image="img:1", source_revision="", operation_id="op-1"
        )
        store.confirm_installed(older["operationId"], image="img:1")
        newer = store.begin(
            stack="moonmind", desired_image="img:2", source_revision="", operation_id="op-2"
        )
        store.record_attempt_error(
            newer["operationId"], error="pull failed: password=hunter2"
        )
        store.begin(
            stack="other", desired_image="img:3", source_revision="", operation_id="op-3"
        )
        status, listed = harness.call("GET", "/v1/operations?stack=moonmind&limit=5")
        assert status == 200
        assert [op["operationId"] for op in listed["operations"]] == ["op-2", "op-1"]
        assert "hunter2" not in json.dumps(listed)
        status, limited = harness.call("GET", "/v1/operations?stack=moonmind&limit=1")
        assert [op["operationId"] for op in limited["operations"]] == ["op-2"]
        status, logs = harness.call("GET", "/v1/operations/op-2/logs")
        assert status == 200
        assert "hunter2" not in json.dumps(logs)
        assert logs["errorSummary"].startswith("attempt 1: pull failed")
    finally:
        harness.close()


def test_default_target_is_derived_from_the_mounted_checkout(
    controller_path, tmp_path
):
    server_mod = load("server")
    repo = tmp_path / "MoonMind"
    repo.mkdir()
    (repo / "docker-compose.yaml").write_text("services: {}\n")
    (repo / "site.yaml").write_text("services: {}\n")
    (repo / ".env").write_text(
        "COMPOSE_FILE=docker-compose.yaml:site.yaml\nCOMPOSE_PROJECT_NAME=moonmind\n"
    )
    commands = []

    class _Runner:
        def run(self, args, timeout_seconds):
            commands.append(tuple(args))
            return {
                "exit": 0,
                "output": "api\ndocker-proxy\nsandbox-egress-proxy\npostgres\n",
            }

    target = server_mod.default_target("moonmind", repo=str(repo), runner=_Runner())
    assert target == {
        "project": "moonmind",
        "projectDir": str(repo),
        "composeFiles": ["docker-compose.yaml", "site.yaml"],
        "services": ["api", "postgres"],
        "envFile": str(repo / ".env"),
    }
    assert commands[0][-2:] == ("config", "--services")
    assert ("--env-file", str(repo / ".env")) == commands[0][
        commands[0].index("--env-file") : commands[0].index("--env-file") + 2
    ]


def test_production_target_resolver_uses_the_recorded_target_project(
    controller_path, tmp_path, monkeypatch
):
    """A `-p` deployment is updated in place, never as a parallel `moonmind`."""
    server_mod = load("server")
    repo = tmp_path / "moonmind-prod"
    repo.mkdir()
    (repo / "docker-compose.yaml").write_text("services: {}\n")
    commands = []

    class _Runner:
        def run(self, args, timeout_seconds):
            commands.append(tuple(args))
            return {"exit": 0, "output": "api\n"}

    monkeypatch.setattr(server_mod.engine, "subprocess_runner", lambda: _Runner())
    monkeypatch.setenv("MOONMIND_CONTROLLER_TARGET_REPO", str(repo))
    monkeypatch.setenv("MOONMIND_CONTROLLER_TARGET_PROJECT", "my-instance")
    target = server_mod.production_target_resolver("moonmind")
    assert target["project"] == "my-instance"
    assert commands[0][commands[0].index("--project-name") + 1] == "my-instance"


def _compose_file_args(command):
    return [command[i + 1] for i, arg in enumerate(command) if arg == "-f"]


def test_default_target_resolves_compose_files_from_any_working_directory(
    controller_path, tmp_path, monkeypatch
):
    """The controller runs from its own directory, not the checkout.

    Compose resolves a relative ``-f`` against the process working
    directory, so the files it is given must name the checkout's own files.
    """
    import os

    server_mod = load("server")
    repo = tmp_path / "MoonMind"
    repo.mkdir()
    (repo / "docker-compose.yaml").write_text("services: {}\n")
    (repo / "docker-compose.override.yaml").write_text("services: {}\n")
    elsewhere = tmp_path / "opt-moonmind-controller"
    elsewhere.mkdir()
    (elsewhere / "docker-compose.yaml").write_text("services: {decoy: {}}\n")
    monkeypatch.chdir(elsewhere)
    commands = []

    class _Runner:
        def run(self, args, timeout_seconds):
            commands.append(tuple(args))
            return {"exit": 0, "output": "api\n"}

    target = server_mod.default_target("moonmind", repo=str(repo), runner=_Runner())
    assert target["composeFiles"] == [
        "docker-compose.yaml",
        "docker-compose.override.yaml",
    ]
    files = _compose_file_args(commands[0])
    assert [os.path.realpath(name) for name in files] == [
        str((repo / "docker-compose.yaml").resolve()),
        str((repo / "docker-compose.override.yaml").resolve()),
    ]


def test_default_target_uses_the_override_file_and_refuses_escapes(
    controller_path, tmp_path
):
    import pytest

    server_mod = load("server")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yaml").write_text("services: {}\n")
    (repo / "docker-compose.override.yaml").write_text("services: {}\n")

    class _Runner:
        def run(self, args, timeout_seconds):
            return {"exit": 0, "output": "api\n"}

    target = server_mod.default_target("moonmind", repo=str(repo), runner=_Runner())
    assert target["composeFiles"] == [
        "docker-compose.yaml",
        "docker-compose.override.yaml",
    ]
    assert target["project"] == "moonmind"
    assert "envFile" not in target
    (repo / ".env").write_text("COMPOSE_FILE=../outside.yaml\n")
    with pytest.raises(ValueError):
        server_mod.default_target("moonmind", repo=str(repo), runner=_Runner())
    with pytest.raises(ValueError):
        server_mod.default_target("moonmind", repo=str(tmp_path / "missing"), runner=_Runner())


def test_submission_without_target_applies_the_derived_target(
    controller_path, tmp_path
):
    applied = []

    def applier(operation):
        applied.append(operation["target"])

    server_mod = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")
    derived = {
        "project": "moonmind",
        "projectDir": "/srv/moonmind",
        "composeFiles": ["docker-compose.yaml"],
        "services": ["api"],
    }
    app = server_mod.build_app(
        store=store,
        secret="test-secret",
        applier=applier,
        target_resolver=lambda stack: dict(derived),
    )
    httpd = server_mod.make_http_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, created = _post_operation(
            httpd.server_address[1],
            {"stack": "moonmind", "desiredImage": "img", "operationId": "ui-1"},
        )
        assert status == 202, created
        assert applied == [derived]
        assert store.load("ui-1")["target"] == derived
    finally:
        httpd.shutdown()
        thread.join(timeout=10)


def test_submission_without_a_derivable_target_is_refused_without_a_record(
    controller_path, tmp_path
):
    server_mod = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")

    def missing(stack):
        raise ValueError("no deployment checkout is mounted for the controller")

    app = server_mod.build_app(
        store=store, secret="test-secret", applier=lambda op: None, target_resolver=missing
    )
    httpd = server_mod.make_http_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _post_operation(
            httpd.server_address[1], {"stack": "moonmind", "desiredImage": "img"}
        )
        assert status == 400
        assert body["error"].startswith("deployment target could not be derived")
        assert store.list_open() == []
    finally:
        httpd.shutdown()
        thread.join(timeout=10)


def test_default_target_reports_compose_failures_as_refusals(controller_path, tmp_path):
    import pytest

    engine_mod = load("engine")
    server_mod = load("server")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.yaml").write_text("services: {}\n")

    class _Failing:
        def __init__(self, error):
            self.error = error

        def run(self, args, timeout_seconds):
            raise self.error

    for error in (
        engine_mod.CommandError("config", 1, "service api: token=hunter2 invalid"),
        FileNotFoundError("docker"),
    ):
        with pytest.raises(ValueError) as refused:
            server_mod.default_target("moonmind", repo=str(repo), runner=_Failing(error))
        assert "hunter2" not in str(refused.value)


def test_reattaching_submission_does_not_rederive_the_target(controller_path, tmp_path):
    server_mod = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")
    derived = []

    def resolver(stack):
        derived.append(stack)
        return {
            "project": "moonmind",
            "projectDir": "/srv/moonmind",
            "composeFiles": ["docker-compose.yaml"],
            "services": ["api"],
        }

    def applier(operation):
        store.confirm_installed(operation["operationId"], image=operation["desired"]["image"])

    app = server_mod.build_app(
        store=store, secret="test-secret", applier=applier, target_resolver=resolver
    )
    httpd = server_mod.make_http_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        body = {"stack": "moonmind", "desiredImage": "img", "operationId": "ui-1"}
        assert _post_operation(port, body)[0] == 202
        status, again = _post_operation(port, body)
        assert (status, again["operationId"]) == (202, "ui-1")
        assert derived == ["moonmind"]
        # A new request is new work: its own operation gets a fresh target.
        status, fresh = _post_operation(port, {**body, "operationId": "ui-2"})
        assert (status, fresh["operationId"]) == (202, "ui-2")
        assert derived == ["moonmind", "moonmind"]
    finally:
        httpd.shutdown()
        thread.join(timeout=10)


def test_retry_never_rewrites_an_operation_that_is_still_applying(
    controller_path, tmp_path
):
    release = threading.Event()
    holder = {}

    def slow(operation):
        holder["store"].mark_stage(operation["operationId"], stage="applying")
        assert release.wait(timeout=30)
        holder["store"].confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    harness = _ThreadedHarness(tmp_path, slow)
    holder["store"] = harness.store
    body = {"stack": "moonmind", "desiredImage": "img", "operationId": "op-run"}
    submitter = threading.Thread(
        target=lambda: harness.call("POST", "/v1/operations", body, timeout=30),
        daemon=True,
    )
    try:
        submitter.start()
        for _ in range(100):
            if harness.call("GET", "/v1/operations/op-run")[1].get("status") == "applying":
                break
            threading.Event().wait(0.05)
        status, _ = harness.call("POST", "/v1/operations/op-run/retry")
        assert status == 409
        record = harness.store.load("op-run")
        assert (record["status"], record["attemptGroup"]) == ("applying", 1)
    finally:
        release.set()
        submitter.join(timeout=30)
        harness.close()


def test_resubmitting_an_orphaned_open_operation_resumes_it(controller_path, tmp_path):
    applied = []
    holder = {}

    def applier(operation):
        applied.append(operation["operationId"])
        holder["store"].confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    harness = _ThreadedHarness(tmp_path, applier)
    holder["store"] = harness.store
    try:
        # Recorded before an interruption that left nobody applying it.
        harness.store.begin(
            stack="moonmind", desired_image="img", source_revision="", operation_id="op-orphan"
        )
        status, resumed = harness.call(
            "POST",
            "/v1/operations",
            {"stack": "moonmind", "desiredImage": "img", "operationId": "op-orphan"},
        )
        assert (status, resumed["status"], applied) == (202, "succeeded", ["op-orphan"])
    finally:
        harness.close()


def test_omnigent_command_failure_keeps_the_original_migration_error(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server_mod = load("server")
    (tmp_path / ".env").write_text("OMNIGENT_IMAGE_TAG=latest\n")
    store = record.OperationStore(tmp_path / "state")
    op = _begin_op(
        record,
        store,
        {
            "project": "moonmind-test",
            "projectDir": str(tmp_path),
            "composeFiles": ["docker-compose.yaml"],
            "services": ["api"],
        },
    )

    class Runner:
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": ""}
            if "--omnigent-select" in args:
                return {
                    "exit": 0,
                    "output": "MOONMIND_OMNIGENT_RESULT="
                    + json.dumps({"status": "advanced", "revision": 3}),
                }
            if "--omnigent-migrate" in args:
                return {"exit": 1, "output": "Omnigent catalog unavailable"}
            if "ps" in args:
                return {
                    "exit": 0,
                    "output": json.dumps({"Service": "api", "State": "running"}),
                }
            return {"exit": 0, "output": "ok"}

    monkeypatch.setattr(server_mod.engine, "subprocess_runner", lambda: Runner())
    result = server_mod.production_apply(store, op)
    assert result["status"] == "partially_verified"
    operation = store.load(op["operationId"])
    assert operation["installed"]["image"] == op["desired"]["image"]
    checks = [c for c in operation["verification"] if c["name"] == "omnigent-migration"]
    assert checks[-1]["status"] == "failed"
    assert "Omnigent catalog unavailable" in checks[-1]["detail"]


def test_omnigent_selection_failure_never_recreates_the_fleet(
    controller_path, tmp_path, monkeypatch
):
    import pytest

    record = load("record")
    server_mod = load("server")
    store = record.OperationStore(tmp_path / "state")
    op = _begin_op(
        record,
        store,
        {
            "project": "moonmind-test",
            "projectDir": str(tmp_path),
            "composeFiles": ["docker-compose.yaml"],
            "services": ["api", "omnigent"],
        },
    )
    commands = []

    class Runner:
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": ""}
            commands.append(args)
            return {"exit": 1, "output": "Omnigent image unavailable"}

    monkeypatch.setattr(server_mod.engine, "subprocess_runner", lambda: Runner())
    with pytest.raises(server_mod.engine.StageError):
        server_mod.production_apply(store, op)
    operation = store.load(op["operationId"])
    assert operation["installed"] is None
    assert "Omnigent image unavailable" in operation["errorSummary"]
    assert not any("up" in command for command in commands)


def test_default_legacy_probe_keeps_orphaned_and_terminal_mutators_observable(
    controller_path, tmp_path, monkeypatch
):
    import subprocess
    from types import SimpleNamespace

    record = load("record")
    lock_mod = load("lock")
    server_mod = load("server")
    state_dir = tmp_path / "state"
    store = record.OperationStore(state_dir)
    op = _begin_op(
        record,
        store,
        {
            "project": "moonmind-test-owned",
            "projectDir": str(tmp_path),
            "composeFiles": ["docker-compose.yaml"],
            "services": ["api"],
        },
    )
    # A labelled one-off is not enough: the current record and kernel owner
    # must both prove it belongs to the in-progress controller step.
    store.record_omnigent_step(op["operationId"], "select")
    monkeypatch.setenv("MOONMIND_CONTROLLER_STATE_DIR", str(state_dir))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=f"step\ttrue\tmoonmind-test-owned\t{op['operationId']}\n",
        ),
    )
    assert server_mod.default_legacy_writer_probe() is True
    with lock_mod.StackLock(state_dir, "moonmind").acquire():
        assert server_mod.default_legacy_writer_probe() is False
        store.supersede(
            op["operationId"], reason="a different operation now owns recovery"
        )
        assert server_mod.default_legacy_writer_probe() is True


def test_uncertain_omnigent_launch_is_observed_before_a_bounded_retry(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server_mod = load("server")
    store = record.OperationStore(tmp_path / "state")
    op = _begin_op(
        record,
        store,
        {
            "project": "moonmind-test",
            "projectDir": str(tmp_path),
            "composeFiles": ["docker-compose.yaml"],
            "services": ["api", "omnigent"],
        },
    )
    launches = []

    class Runner:
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": "still-running-helper" if launches else ""}
            if "--omnigent-select" in args:
                launches.append(args)
                raise server_mod.engine.CommandError(
                    "run", 124, "acknowledgment timed out"
                )
            raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(server_mod.engine, "subprocess_runner", lambda: Runner())
    result = server_mod._apply_recording_failure(
        store,
        op["operationId"],
        lambda operation: server_mod.production_apply(store, operation),
    )
    assert result["status"] == "failed"
    assert len(launches) == 1
    assert "acknowledgment timed out" in result["errorSummary"]
    assert "still running" in result["attempts"][-1]["error"]


def test_restart_recovery_skips_work_another_owner_finished_before_the_lock(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server = load("server")
    lock = server.lock_mod
    store = record.OperationStore(tmp_path / "state")
    op = _begin_op(record, store, {"project": "moonmind-test", "services": ["api"]})
    acquire = lock.StackLock.acquire

    def finished_meanwhile(self):
        # A submission reattached, applied and released the stack between the
        # restart's snapshot of open work and this owner's exclusion.
        store.confirm_installed(op["operationId"], image=op["desired"]["image"])
        return acquire(self)

    monkeypatch.setattr(lock.StackLock, "acquire", finished_meanwhile)

    class Runner:
        def run(self, args, timeout_seconds):
            return {"exit": 0, "output": ""}

    def applier(operation):
        pytest.fail("restart recovery repeated a confirmed installation")

    server.resume_open_operations(store, applier, runner=Runner())
    assert store.load(op["operationId"])["attempts"] == []


@pytest.mark.parametrize("observation", ["running", "unobservable", "failed-exit"])
def test_apply_never_starts_beside_a_previous_owners_release_step(
    controller_path, tmp_path, monkeypatch, observation
):
    record = load("record")
    server = load("server")
    store = record.OperationStore(tmp_path / "state")
    op = _begin_op(record, store, {"project": "moonmind-test", "projectDir": str(tmp_path),
                                   "composeFiles": ["docker-compose.yaml"],
                                   "services": ["api"]})
    commands = []

    class Runner:
        def run(self, args, timeout_seconds):
            commands.append(tuple(args))
            if args[:2] != ("docker", "ps"):
                pytest.fail(f"a writer started beside the previous owner: {args}")
            if observation == "running":
                return {"exit": 0, "output": "abc123\n", "stdout": "abc123\n"}
            if observation == "failed-exit":
                return {"exit": 1, "output": "daemon unavailable"}
            raise server.engine.CommandError("ps", 1, "daemon unavailable")

    monkeypatch.setattr(server.engine, "subprocess_runner", lambda: Runner())
    # A handed-off or interrupted owner's one-off may still run: an observed
    # or unobservable step is never proof of absence.
    with pytest.raises(server.engine.StageError):
        server.production_apply(store, op)
    assert all(command[:2] == ("docker", "ps") for command in commands)
    # Operation identities are caller-supplied; only this deployment's
    # project can hold its previous owner.
    assert any(
        f"label={server.OMNIGENT_OPERATION_LABEL}={op['operationId']}" in command
        and "label=com.docker.compose.project=moonmind-test" in command
        for command in commands
    )
    attempt = store.load(op["operationId"])["attempts"][-1]["error"]
    assert "previous owner" in attempt


def test_refused_submission_for_a_busy_stack_records_no_operation(
    controller_path, tmp_path, monkeypatch
):
    lock = load("lock")
    applied = []
    harness = _ThreadedHarness(tmp_path, lambda operation: applied.append(operation))
    # The writer takes the stack after the ownership probe answered.
    monkeypatch.setattr(lock.StackLock, "probe", lambda self: False)
    try:
        with lock.StackLock(harness.store.state_dir, "moonmind").acquire():
            status, body = harness.call(
                "POST", "/v1/operations", {"stack": "moonmind", "desiredImage": "img"}
            )
        assert (status, body) == (409, {"error": "stack is owned by another writer"})
        # No open record is left for restart recovery to apply later.
        assert harness.store.list_open() == []
        assert applied == []
    finally:
        harness.close()


class _RetiredControllerDocker:
    """Docker as seen beside a controller from the retired shell installer."""

    def __init__(self, *, listed="retired1\n", state="idle", fail=None, exit_code=None):
        self.listed, self.state, self.fail = listed, state, fail
        self.exit_code = exit_code
        self.commands = []

    def run(self, args, timeout_seconds):
        self.commands.append(tuple(args))
        if self.exit_code and args[1] == self.exit_code:
            return {"exit": 1, "output": "", "stdout": ""}
        if self.fail and args[1] == self.fail:
            raise OSError("docker unavailable")
        if args[1] == "ps":
            return {"exit": 0, "output": self.listed, "stdout": self.listed}
        if args[1] == "inspect":
            return {"exit": 0, "output": "", "stdout": "sha256:retired\n"}
        if args[1] == "run":
            return {"exit": 0, "output": self.state, "stdout": self.state + "\n"}
        return {"exit": 0, "output": ""}


def test_idle_retired_shell_controller_is_stopped_with_its_state_kept(controller_path):
    server = load("server")
    docker = _RetiredControllerDocker()
    assert server.reconcile_retired_shell_controller(docker) is False
    assert docker.commands[0][:3] == ("docker", "ps", "--quiet")
    assert f"volume={server.RETIRED_CONTROLLER_VOLUME}" in docker.commands[0]
    # Frozen before it is inspected, so no request it accepts can start work
    # between the idle answer and the stop.
    assert docker.commands[1] == ("docker", "pause", "retired1")
    assert docker.commands[2][:2] == ("docker", "inspect")
    check = docker.commands[3]
    assert check[:3] == ("docker", "run", "--rm")
    assert ("--network", "none") == check[check.index("--network"):][:2]
    volume = check[check.index("--volume") + 1]
    assert volume == (
        f"{server.RETIRED_CONTROLLER_VOLUME}:{server.RETIRED_CONTROLLER_STATE_DIR}:ro"
    )
    assert "sha256:retired" in check
    assert check[-1] == server.RETIRED_CONTROLLER_STATE_DIR
    # Killed while frozen, never removed: its container, logs, volume and
    # secret remain.
    assert docker.commands[4] == ("docker", "kill", "retired1")
    assert len(docker.commands) == 5


@pytest.mark.parametrize("state", ["unfinished", "locked", "unreadable", ""])
def test_busy_or_unreadable_retired_shell_controller_defers_the_controller(
    controller_path, state
):
    server = load("server")
    docker = _RetiredControllerDocker(state=state)
    assert server.reconcile_retired_shell_controller(docker) is True
    assert not any(command[1] in ("stop", "kill") for command in docker.commands)
    assert docker.commands[-1] == ("docker", "unpause", "retired1")


@pytest.mark.parametrize("step", ["ps", "pause", "inspect", "run", "kill"])
@pytest.mark.parametrize("how", ["error", "exit"])
def test_uninspectable_retired_shell_controller_is_never_assumed_absent(
    controller_path, step, how
):
    server = load("server")
    docker = _RetiredControllerDocker(
        fail=step if how == "error" else None,
        exit_code=step if how == "exit" else None,
    )
    with pytest.raises(server.LegacyWriterUnknown):
        server.reconcile_retired_shell_controller(docker)
    if step in ("inspect", "run", "kill"):
        # A frozen controller is never left frozen by a failed reconciliation.
        assert docker.commands[-1] == ("docker", "unpause", "retired1")


def test_no_retired_shell_controller_needs_no_transition(controller_path):
    server = load("server")
    docker = _RetiredControllerDocker(listed="")
    assert server.reconcile_retired_shell_controller(docker) is False
    assert len(docker.commands) == 1


@pytest.mark.parametrize(
    "case, expected",
    [
        ("no-record", "idle"),
        ("installed", "idle"),
        ("unfinished", "unfinished"),
        ("corrupt", "unreadable"),
        ("held-lock", "locked"),
    ],
)
def test_retired_controller_idle_check_reads_its_record_and_kernel_lock(
    controller_path, tmp_path, case, expected
):
    import fcntl
    import subprocess
    import sys

    server = load("server")
    (tmp_path / "locks").mkdir()
    lock_file = tmp_path / "locks" / "moonmind.lock"
    lock_file.write_text('{"contract": "moonmind.deployment-kernel-lock.v1"}')
    record = {
        "schema": "moonmind-controller-operation/v1",
        "status": "desired",
        "desired": {"targetImage": "img:new"},
        "installed": None,
    }
    if case == "installed":
        record.update(status="installed", installed={"image": "img:new"})
    if case in ("installed", "unfinished", "held-lock"):
        (tmp_path / "operation.json").write_text(json.dumps(record))
    if case == "corrupt":
        (tmp_path / "operation.json").write_text("{not-json")
    if case == "held-lock":
        (tmp_path / "operation.json").write_text(
            json.dumps({**record, "status": "installed", "installed": {"image": "img:new"}})
        )
    with open(lock_file, "a+b") as held:
        if case == "held-lock":
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        completed = subprocess.run(
            [sys.executable, "-c", server.RETIRED_CONTROLLER_IDLE_CHECK, str(tmp_path)],
            capture_output=True, text=True, timeout=30, check=True,
        )
    assert completed.stdout.strip() == expected
