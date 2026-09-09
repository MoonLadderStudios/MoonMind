"""A planner without daemon authority must use the Docker Backend contract."""

import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base
from moonmind.schemas.temporal_activity_models import PlanGenerateInput
from moonmind.workflows.temporal import worker_runtime
from moonmind.workflows.temporal.activity_runtime import TemporalPlanActivities
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

ROOT = Path(__file__).resolve().parents[4]
REPLAY = json.loads(
    (
        ROOT
        / "tests/integration/reliability/replays/planner-docker-owner/manifest.json"
    ).read_text(encoding="utf-8")
)


@pytest.fixture
def isolated_planner(monkeypatch):
    for name in (
        "DOCKER_HOST",
        "SYSTEM_DOCKER_HOST",
        "MOONMIND_CONTAINER_BACKEND_ENABLED",
        "MOONMIND_CONTAINER_BACKEND_KIND",
        "MOONMIND_CONTAINER_BACKEND_RAW_CLI_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    original_exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda path: (
            False if str(path) == "/var/run/docker.sock" else original_exists(path)
        ),
    )
    original_which = worker_runtime.shutil.which
    monkeypatch.setattr(
        worker_runtime.shutil,
        "which",
        lambda command: "/usr/bin/gh" if command == "gh" else original_which(command),
    )


@pytest.mark.parametrize("enabled", [None, "", "true"])
def test_default_backend_does_not_require_planner_daemon(
    isolated_planner, monkeypatch, enabled
):
    if enabled is not None:
        monkeypatch.setenv("MOONMIND_CONTAINER_BACKEND_ENABLED", enabled)
    assert (
        worker_runtime._required_capability_blockers(
            parameters={"requiredCapabilities": ["docker"]}, task_payload={}
        )
        == []
    )


@pytest.mark.parametrize("direct_daemon", [False, True])
def test_disabled_backend_cannot_be_bypassed_by_direct_daemon(
    isolated_planner, monkeypatch, direct_daemon
):
    monkeypatch.setenv("MOONMIND_CONTAINER_BACKEND_ENABLED", "false")
    if direct_daemon:
        monkeypatch.setenv("DOCKER_HOST", "tcp://unrelated-daemon:2375")
    blockers = worker_runtime._required_capability_blockers(
        parameters={"requiredCapabilities": ["docker"]}, task_payload={}
    )
    assert len(blockers) == 1
    assert blockers[0]["check"] == "container_backend"
    assert blockers[0]["capability"] == "docker"
    assert "MOONMIND_CONTAINER_BACKEND_ENABLED" in blockers[0]["remediation"]


def test_unsupported_backend_has_structured_blocker(isolated_planner, monkeypatch):
    monkeypatch.setenv("MOONMIND_CONTAINER_BACKEND_KIND", "unknown-provider")
    blockers = worker_runtime._required_capability_blockers(
        parameters={"requiredCapabilities": ["docker"]}, task_payload={}
    )
    assert len(blockers) == 1
    assert blockers[0]["check"] == "container_backend"
    assert blockers[0]["source"] == "requiredCapabilities"


def test_unrelated_plan_does_not_resolve_docker_configuration(
    isolated_planner, monkeypatch
):
    monkeypatch.setenv("MOONMIND_CONTAINER_BACKEND_KIND", "unknown-provider")
    assert (
        worker_runtime._required_capability_blockers(parameters={}, task_payload={})
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "typed_request", [False, True], ids=["historical-dict", "typed-request"]
)
@pytest.mark.parametrize("runtime", ["omnigent", "codex_cli", "claude"])
async def test_plan_generate_replays_without_planner_docker_authority(
    isolated_planner, tmp_path, typed_request, runtime
):
    request = json.loads(json.dumps(REPLAY["request"]))
    request["parameters"]["targetRuntime"] = runtime
    request["parameters"]["workflow"]["runtime"]["mode"] = runtime
    parameters = request["parameters"]
    principal = request["principal"]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/artifacts.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            activities = TemporalPlanActivities(
                artifact_service=service,
                planner=worker_runtime._build_runtime_planner(),
            )
            # Exercise the decorated worker entrypoint with both the stored
            # request shape and the current typed activity input. The plan
            # artifact, not an assistant assertion, is the terminal evidence.
            result = await activities.plan_generate(
                PlanGenerateInput.model_validate(request) if typed_request else request
            )
            _, content = await service.read(
                artifact_id=result.plan_ref.artifact_id, principal=principal
            )
            plan = json.loads(content)
            node = plan["nodes"][0]
            assert node["tool"]["type"] == "agent_runtime"
            planned_runtime = node["inputs"]["runtime"]
            assert planned_runtime["mode"] == runtime
            assert planned_runtime["model"] == parameters["model"]
            assert planned_runtime["effort"] == parameters["effort"]
            assert planned_runtime["profileId"] == parameters["profileId"]
    finally:
        await engine.dispose()
