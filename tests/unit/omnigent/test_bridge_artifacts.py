"""Unit tests for Omnigent bridge artifact publishing and resource harvesting."""

from __future__ import annotations

import json
from typing import Any

import pytest

from moonmind.omnigent.bridge_artifacts import (
    BridgeResourceHarvester,
    LocalOmnigentArtifactGateway,
    _redacted_endpoint_url,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile:test",
        correlationId="corr-1",
        idempotencyKey="idem-1",
    )


def test_provider_endpoint_provenance_is_accepted_but_credentials_are_redacted() -> None:
    assert (
        _redacted_endpoint_url(
            "https://provider-user:provider-password@omnigent.example:8443/v1/?token=secret#session"
        )
        == "https://omnigent.example:8443/v1"
    )
    assert _redacted_endpoint_url("provider-native-session-id") == "redacted"


class FakeHarvestClient:
    async def list_changed_files(self, _session_id: str) -> dict[str, Any]:
        return {"items": [{"path": "src/app.py"}]}

    async def list_workspace_files(self, _session_id: str) -> dict[str, Any]:
        return {
            "items": [
                {"path": "README.md", "type": "file"},
                {"path": "src", "type": "directory"},
            ]
        }

    async def get_workspace_file(self, _session_id: str, path: str) -> bytes:
        return {
            "README.md": b"# Fake repo\n",
            "src/app.py": b"print('fake')\n",
        }[path]

    async def get_workspace_diff(self, _session_id: str, path: str) -> bytes:
        return f"diff --git a/{path} b/{path}\n".encode("utf-8")

    async def list_session_files(self, _session_id: str) -> dict[str, Any]:
        return {"items": [{"id": "file-1", "filename": "session.log"}]}

    async def get_session_file_content(self, _session_id: str, _file_id: str) -> bytes:
        return b"session file evidence\n"

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return {"id": session_id, "status": "completed"}


@pytest.mark.asyncio
async def test_bridge_resource_harvester_writes_section_12_artifacts(tmp_path) -> None:
    refs: dict[str, str] = {}
    manifest: dict[str, Any] = {"patchUnavailable": True, "artifactRefs": refs}
    harvester = BridgeResourceHarvester(
        client=FakeHarvestClient(),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
        request=_request(),
        session_id="session-1",
        manifest=manifest,
        refs=refs,
    )

    await harvester.harvest_child_sessions(
        [{"type": "session.child.created", "childSessionId": "child-1"}]
    )
    await harvester.harvest_resources(capture_policy=None)

    assert manifest["childSessions"] == 1
    assert manifest["changedFiles"][0]["path"] == "src/app.py"
    assert manifest["workspaceFiles"][1] == {"path": "src", "skipped": "directory"}
    assert manifest["workspaceDiffs"][0]["path"] == "src/app.py"
    assert manifest["sessionFiles"][0]["filename"] == "session.log"
    assert manifest["patchUnavailable"] is False
    assert manifest["changedFiles"][0]["diffArtifactRef"] == manifest["workspaceDiffs"][0]["artifactRef"]
    assert refs["changedFilesIndexRef"].endswith(
        "/output.omnigent.changed_files.index.json"
    )
    assert refs["childSessionsRef"].endswith("/runtime.omnigent.child_sessions.jsonl")

    diff = (
        tmp_path
        / "corr-1"
        / "output.omnigent.workspace_diffs"
        / "src"
        / "app.py.diff"
    )
    assert diff.read_text(encoding="utf-8") == "diff --git a/src/app.py b/src/app.py\n"
    child_snapshot = json.loads(
        (
            tmp_path
            / "corr-1"
            / "runtime.omnigent.child_sessions"
            / "child-1.json"
        ).read_text(encoding="utf-8")
    )
    assert child_snapshot["id"] == "child-1"
