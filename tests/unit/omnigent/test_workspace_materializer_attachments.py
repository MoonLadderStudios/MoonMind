"""Ready-workspace retries retain ordinary input paths and candidate edits."""

import os
from types import SimpleNamespace

import pytest

from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from tests.unit.omnigent.test_workspace_materializer import _request, _workspace_id


@pytest.mark.asyncio
@pytest.mark.parametrize("named_evidence", [False, True])
@pytest.mark.parametrize("changed_brief", [False, True])
async def test_ready_workspace_readmits_ordinary_attachments_without_replacing_candidate(
    tmp_path, named_evidence, changed_brief
):
    workspace = tmp_path / "temporal_sandbox" / _workspace_id() / "repo"
    (workspace / ".git" / "info").mkdir(parents=True)
    candidate = workspace / "candidate.txt"
    candidate.write_bytes(b"saved candidate\n")
    payloads = {
        "original": b"original objective",
        "corrected": b"corrected instructions retaining the original objective",
        "gate": b'{"verdict":"ADDITIONAL_WORK_NEEDED"}',
    }
    metadata_reads = []

    class Artifacts:
        async def get_metadata(self, *, artifact_id, principal):
            assert principal == "service:omnigent_workspace_attachment"
            metadata_reads.append(artifact_id)
            return SimpleNamespace(size_bytes=len(payloads[artifact_id])), [
                SimpleNamespace(workflow_id="workflow-1")
            ]

        async def read_chunks(self, *, artifact_id, **kwargs):
            return SimpleNamespace(), iter((payloads[artifact_id],))

    async def no_clone(*args, **kwargs):
        raise AssertionError("retry must retain the saved candidate")

    request = _request(
        {
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": _workspace_id(),
                "relativePath": "repo",
            },
            "repository": "MoonLadderStudios/MoonMind",
            "branch": "main",
        }
    )
    request.input_refs = ["artifact://original"]
    if named_evidence:
        request.parameters = {"gateResultRef": "artifact://gate"}
    materializer = OmnigentWorkspaceMaterializer(
        command_runner=no_clone,
        workspace_root=tmp_path,
        artifact_service=Artifacts(),
    )
    identity = {"runtime_uid": os.getuid(), "runtime_gid": os.getgid()}
    first = await materializer.materialize(request, **identity)
    initial_paths = first["materializedInputPaths"]
    assert payloads["original"] in {
        (workspace / path).read_bytes() for path in initial_paths.values()
    }
    candidate.write_bytes(b"saved candidate\naccepted local edit\n")
    if changed_brief:
        request.input_refs.append("artifact://corrected")
    for _ in range(2):
        metadata_reads.clear()
        retried = await materializer.materialize(request, **identity)
        paths = retried["materializedInputPaths"]
        expected = {"original", "corrected"} if changed_brief else {"original"}
        if named_evidence:
            expected.add("gate")
            assert (workspace / paths["gateResultPath"]).read_bytes() == payloads[
                "gate"
            ]
        assert set(metadata_reads) == expected  # Ready state never supplies authority.
        assert {(workspace / path).read_bytes() for path in paths.values()} == {
            payloads[ref] for ref in expected
        }
        if not changed_brief:
            assert paths == initial_paths
        assert candidate.read_bytes() == b"saved candidate\naccepted local edit\n"
