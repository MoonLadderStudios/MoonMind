"""Unit tests for Omnigent bridge artifact publishing and resource harvesting."""

from __future__ import annotations

import json
from typing import Any

import pytest

from moonmind.omnigent.bridge_artifacts import (
    BridgeResourceHarvester,
    LocalOmnigentArtifactGateway,
    OmnigentArtifactError,
    OmnigentCaptureBundle,
    TemporalOmnigentArtifactGateway,
    _build_capture_bundle_impl,
    _associate_resource_events,
    _capture_resource_projection,
    _reconcile_changed_file_evidence,
    _redacted_endpoint_url,
    build_omnigent_terminal_refs,
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


def _step_request() -> AgentExecutionRequest:
    payload = _request().model_dump(by_alias=True, mode="json")
    payload.update(
        {
            "stepExecution": {
                "schemaVersion": "v1",
                "workflowId": "workflow-1",
                "runId": "run-1",
                "logicalStepId": "step-a",
                "executionOrdinal": 1,
                "stepExecutionId": "workflow-1:run-1:step-a:execution:1",
                "runtimeContextPolicy": "fresh_agent_run",
            },
        }
    )
    return AgentExecutionRequest.model_validate(payload)


def test_provider_endpoint_provenance_is_accepted_but_credentials_are_redacted() -> (
    None
):
    assert (
        _redacted_endpoint_url(
            "https://provider-user:provider-password@omnigent.example:8443/v1/?token=secret#session"
        )
        == "https://omnigent.example:8443/v1"
    )
    assert _redacted_endpoint_url("provider-native-session-id") == "redacted"


def test_durable_artifact_gateway_accepts_only_temporal_artifact_ids() -> None:
    assert TemporalOmnigentArtifactGateway._artifact_id("artifact:art_123") == "art_123"
    assert TemporalOmnigentArtifactGateway._artifact_id("art_456") == "art_456"

    with pytest.raises(OmnigentArtifactError, match="Unsupported durable artifact ref"):
        TemporalOmnigentArtifactGateway._artifact_id(
            "artifact://omnigent/local-only/evidence.json"
        )


def test_durable_artifact_gateway_links_generic_evidence_to_step_execution() -> None:
    link = TemporalOmnigentArtifactGateway._execution_link(
        _step_request(),
        name="host-attestation.json",
        link_type="evidence.host",
    )

    assert link is not None
    assert link.workflow_id == "workflow-1"
    assert link.run_id == "run-1"
    assert link.link_type == "evidence.host"
    assert link.created_by_activity_type == "integration.omnigent.execute"


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


class OversizedHarvestClient(FakeHarvestClient):
    async def get_workspace_file(self, _session_id: str, _path: str) -> bytes:
        return b"too large"

    async def get_workspace_diff(self, _session_id: str, _path: str) -> bytes:
        return b"too large"

    async def get_session_file_content(self, _session_id: str, _file_id: str) -> bytes:
        return b"too large"


def test_resource_reconciliation_accepts_explicit_null_lists() -> None:
    manifest = {"workspaceDiffs": None, "changedFiles": None, "sessionFiles": None}

    _reconcile_changed_file_evidence(manifest)
    _associate_resource_events(manifest, [])


def test_local_artifact_refs_do_not_advertise_unresolvable_ui_actions() -> None:
    projection = _capture_resource_projection(
        {
            "changedFiles": [
                {
                    "path": "src/app.py",
                    "artifactRef": "artifact://omnigent/corr/src/app.py",
                }
            ]
        }
    )

    resource = projection["groups"][0]["resources"][0]
    assert resource["status"] == "available"
    assert resource["previewAvailable"] is False
    assert resource["downloadAvailable"] is False


def test_required_evidence_failure_is_reflected_in_bridge_terminal_refs() -> None:
    refs = build_omnigent_terminal_refs(
        OmnigentCaptureBundle(
            output_refs=["artifact://omnigent/corr/output"],
            diagnostics_ref="artifact://omnigent/corr/diagnostics",
            resource_harvest_failure_class="system_error",
        ),
        terminal_status="completed",
        final_snapshot={"summary": "done"},
    )

    assert refs["failureClass"] == "system_error"
    assert refs["failureCode"] == "omnigent_required_resource_evidence_missing"
    assert "evidence" in refs["summary"].lower()


@pytest.mark.asyncio
async def test_read_bytes_roundtrips_written_artifact(tmp_path) -> None:
    gateway = LocalOmnigentArtifactGateway(root=tmp_path)
    ref = await gateway.write_bytes(
        request=_request(),
        name="output.bin",
        payload=b"captured-evidence",
        link_type="input.attachment",
    )

    assert await gateway.read_bytes(ref) == b"captured-evidence"


@pytest.mark.asyncio
async def test_capture_policy_disables_stream_and_evidence_artifacts(tmp_path) -> None:
    gateway = LocalOmnigentArtifactGateway(root=tmp_path)

    bundle = await _build_capture_bundle_impl(
        client=FakeHarvestClient(),
        artifact_gateway=gateway,
        request=_request(),
        session_id="session-1",
        agent_id="agent-1",
        initial_snapshot={"id": "session-1"},
        final_snapshot={"id": "session-1", "status": "completed"},
        first_message_request={"parts": [{"text": "read the repository"}]},
        first_message_response={"id": "message-1"},
        first_message_posted=True,
        first_message_response_identifiers={"messageId": "message-1"},
        raw_events=[{"type": "session.completed"}],
        normalized_events=[{"type": "session.completed"}],
        terminal_status="completed",
        diagnostics={},
        harvest_resources=True,
        capture_policy={"stream": False, "evidence": False},
    )

    assert "rawSseStreamRef" not in bundle.metadata_refs
    assert "normalizedEventStreamRef" not in bundle.metadata_refs
    assert "finalSnapshotRef" not in bundle.metadata_refs
    captured_names = {path.name for path in tmp_path.rglob("*") if path.is_file()}
    assert "runtime.omnigent.sse.raw.jsonl" not in captured_names
    assert "runtime.omnigent.snapshot.initial.json" not in captured_names
    assert "output.omnigent.snapshot.final.json" not in captured_names


@pytest.mark.asyncio
async def test_capture_redacts_credentials_from_provider_snapshots(tmp_path) -> None:
    gateway = LocalOmnigentArtifactGateway(root=tmp_path)
    fake_token = "ghp_" + "a" * 36
    credential_url = (
        f"https://x-access-token:{fake_token}@github.com/example/repository.git"
    )
    snapshot = {
        "id": "session-1",
        "status": "completed",
        "items": [
            {
                "type": "function_call_output",
                "data": {"output": f"origin\t{credential_url} (fetch)"},
            }
        ],
    }

    await _build_capture_bundle_impl(
        client=None,
        artifact_gateway=gateway,
        request=_request(),
        session_id="session-1",
        agent_id="agent-1",
        initial_snapshot=snapshot,
        final_snapshot=snapshot,
        first_message_request=None,
        first_message_response=None,
        first_message_posted=True,
        first_message_response_identifiers=None,
        raw_events=[],
        normalized_events=[],
        terminal_status="completed",
        diagnostics={},
        harvest_resources=False,
    )

    initial = (
        tmp_path / "corr-1" / "runtime.omnigent.snapshot.initial.json"
    ).read_text(encoding="utf-8")
    final = (
        tmp_path / "corr-1" / "output.omnigent.snapshot.final.json"
    ).read_text(encoding="utf-8")
    assert fake_token not in initial
    assert fake_token not in final
    assert "[REDACTED]" in initial
    assert "[REDACTED]" in final


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["read_text", "read_bytes"])
@pytest.mark.parametrize(
    "escaping_ref",
    [
        "artifact://omnigent/../../etc/passwd",
        "artifact://omnigent//etc/passwd",
    ],
)
async def test_read_rejects_refs_escaping_artifact_root(
    tmp_path, reader, escaping_ref
) -> None:
    gateway = LocalOmnigentArtifactGateway(root=tmp_path / "root")

    with pytest.raises(OmnigentArtifactError, match="escapes artifact root"):
        await getattr(gateway, reader)(escaping_ref)


