"""Published receipts cross continuation and the actual bounded materializer."""

import hashlib
import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from moonmind.omnigent import workspace_artifacts
from moonmind.workflows.temporal.remediation_loop import (
    RemediationLoopSpec,
    RemediationLoopState,
)
from moonmind.workflows.temporal.step_evidence import (
    publish_step_evidence,
    step_evidence_projection_options,
)
from moonmind.workflows.temporal.workflows import run as module
from tests.unit.omnigent.test_workspace_safe_sources import FakeArtifactService
from tests.unit.workflows.temporal.test_step_evidence import candidate


class _Store(FakeArtifactService):
    async def put_content_addressed_payload_complete(self, *, payload, **_kwargs):
        artifact_id = f"art_receipt_{len(self.payloads)}"
        self.payloads[artifact_id] = payload
        return SimpleNamespace(artifact_id=artifact_id), True


def _continue(parent, monkeypatch):
    monkeypatch.setattr(module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(
        module.workflow, "now", lambda: datetime(2026, 10, 29, tzinfo=UTC)
    )
    monkeypatch.setattr(
        module.workflow,
        "info",
        lambda: SimpleNamespace(
            task_queue="mm.workflow.user.v2",
            namespace="default",
            workflow_id="parent",
            run_id="run",
        ),
    )
    parent._remediation_loop_spec = RemediationLoopSpec(
        loopId="loop",
        remediationTool={"name": "omnigent"},
        verificationTool={"name": "omnigent"},
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
    restored = module.MoonMindRunWorkflow()
    restored._remediation_loop_spec = parent._remediation_loop_spec
    restored._remediation_loop_continuation = (
        parent._build_remediation_loop_continue_as_new_input(ordered_nodes=[])[
            "remediation_loop_continuation"
        ]
    )
    restored._restore_remediation_loop_continuation(ordered_nodes=[])
    return restored._build_agent_execution_request(
        node_inputs={
            "instructions": "Verify",
            "repository": "org/repo",
            "repositoryOperation": "read",
            "selectedSkill": "moonspec-verify",
        },
        node_id="verify",
        tool_name="omnigent",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["two_maximum_file_counts", "aggregate_bytes"])
@pytest.mark.parametrize("route", ["shared", "profile_bound"])
async def test_publisher_continuation_materializer_keeps_whole_groups_within_total_budget(
    tmp_path, monkeypatch, case, route
):
    monkeypatch.setattr(module.workflow, "patched", lambda _: True)
    root, manifest, _ = candidate(tmp_path)
    store = _Store(
        {"art_brief": b"original issue", "art_gate": b"original gate"},
        workflow_id="parent",
    )
    parent = module.MoonMindRunWorkflow()
    parent._assessment_context.update(
        briefArtifactRef="art_brief", assessmentArtifactRef="art_gate"
    )
    iterations, files, size = (
        (2, 32, 64) if case == "two_maximum_file_counts" else (12, 4, 8 * 1024)
    )
    if case == "aggregate_bytes":
        # Exercise the same arithmetic at KiB scale rather than allocating
        # 384 MiB. Production remains 256 MiB; its limit is never raised.
        monkeypatch.setattr(workspace_artifacts, "MAX_TOTAL_BYTES", 256 * 1024)
    bundles = []
    for attempt in range(iterations):
        entries = []
        for index in range(files):
            path = f"artifacts/check-{attempt}-{index}.txt"
            body = f"{attempt}:{index}:".encode() + b"a" * size
            (root / path).write_bytes(body)
            entries.append(
                {"path": path, "sha256": "sha256:" + hashlib.sha256(body).hexdigest()}
            )
        (root / "artifacts/step-evidence.json").write_text(
            json.dumps({**manifest, "files": entries})
        )
        published = await publish_step_evidence(
            workspace=root,
            service=store,
            principal="workflow:parent",
            execution_ref=None,
        )
        bundles.append(published["stepEvidenceRefs"])
        parent._record_execution_context(
            node_id=f"attempt-{attempt}", execution_result={"outputs": published}
        )
    request = _continue(parent, monkeypatch)
    assert request.parameters["stepEvidenceBundles"] == bundles
    proposed = set(request.input_refs)
    if case == "two_maximum_file_counts":
        assert len(proposed) == 68
    else:
        assert len(proposed) == 62
        assert (
            sum(
                len(store.payloads[ref.removeprefix("artifact://")]) for ref in proposed
            )
            > workspace_artifacts.MAX_TOTAL_BYTES
        )
    if route == "shared":
        fresh = tmp_path / "fresh-verifier"
        fresh.mkdir()
        evidence = await workspace_artifacts.WorkspaceArtifactProjector(
            store
        ).project_attachments(
            fresh,
            refs=tuple(request.input_refs),
            workflow_id="parent",
            runtime_uid=os.getuid(),
            runtime_gid=os.getgid(),
            **step_evidence_projection_options(request.parameters),
        )
    else:
        from moonmind.omnigent.profile_bound_execution import (
            OmnigentProfileBoundExecutionCoordinator,
        )
        from tests.unit.omnigent.test_oauth_profile_lifecycle import (
            _binding,
            _egress_attestation,
            _host_lease,
            _runtime_for,
        )

        runtime = _runtime_for(tmp_path)
        runtime._validate_effective_launch = MagicMock(
            return_value={
                "providerRuntime": "codex_cli",
                "harness": "codex-native",
                "hostMode": "on_demand_docker",
                "mountClasses": ["workspace"],
            }
        )
        runtime._prepare_skill_projection = AsyncMock(return_value=tmp_path / "skills")
        runtime._attest_egress = AsyncMock(return_value=_egress_attestation())

        class PreparedBeforeHostLaunch(Exception):
            pass

        runtime._attest_server_image = AsyncMock(side_effect=PreparedBeforeHostLaunch)
        workspace_id = hashlib.sha256(b"parent:step-1").hexdigest()[:24]
        fresh = tmp_path / "workspaces" / "temporal_sandbox" / workspace_id / "repo"

        async def prepare():
            # This is the same request/ref boundary used by the admitted
            # codex-profile-bound realizer's coordinator. Only external host
            # attestation/launch and Skill setup are stubbed; workspace
            # authority, clone, byte projection and their limits are real.
            with pytest.raises(PreparedBeforeHostLaunch):
                await runtime.prepare_host(
                    binding=_binding(),
                    host_lease=_host_lease(),
                    workspace_key="parent:step-1",
                    workspace_locator={"kind": "sandbox", "workspaceId": workspace_id},
                    current_workflow_id="parent",
                    current_step_execution_id="step-1",
                    repository_source=str(root),
                    starting_branch="main",
                    artifact_gateway=store,
                    evidence_request=request,
                    attachment_refs=OmnigentProfileBoundExecutionCoordinator._attachment_refs(
                        request
                    ),
                )
            return runtime._last_workspace_evidence["materialization"]["attachments"]

        evidence = await prepare()
    visible = {item["ref"] for item in evidence}
    assert {"artifact://art_brief", "artifact://art_gate"} <= visible
    assert set(bundles[-1]) <= visible
    assert len(visible) <= workspace_artifacts.MAX_INPUT_REFS
    assert (
        sum(item["bytes"] for item in evidence) <= workspace_artifacts.MAX_TOTAL_BYTES
    )
    for group in bundles:
        assert visible.intersection(group) in (set(), set(group))
    assert len(visible) < len(proposed)
    if route == "profile_bound":
        (fresh / "retained-candidate.txt").write_bytes(b"accepted local edit")
        store.payloads["art_next"] = b"new ordinary input"
        request.input_refs.append("artifact://art_next")
        refreshed = await prepare()
        assert "artifact://art_next" in {item["ref"] for item in refreshed}
        assert (fresh / "retained-candidate.txt").read_bytes() == b"accepted local edit"


def test_history_retention_drops_whole_bundles_not_individual_reports(monkeypatch):
    monkeypatch.setattr(module.workflow, "patched", lambda _: True)
    parent = module.MoonMindRunWorkflow()
    bundles = []
    for attempt in range(5):
        group = [f"artifact://art_{attempt}_{i}" for i in range(33)]
        bundles.append(group)
        parent._record_execution_context(
            node_id=str(attempt),
            execution_result={
                "outputs": {"stepEvidenceRef": group[0], "stepEvidenceRefs": group}
            },
        )
    assert parent._assessment_context["stepEvidenceBundles"] == bundles[-3:]
    assert parent._assessment_context["stepEvidenceRefs"] == [
        ref for group in bundles[-3:] for ref in group
    ]
