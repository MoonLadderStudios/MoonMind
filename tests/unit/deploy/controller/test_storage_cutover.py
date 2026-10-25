"""Storage migration remains inside the controller's existing apply ownership."""

import pytest
from conftest import load


def test_storage_helper_is_required_before_recreating_the_selected_store(controller_path, monkeypatch):
    server = load("server")
    events = []
    operation = {"operationId": "storage", "desired": {"image": "candidate"}, "target": {"services": ["api", "minio"]}}

    class Store:
        def load(self, key):
            return operation

        def record_storage_transition(self, key, receipt):
            events.append(receipt)

    def helper(*args, **kwargs):
        assert kwargs["phase"] == "storage-prepare"
        return {"status": "prepared", "verifiedObjects": 3, "sourceRetained": True}

    monkeypatch.setattr(server, "run_omnigent_step", helper)
    receipt = server.prepare_controller_storage(Store(), operation, runner=object(), overlay="image.env")
    assert receipt["verifiedObjects"] == 3
    assert events[0]["receipt"]["sourceRetained"]


def test_storage_helper_failure_refuses_application_recreation(controller_path, monkeypatch):
    server = load("server")
    operation = {"operationId": "storage", "desired": {"image": "candidate"}, "target": {"services": ["minio"]}}

    class Store:
        def load(self, key):
            return operation

    monkeypatch.setattr(server, "run_omnigent_step", lambda *a, **k: {"status": "failed"})
    with pytest.raises(ValueError, match="storage"):
        server.prepare_controller_storage(Store(), operation, runner=object(), overlay="image.env")


def test_rollback_uses_retained_capable_helper(controller_path, tmp_path, monkeypatch):
    import json

    server, record = load("server"), load("record")
    store = record.OperationStore(tmp_path)
    operation = store.begin(stack="test", desired_image="old-app", source_revision="", target={"services": ["minio"]})
    (store.state_dir / "controller-image.json").write_text(json.dumps({"pinned": "new-capable-helper", "verified": True}))
    seen = []

    def helper(*args, **kwargs):
        seen.append(kwargs["helper_image"])
        return {"status": "unsupported" if kwargs["helper_image"] == "old-app" else "prepared"}

    monkeypatch.setattr(server, "run_omnigent_step", helper)
    assert server.prepare_controller_storage(store, operation, runner=object(), overlay="image.env")["status"] == "prepared"
    assert seen == ["old-app", "new-capable-helper"]
