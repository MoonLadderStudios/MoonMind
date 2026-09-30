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


def _verification_runner(engine, ps_state="running"):
    import json as _json

    class _Runner:
        def __init__(self):
            self.commands = []

        def run(self, args, timeout_seconds):
            self.commands.append(tuple(args))
            if "ps" in args:
                return {
                    "exit": 0,
                    "output": _json.dumps({"Service": "api", "State": ps_state}),
                }
            return {"exit": 0, "output": "ok"}

    return _Runner()


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


def test_production_apply_records_omnigent_gap_without_migrator(
    controller_path, tmp_path, monkeypatch
):
    record = load("record")
    server_mod = load("server")
    (tmp_path / ".env").write_text("OMNIGENT_IMAGE_TAG=v1\n", encoding="utf-8")
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
    result = server_mod.production_apply(store, op)
    assert result["status"] == "partially_verified", result
    omnigent = [
        check
        for check in store.load(op["operationId"])["verification"]
        if check["name"] == "omnigent-migration"
    ]
    assert omnigent and omnigent[0]["status"] == "unavailable"


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
                    "legacy application-owned writer may still be active; "
                    "stop or reconcile it before the controller takes over "
                    "(REQ-07)"
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
        status, again = _post_operation(port, {**body, "operationId": "ui-2"})
        assert (status, again["operationId"]) == (202, "ui-1")
        assert derived == ["moonmind"]
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
