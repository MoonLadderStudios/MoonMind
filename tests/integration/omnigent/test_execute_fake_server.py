"""MM-1059 fake Omnigent server coverage for streaming-gateway execution."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
from moonmind.omnigent.execute import run_omnigent_execution
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from tests.helpers.omnigent_conformance import (
    FakeOmnigentServer,
    start_fake_omnigent_server,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest_asyncio.fixture
async def fake_omnigent_server(request: pytest.FixtureRequest):
    server = FakeOmnigentServer(supports_diff=bool(request.param))
    running = await start_fake_omnigent_server(server)
    try:
        yield server, running.base_url
    finally:
        await running.runner.cleanup()


@pytest.mark.parametrize("fake_omnigent_server", [True, False], indirect=True)
async def test_omnigent_execute_harvests_resources_with_fake_server(
    fake_omnigent_server,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    server, server_url = fake_omnigent_server
    monkeypatch.setenv("OMNIGENT_ENABLED", "1")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", server_url)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            workspaceSpec={
                "repository": "https://github.com/org/repo",
                "branch": "main",
            },
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex"},
                    "session": {"hostType": "managed"},
                    "prompt": {"text": "Implement MM-1059 fake-server scenario"},
                }
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    assert result.failure_class is None
    assert result.summary == "fake Omnigent completed"
    assert result.metadata["workspaceFilesIndexRef"].startswith("artifact://omnigent/")
    assert result.metadata["sessionFilesIndexRef"].startswith("artifact://omnigent/")
    assert result.metadata["githubPrUrl"] == "https://github.example/org/repo/pull/42"
    assert len(server.session_ids) == 1
    assert len(server.events) == 1
    assert server.create_payloads[0]["agent_id"] == "agent-1"
    assert server.create_payloads[0]["workspace"] == "https://github.com/org/repo#main"
    assert server.create_payloads[0]["labels"]["moonmind.issue"] == "MM-1059"

    manifest = json.loads(
        (tmp_path / "corr-1" / "output.omnigent.capture_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["workspaceFiles"][0]["path"] == "README.md"
    assert manifest["sessionFiles"][0]["filename"] == "session.log"
    if server.supports_diff:
        assert manifest["patchUnavailable"] is False
        assert manifest["workspaceDiffs"][0]["path"] == "src/app.py"
    else:
        assert manifest["patchUnavailable"] is True
        assert "workspaceDiffsUnavailable" in manifest


@pytest.mark.parametrize("fake_omnigent_server", [True], indirect=True)
async def test_omnigent_execute_required_artifact_persistence_failure_is_terminal(
    fake_omnigent_server,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Required MoonMind artifact reads fail closed before provider execution."""

    server, server_url = fake_omnigent_server
    monkeypatch.setenv("OMNIGENT_ENABLED", "1")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", server_url)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-required-artifact",
            idempotencyKey="idem-required-artifact",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"instructionRef": "artifact://omnigent/missing"},
                }
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    assert result.failure_class == "system_error"
    assert result.provider_error_code == "omnigent_artifact_persistence_failed"
    assert result.metadata["normalizedStatus"] == "failed"
    assert len(server.session_ids) == 1


@pytest.mark.parametrize("fake_omnigent_server", [True], indirect=True)
async def test_omnigent_execute_terminal_capture_retry_is_idempotent(
    fake_omnigent_server,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A retried terminal capture converges on the same bounded artifact refs."""

    _server, server_url = fake_omnigent_server
    monkeypatch.setenv("OMNIGENT_ENABLED", "1")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", server_url)
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-retry",
        idempotencyKey="idem-retry",
        parameters={
            "omnigent": {
                "agent": {"agentName": "codex"},
                "session": {"allowEmptyWorkspace": True},
                "prompt": {"text": "retry terminal capture"},
            }
        },
    )
    gateway = LocalOmnigentArtifactGateway(root=tmp_path)

    first = await run_omnigent_execution(request, artifact_gateway=gateway)
    first_manifest = json.loads(
        (tmp_path / "corr-retry" / "output.omnigent.capture_manifest.json").read_text()
    )
    second = await run_omnigent_execution(request, artifact_gateway=gateway)
    second_manifest = json.loads(
        (tmp_path / "corr-retry" / "output.omnigent.capture_manifest.json").read_text()
    )

    assert first.failure_class is None
    assert second.failure_class is None
    assert first.metadata["captureManifestRef"] == second.metadata["captureManifestRef"]
    assert first.output_refs == second.output_refs
    # A whole execution retry may create a new provider session. Terminal
    # capture identity is the MoonMind artifact set, not that provenance ID.
    first_manifest.pop("omnigentSessionId")
    second_manifest.pop("omnigentSessionId")
    assert first_manifest == second_manifest
