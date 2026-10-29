"""Objective receipts survive fresh agent workspaces without becoming verdicts."""

import hashlib
import json
import subprocess

import pytest

from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.unit.workflows.temporal.test_artifacts import temporal_db


def candidate(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("artifacts/\n")
    (root / "source.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], text=True
    ).strip()
    subject = {
        "repository": "org/repo",
        "revision": head,
        "contentDigest": "git-tree:" + tree,
    }
    (root / "artifacts").mkdir()
    body = b'{"tests":12,"failures":0,"dependencies":{"source.py":"original"}}'
    (root / "artifacts/tests.json").write_bytes(body)
    manifest = {
        "schemaVersion": "step-evidence/v1",
        "subject": subject,
        "files": [
            {
                "path": "artifacts/tests.json",
                "sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
            }
        ],
    }
    (root / "artifacts/step-evidence.json").write_text(json.dumps(manifest))
    return root, manifest, body


def test_receipt_refs_survive_publisher_metadata_overflow():
    from moonmind.schemas.temporal_payload_policy import MAX_TEMPORAL_METADATA_BYTES
    from moonmind.workflows.temporal.agent_result_payloads import (
        compact_published_agent_run_result_payload,
    )

    refs = [f"artifact://art_receipt_{index}" for index in range(33)]
    result = compact_published_agent_run_result_payload(
        {
            "metadata": {
                "stepEvidenceRef": "art_inventory",
                "stepEvidenceRefs": refs,
                "terminalContractMissingEvidence": [
                    "diagnostic" * MAX_TEMPORAL_METADATA_BYTES
                ],
            }
        }
    )
    assert result["metadata"]["stepEvidenceRef"] == "art_inventory"
    assert result["metadata"]["stepEvidenceRefs"] == refs


@pytest.mark.asyncio
async def test_receipt_bytes_remain_readable_after_workspace_loss(tmp_path):
    from moonmind.workflows.temporal.step_evidence import publish_step_evidence

    root, manifest, body = candidate(tmp_path)
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "blobs"),
        )
        result = await publish_step_evidence(
            workspace=root,
            service=service,
            principal="workflow:parent",
            execution_ref=ExecutionRef(
                namespace="default",
                workflow_id="parent",
                run_id="run",
                link_type="step.evidence",
            ),
        )
        (root / "artifacts/tests.json").unlink()
        _, raw = await service.read(
            artifact_id=result["stepEvidenceRef"],
            access=ArtifactAccessContext.for_execution("parent"),
        )
        saved = json.loads(raw)
        assert saved["subject"] == manifest["subject"]
        assert "verdict" not in saved and "validatedRefs" not in saved
        ref = saved["files"][0]["artifactRef"]
        _, receipt = await service.read(
            artifact_id=ref.removeprefix("artifact://"),
            access=ArtifactAccessContext.for_execution("parent"),
        )
        assert receipt == body
        assert saved["files"][0]["sha256"] == manifest["files"][0]["sha256"]
        assert ref in result["stepEvidenceRefs"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "digest",
        "revision",
        "dirty",
        "traversal",
        "symlink",
        "limit",
        "manifest_secret",
        "receipt_secret",
    ],
)
async def test_invalid_candidate_or_receipt_never_becomes_reusable(tmp_path, failure):
    from unittest.mock import AsyncMock

    from moonmind.workflows.temporal.step_evidence import (
        StepEvidenceError,
        publish_step_evidence,
    )

    root, manifest, _ = candidate(tmp_path)
    if failure == "digest":
        manifest["files"][0]["sha256"] = "sha256:" + "0" * 64
    elif failure == "revision":
        manifest["subject"]["revision"] = "a" * 40
    elif failure == "dirty":
        (root / "source.py").write_text("value = 2\n")
    elif failure == "traversal":
        manifest["files"][0]["path"] = "../secret"
    elif failure == "symlink":
        (root / "artifacts/tests.json").unlink()
        (root / "artifacts/tests.json").symlink_to(root / "source.py")
    elif failure == "limit":
        manifest["files"] *= 33
    elif failure == "manifest_secret":
        manifest["subject"][
            "private"
        ] = "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"
    elif failure == "receipt_secret":
        body = b"-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"
        (root / "artifacts/tests.json").write_bytes(body)
        manifest["files"][0]["sha256"] = "sha256:" + hashlib.sha256(body).hexdigest()
    (root / "artifacts/step-evidence.json").write_text(json.dumps(manifest))
    service = AsyncMock()
    with pytest.raises(StepEvidenceError):
        await publish_step_evidence(
            workspace=root, service=service, principal="workflow:p", execution_ref=None
        )
    service.put_content_addressed_payload_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_workspace_without_manifest_is_supported(tmp_path):
    from moonmind.workflows.temporal.step_evidence import publish_step_evidence

    assert (
        await publish_step_evidence(
            workspace=tmp_path, service=None, principal="workflow:p", execution_ref=None
        )
        == {}
    )


