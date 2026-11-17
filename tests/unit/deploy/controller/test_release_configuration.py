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
        self.digests = ["example/app@sha256:selected"]
        self.compose = {
            "services": {
                "api": {"image": "${MOONMIND_IMAGE}"},
                "minio": {"image": "retained-store", "volumes": ["minio-data:/data"]},
            }
        }
        # Existing project containers as (project, service, name, running).
        self.containers = []

    def run(self, args, timeout_seconds):
        self.commands.append(tuple(args))
        if args[:2] == ("docker", "ps"):
            # Docker lists only running containers unless asked for --all.
            filters = [args[i + 1] for i, arg in enumerate(args) if arg == "--filter"]
            rows = [
                json.dumps({"service": service, "name": name})
                for project, service, name, running in self.containers
                if (running or "--all" in args)
                and f"label=com.docker.compose.project={project}" in filters
            ]
            return {"exit": 0, "stdout": "\n".join(rows)}
        if args[:2] == ("docker", "pull"):
            return {"exit": 0, "stdout": "pulled"}
        if args[:3] == ("docker", "image", "inspect"):
            return {
                "exit": 0,
                "stdout": json.dumps(
                    [
                        {
                            "Id": "sha256:installed",
                            "RepoDigests": self.digests,
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

    class NoCommands(ReleaseRunner):
        def run(self, args, *rest, **kwargs):
            # Only the retired-host gate's read-only container listing runs.
            if tuple(args[:2]) != ("docker", "ps"):
                pytest.fail(
                    "An admitted release must survive a retry without reselecting latest"
                )
            return super().run(args, *rest, **kwargs)

    assert (
        server.prepare_release_configuration(
            store,
            store.load(op["operationId"]),
            runner=NoCommands(),
            overlay="image.env",
        )
        == prepared
    )


def test_image_aliases_do_not_change_the_requested_repository(
    controller_path, tmp_path
):
    server, store, op = installation(controller_path, tmp_path)
    runner = ReleaseRunner()
    runner.digests.insert(0, "another-registry/foreign-app@sha256:other")
    prepared = server.prepare_release_configuration(
        store, op, runner=runner, overlay="image.env"
    )
    assert prepared["resolvedImage"] == "example/app@sha256:selected"


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


def refused_release(server, store, op, runner):
    with pytest.raises(server.engine.StageError, match="release-configuration") as info:
        server.prepare_release_configuration(
            store, op, runner=runner, overlay="image.env"
        )
    # Refusal precedes any container change and leaves the update retryable.
    assert not any(
        word in args for args in runner.commands for word in ("up", "stop", "rm")
    )
    assert "releaseConfiguration" not in store.load(op["operationId"])
    return info.value.output


def assert_static_host_migration_guidance(message):
    assert "Static-connected Omnigent hosts were removed" in message
    assert "Codex and Claude Code OAuth run on on-demand hosts" in message
    assert "Let active static-connected runs finish (or cancel them)" in message
    assert "retry the update" in message
    assert "codex_auth_volume" in message and "claude_auth_volume" in message
    assert "nothing is deleted" in message


def test_retired_profile_in_deployment_env_is_refused_with_migration_guidance(
    controller_path, tmp_path, monkeypatch
):
    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    server, store, op = installation(controller_path, tmp_path)
    env_file = Path(op["target"]["envFile"])
    env_file.write_text("COMPOSE_PROFILES=temporal-ui, omnigent-host-codex\n")
    message = refused_release(server, store, op, ReleaseRunner())
    assert (
        f"COMPOSE_PROFILES in {env_file} enables retired profile "
        "omnigent-host-codex" in message
    )
    assert f"remove omnigent-host-codex from COMPOSE_PROFILES in {env_file}" in message
    assert "temporal-ui" not in message
    assert_static_host_migration_guidance(message)


def test_last_deployment_env_file_owns_compose_profiles(
    controller_path, tmp_path, monkeypatch
):
    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    server, store, op = installation(controller_path, tmp_path)
    repo = Path(op["target"]["projectDir"])
    Path(op["target"]["envFile"]).write_text("COMPOSE_PROFILES=\n")
    release_env = repo / "deploy" / "state" / ".env.deploy"
    release_env.parent.mkdir(parents=True)
    release_env.write_text("COMPOSE_PROFILES=omnigent-host-claude,omnigent-host\n")
    message = refused_release(server, store, op, ReleaseRunner())
    assert (
        "remove omnigent-host-claude, omnigent-host from COMPOSE_PROFILES "
        f"in {release_env}" in message
    )


def test_controller_environment_profiles_take_compose_precedence(
    controller_path, tmp_path, monkeypatch
):
    server, store, op = installation(controller_path, tmp_path)
    Path(op["target"]["envFile"]).write_text("COMPOSE_PROFILES=omnigent-host-codex\n")
    monkeypatch.setenv("COMPOSE_PROFILES", "")
    server.prepare_release_configuration(
        store, op, runner=ReleaseRunner(), overlay="image.env"
    )

    (tmp_path / "process").mkdir()
    server, store, op = installation(controller_path, tmp_path / "process")
    monkeypatch.setenv("COMPOSE_PROFILES", "omnigent-host-claude")
    message = refused_release(server, store, op, ReleaseRunner())
    assert (
        "remove omnigent-host-claude from COMPOSE_PROFILES in the controller "
        "environment" in message
    )
    assert_static_host_migration_guidance(message)


@pytest.mark.parametrize(
    "profiles", ["", "temporal-ui", " temporal-tools , site-extra ,"]
)
def test_default_and_unretired_profiles_are_admitted(
    controller_path, tmp_path, monkeypatch, profiles
):
    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    server, store, op = installation(controller_path, tmp_path)
    Path(op["target"]["envFile"]).write_text(f"COMPOSE_PROFILES={profiles}\n")
    runner = ReleaseRunner()
    prepared = server.prepare_release_configuration(
        store, op, runner=runner, overlay="image.env"
    )
    assert prepared["target"]["services"] == ["api", "minio"]


def test_release_still_providing_a_profile_keeps_it(
    controller_path, tmp_path, monkeypatch
):
    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    server, store, op = installation(controller_path, tmp_path)
    Path(op["target"]["envFile"]).write_text("COMPOSE_PROFILES=omnigent-host-codex\n")
    runner = ReleaseRunner()
    runner.compose["services"]["omnigent-host-codex"] = {
        "image": "host",
        "profiles": ["omnigent-host-codex"],
    }
    runner.compose["services"]["omnigent-host-codex-init"] = {
        "image": "host",
        "profiles": ["omnigent-host-codex"],
    }
    runner.containers.append(
        ("moonmind-test", "omnigent-host-codex", "moonmind-test-codex-1", True)
    )
    server.prepare_release_configuration(store, op, runner=runner, overlay="image.env")


def test_running_retired_service_is_refused_until_drained(
    controller_path, tmp_path, monkeypatch
):
    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    server, store, op = installation(controller_path, tmp_path)
    runner = ReleaseRunner()
    runner.containers.append(
        ("moonmind-test", "omnigent-host-claude", "moonmind-test-claude-1", True)
    )
    message = refused_release(server, store, op, runner)
    assert (
        "Retired service omnigent-host-claude is still running "
        "(container moonmind-test-claude-1)" in message
    )
    assert "stop moonmind-test-claude-1 (docker stop moonmind-test-claude-1)" in message
    assert "COMPOSE_PROFILES" not in message
    assert_static_host_migration_guidance(message)


def test_bound_configuration_from_an_earlier_controller_is_still_gated(
    controller_path, tmp_path, monkeypatch
):
    # A controller predating the gate can bind the release configuration and
    # hand off; the release's controller checks again before any apply.
    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    server, store, op = installation(controller_path, tmp_path)
    server.prepare_release_configuration(
        store, op, runner=ReleaseRunner(), overlay="image.env"
    )
    bound = store.load(op["operationId"])
    Path(bound["target"]["envFile"]).write_text("COMPOSE_PROFILES=omnigent-host-codex\n")
    runner = ReleaseRunner()
    runner.containers.append(
        ("moonmind-test", "omnigent-host-codex", "moonmind-test-codex-1", True)
    )

    with pytest.raises(server.engine.StageError, match="release-configuration") as info:
        server.prepare_release_configuration(
            store, bound, runner=runner, overlay="image.env"
        )

    message = info.value.output
    assert "enables retired profile omnigent-host-codex" in message
    assert "moonmind-test-codex-1" in message
    assert_static_host_migration_guidance(message)
    assert not any(
        word in args for args in runner.commands for word in ("up", "stop", "rm")
    )


@pytest.mark.parametrize(
    "container",
    [
        ("moonmind-test", "omnigent-host-codex", "moonmind-test-codex-1", False),
        ("moonmind-test", "omnigent-host-codex-init", "moonmind-test-init-1", False),
        ("other-project", "omnigent-host-codex", "other-project-codex-1", True),
    ],
)
def test_stopped_or_foreign_retired_containers_do_not_block(
    controller_path, tmp_path, monkeypatch, container
):
    monkeypatch.delenv("COMPOSE_PROFILES", raising=False)
    server, store, op = installation(controller_path, tmp_path)
    runner = ReleaseRunner()
    runner.containers.append(container)
    prepared = server.prepare_release_configuration(
        store, op, runner=runner, overlay="image.env"
    )
    assert prepared["target"]["services"] == ["api", "minio"]


@pytest.mark.parametrize("storage_failure", [False, True])
def test_apply_uses_release_configuration_before_storage_and_recreation(
    controller_path, tmp_path, monkeypatch, storage_failure
):
    server, store, op = installation(controller_path, tmp_path)

    class Runner(ReleaseRunner):
        def run(self, args, timeout_seconds):
            if args[-3:] == ("config", "--format", "json"):
                return {"exit": 0, "output": json.dumps(self.compose)}
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


def test_release_staging_failures_exhaust_as_retryable_recorded_operations(
    controller_path, tmp_path, monkeypatch
):
    server, store, op = installation(controller_path, tmp_path)
    monkeypatch.setattr(
        server.engine, "subprocess_runner", lambda: ReleaseRunner(fail=True)
    )
    monkeypatch.setattr(
        server, "require_previous_oneoffs_exited", lambda *a, **kw: None
    )
    result = server._apply_recording_failure(
        store,
        op["operationId"],
        lambda current: server.production_apply(store, current),
    )
    assert result["status"] == "failed"
    assert result["autoAttemptsExhausted"]
    assert len(result["attempts"]) == server.record_mod.MAX_AUTO_ATTEMPTS
    assert "release configuration unavailable" in result["errorSummary"].lower()
    assert store.begin_retry(op["operationId"])["status"] == "pending"


def test_custom_controller_state_is_mounted_into_the_release_helper(
    controller_path, tmp_path, monkeypatch
):
    server, store, op = installation(controller_path, tmp_path)
    op = server.prepare_release_configuration(
        store, op, runner=ReleaseRunner(), overlay="image.env"
    )
    monkeypatch.setenv("MOONMIND_CONTROLLER_MANAGED", "1")
    monkeypatch.setenv("HOSTNAME", "controller-container")

    class Runner:
        def run(self, args, timeout_seconds):
            if args[:2] == ("docker", "ps"):
                return {"exit": 0, "stdout": ""}
            if args[:2] == ("docker", "inspect"):
                assert "controller-container" in args
                return {
                    "exit": 0,
                    "stdout": json.dumps(
                        [
                            {
                                "Type": "bind",
                                "Destination": str(store.state_dir),
                                "Source": "/host/custom/controller-state",
                            }
                        ]
                    ),
                }
            files = [args[i + 1] for i, arg in enumerate(args) if arg == "-f"]
            helper = json.loads(Path(files[-1]).read_text())["services"][
                server.LEGACY_CONTROL_SERVICE
            ]
            mounts = [
                v
                for v in helper["volumes"]
                if isinstance(v, dict)
                and v["target"] == "/workspace/deployment_state/controller"
            ]
            assert len(mounts) == 1
            assert mounts[0]["source"] == "/host/custom/controller-state"
            assert mounts[0]["bind"]["create_host_path"] is False
            assert (
                helper["environment"]["MOONMIND_CONTROLLER_STATE_DIR"]
                == mounts[0]["target"]
            )
            return {
                "exit": 0,
                "output": 'MOONMIND_OMNIGENT_RESULT={"status":"skipped"}',
            }

    assert (
        server.run_omnigent_step(
            store, op, runner=Runner(), overlay="image.env", phase="select"
        )["status"]
        == "skipped"
    )
