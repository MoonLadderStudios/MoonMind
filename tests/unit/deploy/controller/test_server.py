"""Authenticated local endpoint + restart convergence (REQ-02, REQ-04)."""
import json
import threading
import urllib.request
from wsgiref.simple_server import make_server

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
        status, retried = harness.call(
            "POST", f"/v1/operations/{op_id}/retry", secret="test-secret"
        )
        assert status == 202
        assert retried["attemptGroup"] == 2
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
        try:
            with urllib.request.urlopen(request, timeout=10):
                raise AssertionError("expected a 500 after the retry budget")
        except urllib.error.HTTPError as exc:
            assert exc.code == 500
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


def test_env_files_layer_deployment_env_under_the_overlay(
    controller_path, tmp_path
):
    server_mod = load("server")
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / ".env").write_text("AUTH_PROVIDER=disabled\n")
    layered = server_mod._env_files_for_apply(
        {"projectDir": str(project_dir)}, "/state/image-overlays/op.env"
    )
    assert layered == [str(project_dir / ".env"), "/state/image-overlays/op.env"]
    explicit = server_mod._env_files_for_apply(
        {"projectDir": str(project_dir), "envFile": "/custom/dep.env"},
        "/state/image-overlays/op.env",
    )
    assert explicit == ["/custom/dep.env", "/state/image-overlays/op.env"]
    bare_dir = tmp_path / "bare"
    bare_dir.mkdir()
    assert server_mod._env_files_for_apply(
        {"projectDir": str(bare_dir)}, "/state/image-overlays/op.env"
    ) == ["/state/image-overlays/op.env"]
