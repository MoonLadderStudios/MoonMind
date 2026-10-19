"""Test the upgrade adapter against the real controller store and stack lock."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "deploy/controller"))
import server as controller  # noqa: E402


IMAGE = "sha256:" + "a" * 64


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "moonmind-upgrade-4356.fixture"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: ../fixture\n")
    (repo / ".first-run-journey-project").write_text("moonmind-test-upgrade\n")
    (repo / "docker-compose.yaml").write_text("services: {}\n")
    return repo


def adapter():
    spec = importlib.util.spec_from_file_location(
        "journey_cutover", ROOT / "tools/single_user_journey_cutover.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("status", [None, "pending", "attention", "completed"])
def test_requires_passed_check_and_persisted_completion(checkout, monkeypatch, status):
    compose = checkout / "docker-compose.yaml"
    compose.write_text("services: {}\n")
    isolation = checkout / "isolation.json"
    isolation.write_text("{}")
    seen = []

    def converge(store, operation, *, runner, overlay):
        assert controller.lock_mod.StackLock(
            store.state_dir, operation["stack"]
        ).probe()
        assert Path(overlay).read_text() == f"MOONMIND_IMAGE={IMAGE}\n"
        assert operation["target"]["composeFiles"] == [str(compose), str(isolation)]
        seen.append(operation["operationId"])
        if status:
            store.record_routing_cutover(operation["operationId"], {"status": status})
        return {"status": "passed"}

    monkeypatch.setattr(controller, "converge_routing_cutover", converge)
    kwargs = dict(
        repo=checkout,
        project="moonmind-test-upgrade",
        image=IMAGE,
        revision="candidate-sha",
        compose_files=[str(compose), str(isolation)],
        controller=controller,
    )
    result = adapter().run_cutover(**kwargs)
    assert result["passed"] is (status == "completed")
    again = adapter().run_cutover(**kwargs)
    assert seen[0] == seen[1]
    assert again["passed"] is result["passed"]


def test_none_check_fails_even_with_completed_receipt(checkout, monkeypatch):
    def converge(store, operation, **kwargs):
        store.record_routing_cutover(operation["operationId"], {"status": "completed"})
        return None

    monkeypatch.setattr(controller, "converge_routing_cutover", converge)
    result = adapter().run_cutover(
        repo=checkout,
        project="moonmind-test-upgrade",
        image=IMAGE,
        revision="sha",
        compose_files=[str(checkout / "docker-compose.yaml")],
        controller=controller,
    )
    assert not result["passed"]


def test_refuses_non_test_project_before_controller_write(checkout):
    with pytest.raises(ValueError, match="project"):
        adapter().run_cutover(
            repo=checkout,
            project="moonmind",
            image=IMAGE,
            revision="sha",
            compose_files=[str(checkout / "docker-compose.yaml")],
            controller=controller,
        )
    assert not (checkout / "deploy/state/controller").exists()


def test_failed_check_rejects_completed_receipt(checkout, monkeypatch):
    def converge(store, operation, **kwargs):
        store.record_routing_cutover(operation["operationId"], {"status": "completed"})
        return {"status": "failed"}

    monkeypatch.setattr(controller, "converge_routing_cutover", converge)
    assert not adapter().run_cutover(
        repo=checkout,
        project="moonmind-test-upgrade",
        image=IMAGE,
        revision="sha",
        compose_files=[str(checkout / "docker-compose.yaml")],
        controller=controller,
    )["passed"]


@pytest.mark.parametrize("fault", ["marker", "checkout", "compose", "image"])
def test_rejects_unowned_or_unresolved_target(checkout, fault, monkeypatch):
    monkeypatch.setattr(controller, "converge_routing_cutover", lambda *a, **k: None)
    image = IMAGE
    files = [str(checkout / "docker-compose.yaml")]
    if fault == "marker":
        (checkout / ".first-run-journey-project").write_text("moonmind-test-other")
    elif fault == "checkout":
        (checkout / ".git").unlink()
    elif fault == "compose":
        files = [str(checkout / "missing.yaml")]
    else:
        image = "candidate:latest"
    with pytest.raises(ValueError):
        adapter().run_cutover(
            repo=checkout,
            project="moonmind-test-upgrade",
            image=image,
            revision="sha",
            compose_files=files,
            controller=controller,
        )
    assert not (checkout / "deploy/state/controller").exists()
