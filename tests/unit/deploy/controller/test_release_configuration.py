"""Release configuration must not follow an advanced deployment checkout."""

import json
from pathlib import Path

import pytest
from conftest import load


def installation(controller_path, tmp_path):
    server, record = load("server"), load("record")
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / "docker-compose.yaml").write_text(
        "services: {minio: {image: future-store}}"
    )
    (repo / "docker-compose.override.yaml").write_text(
        "services: {api: {ports: ['7000:8000']}}"
    )
    store = record.OperationStore(tmp_path / "state")
    target = {
        "project": "moonmind-test",
        "projectDir": str(repo),
        "composeFiles": ["docker-compose.yaml", "docker-compose.override.yaml"],
        "services": ["api", "minio", "future-worker"],
        "envFile": str(repo / ".env"),
        "operatorUrls": ["http://127.0.0.1:7000"],
    }
    op = store.begin(
        stack="moonmind-test",
        desired_image="example/app:latest",
        source_revision="",
        target=target,
    )
    return server, store, op


class ReleaseRunner:
    def __init__(self, *, fail=False):
        self.commands = []
        self.fail = fail
        self.compose = {
            "services": {
                "api": {"image": "${MOONMIND_IMAGE}"},
                "minio": {"image": "retained-store", "volumes": ["minio-data:/data"]},
            }
        }

    def run(self, args, timeout_seconds):
        self.commands.append(tuple(args))
        if args[:2] == ("docker", "pull"):
            return {"exit": 0, "stdout": "pulled"}
        if args[:3] == ("docker", "image", "inspect"):
            return {
                "exit": 0,
                "stdout": json.dumps(
                    [
                        {
                            "Id": "sha256:installed",
                            "RepoDigests": ["example/app@sha256:selected"],
                        }
                    ]
                ),
            }
        if args[:2] == ("docker", "run"):
            assert "--network" in args and args[args.index("--network") + 1] == "none"
            assert "--mount" not in args and "-v" not in args
            assert "sha256:installed" in args
            return {
                "exit": 1 if self.fail else 0,
                "stdout": json.dumps(self.compose),
                "output": "release configuration unavailable",
            }
        assert args[-2:] == ("config", "--services")
        files = [args[i + 1] for i, arg in enumerate(args) if arg == "-f"]
        assert json.loads(Path(files[0]).read_text()) == self.compose
        assert files[1].endswith("docker-compose.override.yaml")
        return {"exit": 0, "stdout": "api\nminio\ndocker-proxy\n"}


def test_selected_release_owns_base_and_services_preserving_deployment_inputs(
    controller_path, tmp_path
):
    server, store, op = installation(controller_path, tmp_path)
    runner = ReleaseRunner()
    prepared = server.prepare_release_configuration(
        store, op, runner=runner, overlay="image.env"
    )
    target = prepared["target"]
    assert target["services"] == ["api", "minio"]
    assert target["composeFiles"][1:] == op["target"]["composeFiles"][1:]
    for key in ("project", "projectDir", "envFile", "operatorUrls"):
        assert target[key] == op["target"][key]
    assert (
        Path(op["target"]["projectDir"], "docker-compose.yaml")
        .read_text()
        .endswith("future-store}}")
    )
    assert prepared["resolvedImage"] == "example/app@sha256:selected"
    assert (
        store.load(op["operationId"])["releaseConfiguration"]["submittedTarget"]
        == op["target"]
    )
    assert prepared["desired"] == op["desired"]


def test_retry_reuses_resolved_release_after_tag_and_checkout_advance(
    controller_path, tmp_path
):
    server, store, op = installation(controller_path, tmp_path)
    prepared = server.prepare_release_configuration(
        store, op, runner=ReleaseRunner(), overlay="image.env"
    )

    class NoCommands:
        def run(self, *args, **kwargs):
            pytest.fail(
                "An admitted release must survive a retry without reselecting latest"
            )

    assert (
        server.prepare_release_configuration(
            store,
            store.load(op["operationId"]),
            runner=NoCommands(),
            overlay="image.env",
        )
        == prepared
    )