@pytest.mark.asyncio
async def test_resource_harvester_does_not_persist_content_over_byte_limit(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "moonmind.omnigent.bridge_artifacts._MAX_OMNIGENT_CONTENT_BYTES", 1
    )
    refs: dict[str, str] = {}
    manifest: dict[str, Any] = {"artifactRefs": refs}
    harvester = BridgeResourceHarvester(
        client=OversizedHarvestClient(),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
        request=_request(),
        session_id="session-1",
        manifest=manifest,
        refs=refs,
    )

    await harvester.harvest_resources(capture_policy=None)

    for group in ("changedFiles", "workspaceFiles", "workspaceDiffs", "sessionFiles"):
        assert any(
            "harvest limit" in item.get("unavailable", "") for item in manifest[group]
        )
    assert not list(tmp_path.rglob("app.py"))
    assert not list(tmp_path.rglob("session.log"))


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
    assert (
        manifest["changedFiles"][0]["diffArtifactRef"]
        == manifest["workspaceDiffs"][0]["artifactRef"]
    )
    assert refs["changedFilesIndexRef"].endswith(
        "/output.omnigent.changed_files.index.json"
    )
    assert refs["childSessionsRef"].endswith("/runtime.omnigent.child_sessions.jsonl")

    diff = (
        tmp_path / "corr-1" / "output.omnigent.workspace_diffs" / "src" / "app.py.diff"
    )
    assert diff.read_text(encoding="utf-8") == "diff --git a/src/app.py b/src/app.py\n"
    child_snapshot = json.loads(
        (
            tmp_path / "corr-1" / "runtime.omnigent.child_sessions" / "child-1.json"
        ).read_text(encoding="utf-8")
    )
    assert child_snapshot["id"] == "child-1"
