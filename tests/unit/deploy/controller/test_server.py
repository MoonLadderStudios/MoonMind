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