def test_export_failure_preserves_target_without_recreation(controller_path, tmp_path):
    server, store, op = installation(controller_path, tmp_path)
    runner = ReleaseRunner(fail=True)
    with pytest.raises(server.engine.StageError, match="release-configuration"):
        server.prepare_release_configuration(
            store, op, runner=runner, overlay="image.env"
        )
    assert store.load(op["operationId"])["target"] == op["target"]
    assert not any("up" in args or "stop" in args for args in runner.commands)


def test_default_state_path_remains_readable_by_retained_helpers(
    controller_path, tmp_path
):
    server, _, op = installation(controller_path, tmp_path)
    state = Path(op["target"]["projectDir"]) / "deploy/state/controller"
    store = server.record_mod.OperationStore(state)
    op = store.begin(
        stack=op["stack"],
        desired_image=op["desired"]["image"],
        source_revision="",
        target=op["target"],
    )
    prepared = server.prepare_release_configuration(
        store, op, runner=ReleaseRunner(), overlay="image.env"
    )
    assert prepared["target"]["composeFiles"][0].startswith(
        "deploy/state/controller/image-overlays/"
    )
    assert not Path(prepared["target"]["composeFiles"][0]).is_absolute()
    assert (
        server.prepare_release_configuration(
            store, prepared, runner=ReleaseRunner(), overlay="image.env"
        )
        == prepared
    )


def test_explicit_custom_base_keeps_its_authority(controller_path, tmp_path):
    server, store, op = installation(controller_path, tmp_path)
    op["target"]["composeFiles"] = ["site.yaml"]
    runner = ReleaseRunner()
    assert (
        server.prepare_release_configuration(
            store, op, runner=runner, overlay="image.env"
        )
        == op
    )
    assert runner.commands == []


@pytest.mark.parametrize("storage_failure", [False, True])
def test_apply_uses_release_configuration_before_storage_and_recreation(
    controller_path, tmp_path, monkeypatch, storage_failure
):
    server, store, op = installation(controller_path, tmp_path)

    class Runner(ReleaseRunner):
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "output": ""}
            if args[:2] == ("docker", "compose") and args[-2:] != (
                "config",
                "--services",
            ):
                self.commands.append(tuple(args))
                assert "future-worker" not in args
                files = [args[i + 1] for i, arg in enumerate(args) if arg == "-f"]
                assert json.loads(Path(files[0]).read_text()) == self.compose
                return {"exit": 0}
            return super().run(args, timeout_seconds)

    runner = Runner()
    monkeypatch.setattr(server.engine, "subprocess_runner", lambda: runner)
    monkeypatch.setattr(server, "require_routing_floor", lambda *a, **kw: None)
    monkeypatch.setattr(server, "prepare_controller_journals", lambda *a, **kw: None)
    monkeypatch.setattr(
        server, "hand_off_to_requested_controller", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        server,
        "_finish_installed_verification",
        lambda store, operation, **kw: store.load(operation["operationId"]),
    )
    prepared = []

    def storage(store, operation, **kwargs):
        prepared.append(operation)
        assert operation["resolvedImage"] == "example/app@sha256:selected"
        assert not any("up" in args for args in runner.commands)
        assert json.loads(Path(operation["target"]["composeFiles"][0]).read_text())[
            "services"
        ]["minio"]["volumes"] == ["minio-data:/data"]
        if storage_failure:
            raise ValueError("storage transfer unverified")

    monkeypatch.setattr(server, "prepare_controller_storage", storage)
    if storage_failure:
        with pytest.raises(server.engine.ApplyError, match="storage-prepare"):
            server.production_apply(store, op)
        assert not any("up" in args for args in runner.commands)
        assert store.load(op["operationId"])["installed"] is None
    else:
        result = server.production_apply(store, op)
        assert result["installed"]["image"] == "example/app@sha256:selected"
        assert len([args for args in runner.commands if "up" in args]) == 1
    assert len(prepared) == 1