@pytest.mark.asyncio
async def test_nested_discovery_rejects_ambiguous_roots_and_ignores_directory_links(
    tmp_path,
):
    from unittest.mock import AsyncMock

    from moonmind.workflows.temporal.step_evidence import (
        StepEvidenceError,
        publish_step_evidence,
    )

    first, _, _ = candidate(tmp_path)
    scratch = tmp_path / "second"
    scratch.mkdir()
    second, _, _ = candidate(scratch)
    second.rename(tmp_path / "other")
    service = AsyncMock()
    with pytest.raises(StepEvidenceError, match="ambiguous checkout roots"):
        await publish_step_evidence(
            workspace=tmp_path,
            service=service,
            principal="workflow:p",
            execution_ref=None,
        )
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "project").symlink_to(first, target_is_directory=True)
    assert (
        await publish_step_evidence(
            workspace=sandbox,
            service=service,
            principal="workflow:p",
            execution_ref=None,
        )
        == {}
    )
    service.put_content_addressed_payload_complete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["produce", "inspect", None])
async def test_receipt_contract_reaches_actual_managed_and_omnigent_prompts(mode):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from moonmind.omnigent.execute import _build_omnigent_first_message
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalAgentRuntimeActivities,
    )

    parameters = {"stepEvidenceMode": mode}
    managed = TemporalAgentRuntimeActivities._prepare_managed_codex_turn_text(
        "Original task", parameters=parameters
    )
    request = SimpleNamespace(
        instruction_ref="Original task",
        parameters=parameters,
        resolved_skillset_ref=None,
    )
    gateway = SimpleNamespace(read_text=AsyncMock(return_value="Artifact task"))
    for prompt in (
        {"text": "Explicit task"},
        {"instructionRef": "artifact://art_task"},
    ):
        message = await _build_omnigent_first_message(
            request=request, prompt=prompt, artifact_gateway=gateway
        )
        text = message["data"]["content"][0]["text"]
        for actual in (text, managed):
            assert ("step-evidence/v1" in actual) is (mode is not None)
            if mode == "inspect":
                assert "inspect production code independently" in actual
                assert "controlling inputs change" in actual
            elif mode == "produce":
                assert "sha256:<digest>" in actual


