"""Storage migration remains inside the controller's existing apply ownership."""

import json

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
        return {"status": "prepared"}

    monkeypatch.setattr(server, "run_omnigent_step", helper)
    assert server.prepare_controller_storage(store, operation, runner=object(), overlay="image.env")["status"] == "prepared"
    assert seen == ["new-capable-helper"]


@pytest.mark.parametrize(
    "phase", ["storage-prepare", "storage-observe", "journal-prepare"]
)
def test_storage_copy_has_a_separate_bounded_command_budget(
    controller_path, tmp_path, phase
):
    server, record = load("server"), load("record")
    store = record.OperationStore(tmp_path)
    operation = store.begin(
        stack="moonmind-test",
        desired_image="candidate",
        source_revision="",
        target={
            "project": "moonmind-test",
            "projectDir": str(tmp_path),
            "services": ["minio"],
        },
    )

    class Runner:
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": ""}
            request = json.loads(args[-1])
            assert "storage-transfer-budget" in request["controllerCapabilities"]
            assert timeout_seconds == (
                4 * 60 * 60 + 900 if phase == "storage-prepare" else 900
            )
            return {
                "exit": 0,
                "output": 'MOONMIND_OMNIGENT_RESULT={"status":"prepared"}',
            }

    server.run_omnigent_step(
        store, operation, runner=Runner(), overlay="overlay", phase=phase
    )
