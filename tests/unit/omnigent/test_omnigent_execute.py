from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from moonmind.omnigent.execute import _execute_with_client, _ArtifactRecorder
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.skills.artifact_store import FileArtifactStore


class FakeOmnigentClient:
    async def create_session(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self.session_request = dict(payload)
        return {
            "id": "conv_123",
            "authToken": "secret-token",
            "status": "created",
        }

    async def get_session(self, session_id: str) -> dict[str, Any]:
        assert session_id == "conv_123"
        return {
            "id": session_id,
            "status": "completed",
            "summary": "Final snapshot summary",
        }

    async def post_event(
        self,
        session_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        assert session_id == "conv_123"
        self.first_message = dict(payload)
        return {"ok": True, "sessionCookie": "secret-cookie"}

    async def stream_events(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        assert session_id == "conv_123"
        yield {
            "type": "response.output_text.delta",
            "data": {
                "text": "Opened PR https://github.com/acme/widgets/pull/42",
            },
        }
        yield {
            "type": "child_session.created",
            "childSessionId": "conv_child_1",
        }
        yield {"type": "session.completed", "data": {"status": "completed"}}

    async def list_changed_files(self, session_id: str) -> dict[str, Any]:
        assert session_id == "conv_123"
        return {"items": [{"path": "src/app.py"}]}

    async def get_workspace_file(self, session_id: str, path: str) -> bytes:
        assert session_id == "conv_123"
        assert path == "src/app.py"
        return b"print('updated')\n"

    async def list_session_files(self, session_id: str) -> dict[str, Any]:
        assert session_id == "conv_123"
        return {"items": [{"id": "file_1", "filename": "notes.md", "size": 12}]}

    async def get_session_file_content(self, session_id: str, file_id: str) -> bytes:
        assert session_id == "conv_123"
        assert file_id == "file_1"
        return b"# Notes\n"


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        workspaceSpec={"repository": "https://github.com/acme/widgets#main"},
        parameters={
            "description": "Implement the requested change",
            "omnigent": {
                "agent": {"agentId": "ag_123"},
                "session": {"hostType": "managed"},
            },
        },
    )


def _artifact_payload(root: Path, ref: str) -> bytes:
    return FileArtifactStore(root).get_bytes(ref)


def _artifact_json(root: Path, ref: str) -> Any:
    return FileArtifactStore(root).get_json(ref)


@pytest.mark.asyncio
async def test_omnigent_execute_captures_streams_resources_and_diagnostics(tmp_path):
    recorder = _ArtifactRecorder(root=tmp_path)
    diagnostics: dict[str, Any] = {
        "provider": "omnigent",
        "patch": {
            "status": "patch_unavailable",
            "reason": "no_supported_patch_source",
        },
        "childSessionIds": [],
        "githubPrUrls": [],
    }

    result = await _execute_with_client(
        request=_request(),
        client=FakeOmnigentClient(),
        recorder=recorder,
        diagnostics=diagnostics,
    )

    artifact_by_name = {artifact.name: artifact.ref for artifact in recorder.artifacts}
    expected_names = {
        "input.omnigent.session_create.request.json",
        "input.omnigent.session_create.response.json",
        "input.omnigent.first_message.request.json",
        "input.omnigent.first_message.response.json",
        "runtime.omnigent.snapshot.initial.json",
        "runtime.omnigent.sse.raw.jsonl",
        "runtime.omnigent.sse.normalized.jsonl",
        "output.omnigent.snapshot.final.json",
        "output.omnigent.transcript.jsonl",
        "output.omnigent.final_response.md",
        "output.workspace.changed_files.index.json",
        "output.workspace.files/src/app.py.current",
        "output.workspace.manifest.json",
        "output.omnigent.session_files.index.json",
        "output.omnigent.session_files/file_1/notes.md",
        "output.omnigent.session_files/file_1/metadata.json",
        "output.workspace.patch_unavailable.json",
        "output.github.pr.metadata.json",
        "runtime.omnigent.child_sessions.jsonl",
        "runtime.omnigent.diagnostics.json",
    }
    assert expected_names.issubset(artifact_by_name)
    assert result.diagnostics_ref == artifact_by_name["runtime.omnigent.diagnostics.json"]
    assert result.failure_class is None
    assert result.metadata["githubPrUrl"] == "https://github.com/acme/widgets/pull/42"
    assert result.metadata["childSessionIds"] == ["conv_child_1"]
    assert result.metadata["sourceIssue"] == "MM-993"
    assert result.metadata["sourceIssueTrace"] == "MM-993"
    assert set(result.output_refs) >= set(artifact_by_name.values())

    pr_metadata = _artifact_json(
        tmp_path,
        artifact_by_name["output.github.pr.metadata.json"],
    )
    assert pr_metadata == {
        "githubPrUrls": ["https://github.com/acme/widgets/pull/42"],
    }

    diagnostics_payload = _artifact_json(
        tmp_path,
        artifact_by_name["runtime.omnigent.diagnostics.json"],
    )
    assert diagnostics_payload["patch"]["status"] == "patch_unavailable"
    assert diagnostics_payload["childSessionIds"] == ["conv_child_1"]
    assert diagnostics_payload["githubPrUrls"] == [
        "https://github.com/acme/widgets/pull/42"
    ]

    session_response = _artifact_json(
        tmp_path,
        artifact_by_name["input.omnigent.session_create.response.json"],
    )
    first_response = _artifact_json(
        tmp_path,
        artifact_by_name["input.omnigent.first_message.response.json"],
    )
    assert session_response["authToken"] == "[REDACTED]"
    assert first_response["sessionCookie"] == "[REDACTED]"

    final_response = _artifact_payload(
        tmp_path,
        artifact_by_name["output.omnigent.final_response.md"],
    ).decode("utf-8")
    assert "https://github.com/acme/widgets/pull/42" in final_response
