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


def _threaded_server(server_mod, app):
    httpd = make_server(
        "127.0.0.1", 0, app, server_class=server_mod.ThreadingWSGIServer
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _get_json(port, path):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        method="GET",
        headers={"Authorization": "Bearer test-secret"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def test_duplicate_submission_reattaches_to_the_running_operation(
    controller_path, tmp_path
):
    """Host and UI submitting the same target observe one mutation owner."""
    server_mod = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")
    started = threading.Event()
    release = threading.Event()
    applied = []

    def slow_applier(operation):
        applied.append(operation["operationId"])
        store.mark_stage(operation["operationId"], stage="applying")
        started.set()
        assert release.wait(timeout=10)
        store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    app = server_mod.build_app(
        store=store, secret="test-secret", applier=slow_applier
    )
    httpd, thread = _threaded_server(server_mod, app)
    port = httpd.server_address[1]
    body = {"stack": "moonmind", "desiredImage": "img:1", "sourceRevision": "r"}
    first = {}
    submitter = threading.Thread(
        target=lambda: first.update(zip(("status", "body"), _post_operation(port, body)))
    )
    try:
        submitter.start()
        assert started.wait(timeout=10)
        status, duplicate = _post_operation(port, body)
        assert status == 202, duplicate
        assert duplicate["status"] == "applying"
        changed_status, changed = _post_operation(
            port, {**body, "desiredImage": "img:2"}
        )
        # A changed target is new intent: refused while another writer runs,
        # and no competing record is created.
        assert changed_status == 409, changed
        release.set()
        submitter.join(timeout=10)
        assert first["status"] == 202
        assert first["body"]["operationId"] == duplicate["operationId"]
        assert applied == [duplicate["operationId"]]
        assert len(store.list_terminal(stack="moonmind")) == 1
        assert store.list_open(stack="moonmind") == []
    finally:
        release.set()
        httpd.shutdown()
        thread.join(timeout=10)


def test_status_and_list_reads_are_served_while_an_apply_runs(
    controller_path, tmp_path
):
    server_mod = load("server")
    record = load("record")
    store = record.OperationStore(tmp_path / "state")
    older = store.begin(stack="moonmind", desired_image="img:0", source_revision="")
    store.confirm_installed(older["operationId"], image="img:0")
    store.begin(stack="other", desired_image="img:x", source_revision="")
    started = threading.Event()
    release = threading.Event()

    def slow_applier(operation):
        store.mark_stage(operation["operationId"], stage="applying")
        store.record_attempt_error(
            operation["operationId"], error="pull failed password=hunter2"
        )
        started.set()
        assert release.wait(timeout=10)
        store.confirm_installed(
            operation["operationId"], image=operation["desired"]["image"]
        )

    app = server_mod.build_app(
        store=store, secret="test-secret", applier=slow_applier
    )
    httpd, thread = _threaded_server(server_mod, app)
    port = httpd.server_address[1]
    submitter = threading.Thread(
        target=_post_operation,
        args=(port, {"stack": "moonmind", "desiredImage": "img:1"}),
    )
    try:
        submitter.start()
        assert started.wait(timeout=10)
        status, listed = _get_json(port, "/v1/operations?stack=moonmind&limit=5")
        assert status == 200, listed
        operations = listed["operations"]
        assert [op["desired"]["image"] for op in operations] == ["img:1", "img:0"]
        assert operations[0]["status"] == "applying"
        assert "hunter2" not in json.dumps(listed)
        status, _ = _get_json(port, "/v1/operations?stack=../escape")
        assert status == 400
    finally:
        release.set()
        submitter.join(timeout=10)
        httpd.shutdown()
        thread.join(timeout=10)


def _unix_get(path, request_path, secret=None):
    import http.client
    import socket

    connection = http.client.HTTPConnection("localhost", timeout=10)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(path))
    connection.sock = sock
    headers = {"Authorization": f"Bearer {secret}"} if secret is not None else {}
    try:
        connection.request("GET", request_path, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode() or "{}")
    finally:
        connection.close()


def test_state_dir_socket_serves_the_same_authenticated_endpoint(
    controller_path, tmp_path
):
    """The API container reaches the controller through its mounted state.

    The controller publishes TCP only on host loopback, which a container
    cannot reach; the socket in the controller's own state directory is
    visible wherever the deployment state is mounted and survives
    target-project shutdown. The bearer secret still guards it.
    """
    import os
    import socket
    import stat

    server_mod = load("server")
    record = load("record")
    state = tmp_path / "state"
    store = record.OperationStore(state)
    store.begin(stack="moonmind", desired_image="img:0", source_revision="")
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(state / server_mod.SOCKET_NAME))
    stale.close()  # a crashed controller leaves its socket file behind

    app = server_mod.build_app(store=store, secret="test-secret", applier=lambda op: None)
    httpd = server_mod.make_unix_server(state, app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        socket_path = state / server_mod.SOCKET_NAME
        assert stat.S_IMODE(os.stat(socket_path).st_mode) == 0o666
        status, _ = _unix_get(socket_path, "/v1/operations?stack=moonmind")
        assert status == 401
        status, listed = _unix_get(
            socket_path, "/v1/operations?stack=moonmind", secret="test-secret"
        )
        assert status == 200, listed
        assert [op["desired"]["image"] for op in listed["operations"]] == ["img:0"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


def test_state_dir_socket_refuses_to_steal_a_live_controller(controller_path, tmp_path):
    import socket

    server_mod = load("server")
    record = load("record")
    state = tmp_path / "state"
    store = record.OperationStore(state)
    state.mkdir(parents=True, exist_ok=True)
    app = server_mod.build_app(store=store, secret="test-secret", applier=lambda op: None)
    first = server_mod.make_unix_server(state, app)
    try:
        try:
            server_mod.make_unix_server(state, app)
        except OSError:
            pass
        else:
            raise AssertionError("a second listener replaced a live controller socket")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.connect(str(state / server_mod.SOCKET_NAME))
        probe.close()
    finally:
        first.server_close()