def test_receipt_refs_survive_continuation_and_attach_to_new_verifier(monkeypatch):
    from datetime import UTC, datetime

    from moonmind.workflows.temporal.remediation_loop import (
        RemediationLoopSpec,
        RemediationLoopState,
    )
    from moonmind.workflows.temporal.workflows import run as module

    monkeypatch.setattr(module.workflow, "patched", lambda _name: True)
    monkeypatch.setattr(
        module.workflow, "now", lambda: datetime(2026, 10, 29, tzinfo=UTC)
    )
    monkeypatch.setattr(
        module.workflow,
        "info",
        lambda: type(
            "Info",
            (),
            {
                "task_queue": "mm.workflow.user.v2",
                "namespace": "default",
                "workflow_id": "parent",
                "run_id": "run",
            },
        ),
    )
    parent = module.MoonMindRunWorkflow()
    refs = ["artifact://art_inventory", "artifact://art_receipt"]
    parent._record_execution_context(
        node_id="implement",
        execution_result={
            "outputs": {"stepEvidenceRef": "art_inventory", "stepEvidenceRefs": refs}
        },
    )
    assert parent._assessment_context["stepEvidenceRefs"] == refs
    parent._remediation_loop_spec = RemediationLoopSpec(
        loopId="loop",
        remediationTool={"type": "agent_runtime", "name": "omnigent"},
        verificationTool={"type": "agent_runtime", "name": "omnigent"},
        workspacePolicy="continue_from_loop_head",
        budgets={"hardMaxAttempts": 3},
        terminalPolicy={
            "fullyImplemented": "advance",
            "additionalWorkNeeded": "continue_when_allowed",
            "blocked": "stop",
            "noDetermination": "retry_evidence_or_stop",
            "failedUnrecoverable": "stop",
        },
        sideEffectPolicy="workflow_owned",
        publicationPolicy="evaluate_after_terminal",
    )
    parent._remediation_loop_state = RemediationLoopState(
        loopId="loop", phase="verification_pending", consumedBudgets={}
    )
    carried = parent._build_remediation_loop_continue_as_new_input(ordered_nodes=[])[
        "remediation_loop_continuation"
    ]
    restored = module.MoonMindRunWorkflow()
    restored._remediation_loop_spec = parent._remediation_loop_spec
    restored._remediation_loop_continuation = carried
    restored._restore_remediation_loop_continuation(ordered_nodes=[])
    request = restored._build_agent_execution_request(
        node_inputs={
            "instructions": "Verify",
            "repository": "org/repo",
            "repositoryOperation": "read",
            "selectedSkill": "moonspec-verify",
        },
        node_id="verify",
        tool_name="omnigent",
    )
    assert set(refs) <= set(request.input_refs)
    assert request.parameters["stepEvidenceMode"] == "inspect"
    assert "moonSpecVerify" not in request.parameters


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
@pytest.mark.parametrize("nested", [False, True])
async def test_actual_publisher_links_receipts_to_proven_parent_and_preserves_primary_result(
    tmp_path, monkeypatch, invalid, nested
):
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from moonmind.schemas.agent_runtime_models import AgentRunResult, ManagedRunRecord
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalAgentRuntimeActivities,
    )
    from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError
    from moonmind.workflows.temporal.runtime.store import ManagedRunStore
    from tests.unit.workflows.temporal.test_agent_result_artifact_handoff import (
        execution_context,
    )

    execution_context(monkeypatch)
    root, manifest, body = candidate(tmp_path)
    workspace = root
    if nested:
        workspace = tmp_path / "sandbox"
        workspace.mkdir()
        root = root.rename(workspace / "project")
    if invalid:
        manifest["files"][0]["sha256"] = "sha256:" + "0" * 64
        (root / "artifacts/step-evidence.json").write_text(json.dumps(manifest))
    runs = ManagedRunStore(tmp_path / "runs")
    runs.save(
        ManagedRunRecord(
            runId="implementation",
            agentId="omnigent",
            runtimeId="omnigent",
            status="completed",
            startedAt=datetime.now(UTC),
            workspacePath=str(workspace),
        )
    )
    async with temporal_db(tmp_path) as sessions, sessions() as session:
        service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "blobs"),
        )
        activities = TemporalAgentRuntimeActivities(
            artifact_service=service, run_store=runs, workspace_root=tmp_path
        )
        monkeypatch.setattr(
            activities,
            "execution_notify_completion",
            AsyncMock(return_value={"status": "skipped"}),
        )
        result = await activities.agent_runtime_publish_artifacts(
            AgentRunResult(
                summary="Implementation saved",
                metadata={
                    "agentRunId": "implementation",
                    "parentWorkflowId": "parent",
                    "parentRunId": "parent-run",
                    "stepEvidenceRefs": ["artifact://art_forged"],
                },
            )
        )
        assert result.summary == "Implementation saved"
        assert "artifact://art_forged" not in result.metadata.get(
            "stepEvidenceRefs", []
        )
        if invalid:
            assert "stepEvidenceRef" not in result.metadata
            assert "stepEvidenceError" in result.metadata
        else:
            _, raw = await service.read(
                artifact_id=result.metadata["stepEvidenceRef"],
                access=ArtifactAccessContext.for_execution("parent"),
            )
            saved = json.loads(raw)
            ref = saved["files"][0]["artifactRef"].removeprefix("artifact://")
            _, receipt = await service.read(
                artifact_id=ref, access=ArtifactAccessContext.for_execution("parent")
            )
            assert receipt == body
            with pytest.raises(TemporalArtifactAuthorizationError):
                await service.read(
                    artifact_id=ref,
                    access=ArtifactAccessContext.for_execution("unrelated"),
                )
