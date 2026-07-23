from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.workflows.temporal.publication_recovery import publication_operation_key
from moonmind.workflows.temporal.workflows import publication_recovery as workflow_module
from moonmind.workflows.temporal.workflows.publication_recovery import (
    MoonMindPublicationRecoveryWorkflow,
)


def _contract(*, remote: bool = False) -> dict[str, Any]:
    return {
        "schemaVersion": "publication-recovery-v1",
        "sourceWorkflowId": "source",
        "sourceRunId": "source-run",
        "sourceSemanticOutcome": "accepted",
        "target": {
            "kind": "publication",
            "publicationKind": "pull_request",
            "sourcePublicationOperationId": "source-operation",
            "semanticContext": "accepted",
        },
        "continuation": {
            "phase": "resume_publication",
            "publicationIdempotencyKey": publication_operation_key(
                source_workflow_id="source",
                source_run_id="source-run",
                publication_kind="pull_request",
                repository="org/repo",
                head_ref="candidate",
                base_ref="main",
            ),
            "candidateRef": "artifact://candidate",
            **(
                {"verifiedRemoteCandidateRef": "artifact://remote"}
                if remote
                else {
                    "beforePublicationCheckpointRef": "artifact://checkpoint"
                }
            ),
            "expectedHeadSha": "a" * 40,
            "expectedTreeDigest": "sha256:" + "b" * 64,
            "expectedDiffDigest": "sha256:" + "c" * 64,
            "priorObservationsRef": "artifact://observations",
        },
        "intent": {
            "repository": "org/repo",
            "baseRef": "main",
            "headRef": "candidate",
            "mode": "pr",
            "branchPolicy": "reuse_exact_head",
            "githubAuthorityRef": "managed-secret://github",
        },
        "candidateAccepted": True,
        "candidateContaminated": False,
        "hasPublishableChange": True,
        "publicationAuthorityCurrent": True,
        "incompleteDraftAuthorized": False,
    }


@pytest.mark.asyncio
async def test_workflow_runs_only_publication_phases_and_restores_exact_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def execute_activity(
        name: str, payload: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(name)
        if name == "publication_recovery.observe":
            return {
                "authoritative": True,
                "authorityAvailable": True,
                "remoteBranchExists": False,
                "pullRequestExists": False,
            }
        if name == "publication_recovery.restore_candidate":
            return {
                "destinationWorkspaceLocator": {"agentRunId": "destination"},
                "headSha": "a" * 40,
                "treeDigest": "sha256:" + "b" * 64,
                "diffDigest": "sha256:" + "c" * 64,
                "restorationEvidenceRef": "artifact://restoration",
            }
        if name == "publication_recovery.publish":
            return {"pullRequestUrl": "https://github.com/org/repo/pull/1"}
        if name == "publication_recovery.verify":
            return {"evidenceRef": "artifact://verified"}
        if name == "publication_recovery.persist_result":
            return {"resultRef": "artifact://result", "status": "completed"}
        if name == "publication_recovery.cleanup":
            return {"cleaned": True}
        raise AssertionError(name)

    monkeypatch.setattr(workflow_module.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(
        workflow_module.workflow,
        "info",
        lambda: SimpleNamespace(workflow_id="destination", run_id="run"),
    )

    result = await MoonMindPublicationRecoveryWorkflow().run(_contract())

    assert result == {"resultRef": "artifact://result", "status": "completed"}
    assert calls == [
        "publication_recovery.observe",
        "publication_recovery.restore_candidate",
        "publication_recovery.publish",
        "publication_recovery.verify",
        "publication_recovery.persist_result",
        "publication_recovery.cleanup",
    ]
    assert not any("implementation" in name or "verifier" in name for name in calls)


@pytest.mark.asyncio
async def test_matching_existing_pr_reconciles_without_restore_or_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def execute_activity(
        name: str, payload: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(name)
        if name == "publication_recovery.observe":
            return {
                "authoritative": True,
                "authorityAvailable": True,
                "remoteBranchExists": True,
                "remoteHeadSha": "a" * 40,
                "pullRequestExists": True,
                "pullRequestUrl": "https://github.com/org/repo/pull/1",
                "pullRequestHeadRef": "candidate",
                "pullRequestBaseRef": "main",
                "pullRequestHeadSha": "a" * 40,
                "pullRequestDraft": False,
            }
        if name == "publication_recovery.verify":
            return {"evidenceRef": "artifact://verified"}
        if name == "publication_recovery.persist_result":
            return {"status": "reconciled"}
        if name == "publication_recovery.cleanup":
            return {"cleaned": True}
        raise AssertionError(name)

    monkeypatch.setattr(workflow_module.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(
        workflow_module.workflow,
        "info",
        lambda: SimpleNamespace(workflow_id="destination", run_id="run"),
    )

    result = await MoonMindPublicationRecoveryWorkflow().run(_contract(remote=True))

    assert result == {"status": "reconciled"}
    assert calls == [
        "publication_recovery.observe",
        "publication_recovery.verify",
        "publication_recovery.persist_result",
        "publication_recovery.cleanup",
    ]
