"""Compose apply semantics owned by the controller (REQ-03).

Semantic default: stage every image with `pull --policy always`, then apply
with `up -d --pull never --no-build --remove-orphans --wait` under bounded
timeouts. Full `down` and force-recreate are explicit repair operations only,
never automatic escalation. No volume/image pruning.
"""
from conftest import load

import pytest


class FakeRunner:
    def __init__(self, failures=None):
        self.commands = []
        self.failures = failures or {}

    def run(self, args, timeout_seconds):
        self.commands.append((tuple(args), timeout_seconds))
        for key, failure in self.failures.items():
            if key in args:
                raise failure
        return {"exit": 0, "output": "ok"}


def test_apply_stages_all_images_before_any_up(controller_path):
    engine = load("engine")
    runner = FakeRunner()
    engine.apply(
        runner,
        project="moonmind",
        project_dir="/srv/moonmind",
        compose_files=("docker-compose.yaml",),
        services=("api", "worker"),
        images=("img:api", "img:worker"),
    )
    def kind(cmd):
        if "pull" in cmd:
            return "pull"
        if "up" in cmd:
            return "up"
        return "?"

    kinds = [kind(cmd[0]) for cmd in runner.commands]
    assert kinds[0] == "pull"
    first_up = kinds.index("up")
    assert kinds[:first_up].count("pull") == 1
    assert all(k == "pull" for k in kinds[:first_up])


def test_apply_uses_changed_service_semantic_default(controller_path):
    engine = load("engine")
    runner = FakeRunner()
    engine.apply(
        runner,
        project="moonmind",
        project_dir="/srv/moonmind",
        compose_files=("docker-compose.yaml",),
        services=("api",),
        images=("img:api",),
    )
    pull = next(cmd for cmd, _ in runner.commands if "pull" in cmd)
    up = next(cmd for cmd, _ in runner.commands if "up" in cmd)
    assert "--policy" in pull and "always" in pull
    for flag in ("-d", "--pull", "never", "--no-build", "--remove-orphans", "--wait"):
        assert flag in up
    assert "--build" not in up
    assert "--force-recreate" not in up


def test_commands_run_under_bounded_timeouts(controller_path):
    engine = load("engine")
    runner = FakeRunner()
    engine.apply(
        runner,
        project="moonmind",
        project_dir="/srv/moonmind",
        compose_files=("docker-compose.yaml",),
        services=("api",),
        images=("img:api",),
    )
    for _, timeout_seconds in runner.commands:
        assert 0 < timeout_seconds <= engine.MAX_COMMAND_TIMEOUT_SECONDS


def test_pull_failure_leaves_containers_untouched(controller_path):
    engine = load("engine")
    runner = FakeRunner(failures={"pull": engine.CommandError("pull", 1, "denied")})
    with pytest.raises(engine.StageError):
        engine.apply(
            runner,
            project="moonmind",
            project_dir="/srv/moonmind",
            compose_files=("docker-compose.yaml",),
            services=("api",),
            images=("img:api",),
        )
    assert all("up" not in cmd[0] for cmd, _ in runner.commands)


def test_apply_failure_surfaces_exit_status_and_redacted_tail(controller_path):
    engine = load("engine")
    runner = FakeRunner(
        failures={"up": engine.CommandError("up", 14, "token=s3cret boom" * 200)}
    )
    with pytest.raises(engine.ApplyError) as excinfo:
        engine.apply(
            runner,
            project="moonmind",
            project_dir="/srv/moonmind",
            compose_files=("docker-compose.yaml",),
            services=("api",),
            images=("img:api",),
        )
    assert excinfo.value.exit_status == 14
    assert "s3cret" not in str(excinfo.value)


def test_destructive_commands_are_never_generated(controller_path):
    engine = load("engine")
    with pytest.raises(ValueError):
        engine.assert_safe_command(("docker", "compose", "down"))
    with pytest.raises(ValueError):
        engine.assert_safe_command(
            ("docker", "compose", "up", "-d", "--force-recreate")
        )
    with pytest.raises(ValueError):
        engine.assert_safe_command(("docker", "volume", "prune", "-f"))
    with pytest.raises(ValueError):
        engine.assert_safe_command(("docker", "image", "prune", "-f"))


def test_pre_apply_treats_old_health_as_diagnostic_not_admission(controller_path):
    engine = load("engine")
    report = engine.pre_apply_checks(
        config_valid=True,
        storage_ok=True,
        access_preserved=True,
        old_service_health={"api": "unhealthy", "worker": "unknown"},
    )
    assert report["admitted"] is True
    assert report["oldHealth"]["status"] == "diagnostic"


def test_pre_apply_blocks_on_missing_storage_or_access(controller_path):
    engine = load("engine")
    report = engine.pre_apply_checks(
        config_valid=True,
        storage_ok=False,
        access_preserved=True,
        old_service_health={},
    )
    assert report["admitted"] is False


def test_refusing_to_replace_the_controller_itself(controller_path):
    engine = load("engine")
    with pytest.raises(engine.SelfReplacementError):
        engine.apply(
            runner=FakeRunner(),
            project="moonmind-controller",
            project_dir="/srv/controller",
            compose_files=("controller-compose.yaml",),
            services=("controller",),
            images=("img:controller",),
            own_service="controller",
        )


def test_compose_base_layers_env_files_in_order(controller_path):
    engine = load("engine")
    base = engine.compose_base(
        project="moonmind",
        project_dir="/srv/moonmind",
        compose_files=("docker-compose.yaml",),
        env_files=("/srv/moonmind/.env", "/state/image-overlays/op.env"),
    )
    env_flags = [base[i + 1] for i, part in enumerate(base) if part == "--env-file"]
    assert env_flags == ["/srv/moonmind/.env", "/state/image-overlays/op.env"]


def test_compose_base_keeps_single_env_file_compatibility(controller_path):
    engine = load("engine")
    base = engine.compose_base(
        project="moonmind",
        project_dir="/srv/moonmind",
        compose_files=("docker-compose.yaml",),
        env_file="/state/image-overlays/op.env",
    )
    assert "--env-file" in base
    assert "/state/image-overlays/op.env" in base


def test_apply_passes_layered_env_files_to_compose(controller_path):
    engine = load("engine")
    runner = FakeRunner()
    engine.apply(
        runner,
        project="moonmind",
        project_dir="/srv/moonmind",
        compose_files=("docker-compose.yaml",),
        services=("api",),
        images=("img:api",),
        env_files=("/srv/moonmind/.env", "/state/image-overlays/op.env"),
    )
    for cmd, _ in runner.commands:
        env_flags = [cmd[i + 1] for i, part in enumerate(cmd) if part == "--env-file"]
        assert env_flags == ["/srv/moonmind/.env", "/state/image-overlays/op.env"]
