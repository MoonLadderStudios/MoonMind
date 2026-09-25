"""Producer-to-consumer publication policy coverage for #1090.

Uses the existing publication contract, Skill consumers, and publisher:
explicit None is never promoted to Auto, omission may use the admitted Skill
default, Skill-owned Auto never grants the
parent a second push/PR, aliases resolve once at the ingress boundary, each
phase reconciles before retry, and only the unfinished effect retries with the
same operation identity and lease-protected push.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from moonmind.omnigent.workspace_publication import (
    OmnigentWorkspacePublicationService,
    branch_publish_mode_for_destination,
    compile_branch_publish_mode,
)
from moonmind.publish.service import PublishService
from moonmind.workflows.executions.execution_contract import (
    WorkflowContractError,
    resolve_publish_mode_for_skill,
)
from moonmind.workflows.temporal.publication_recovery import (
    PublicationObservation,
    PublicationRecoveryContract,
    PublicationRecoveryEvidence,
    PublicationRecoveryError,
    publication_operation_key,
    reconcile_publication_state,
    validate_restored_candidate,
)

AUTO_AGENT_METADATA = {"mode": "auto", "owner": "agent", "requiresEvidence": True}


def _contract_kwargs(**overrides):
    key = publication_operation_key(
        source_workflow_id="mm:source",
        source_run_id="run-source",
        publication_kind="pull_request",
        repository="MoonLadderStudios/MoonMind",
        head_ref="issue-1090",
        base_ref="main",
    )
    payload = {
        "sourceWorkflowId": "mm:source",
        "sourceRunId": "run-source",
        "sourceSemanticOutcome": "accepted",
        "target": {
            "kind": "publication",
            "publicationKind": "pull_request",
            "sourcePublicationOperationId": "publish-source",
            "semanticContext": "accepted",
        },
        "continuation": {
            "phase": "resume_publication",
            "publicationIdempotencyKey": key,
            "candidateRef": "artifact://candidate/accepted",
            "beforePublicationCheckpointRef": "artifact://checkpoint/before",
            "expectedHeadSha": "a" * 40,
            "expectedTreeDigest": "sha256:" + "b" * 64,
            "expectedDiffDigest": "sha256:" + "c" * 64,
            "priorObservationsRef": "artifact://github/observations",
            "secretScanRef": "artifact://scan/clean",
            "diagnosticsRef": "artifact://diagnostics/publication",
        },
        "intent": {
            "repository": "MoonLadderStudios/MoonMind",
            "baseRef": "main",
            "headRef": "issue-1090",
            "mode": "pr",
            "branchPolicy": "reuse_exact_head",
            "githubAuthorityRef": "managed-secret://github/source",
        },
        "candidateAccepted": True,
        "candidateContaminated": False,
        "hasPublishableChange": True,
        "publicationAuthorityCurrent": True,
        "incompleteDraftAuthorized": False,
    }
    payload.update(overrides)
    return payload


def test_explicit_none_stays_none_omitted_may_use_skill_default() -> None:
    # Explicit "none" stays read-only through the existing Skill contract.
    assert resolve_publish_mode_for_skill("plain-skill", "none") == "none"
    with pytest.raises(WorkflowContractError):
        resolve_publish_mode_for_skill("plain-skill", "auto")
    # Omission resolves through the admitted default, not a silent None. For a
    # non-repository Skill the admitted default stays read-only.
    assert resolve_publish_mode_for_skill("batch-workflows", None) == "none"
    assert resolve_publish_mode_for_skill("batch-workflows", "none") == "none"


def test_auto_capable_skill_omission_uses_default_without_silent_rewrite() -> None:
    assert (
        resolve_publish_mode_for_skill(
            "some-skill",
            None,
            publish_metadata=AUTO_AGENT_METADATA,
        )
        == "auto"
    )
    # Explicit None for an auto-capable skill is never promoted to Auto
    # (docs/Workflows/WorkflowPublishing.md PUBLISH-004). The operator's
    # read-only selection is preserved instead of granting push or merge
    # effects.
    assert (
        resolve_publish_mode_for_skill(
            "some-skill",
            "none",
            publish_metadata=AUTO_AGENT_METADATA,
            diagnostics=[],
        )
        == "none"
    )
    # Skill-owned Auto is not permission for the parent to push again.
    with pytest.raises(WorkflowContractError):
        resolve_publish_mode_for_skill(
            "some-skill", "branch", publish_metadata=AUTO_AGENT_METADATA
        )
    assert compile_branch_publish_mode("auto") == "none"
    assert branch_publish_mode_for_destination("none") == "none"


def test_aliases_resolve_once_then_translate_to_destination() -> None:
    for raw in ("pr", "PR", "pull-request", "pullrequest", "pull_request"):
        assert compile_branch_publish_mode(raw) == "pull_request"
    # The shared publisher acts on {"branch", "pr"}; translation happens once at
    # the handoff so an authorized PR intent is not skipped downstream.
    assert branch_publish_mode_for_destination("pull_request") == "pr"
    assert branch_publish_mode_for_destination("branch") == "branch"


@pytest.mark.asyncio
async def test_parent_with_none_or_auto_performs_no_push() -> None:
    service = OmnigentWorkspacePublicationService("/tmp")
    for mode in ("none", "auto"):
        result = await service.publish_workspace(
            workspace_locator={"kind": "sandbox", "workspaceId": "x", "relativePath": "repo"},
            current_workflow_id="wf",
            current_step_execution_id="step",
            publication_identity="id-1090",
            publish_mode=mode,
            base_branch="main",
            repository="example/repository",
            github_token=None,
        )
        assert result == {"push_status": "skipped"}


def test_matching_pr_reuses_owned_result_without_mutation() -> None:
    contract = PublicationRecoveryContract.model_validate(_contract_kwargs())
    decision = reconcile_publication_state(
        contract,
        PublicationObservation(
            authoritative=True,
            authorityAvailable=True,
            remoteBranchExists=True,
            remoteHeadSha="a" * 40,
            pullRequestExists=True,
            pullRequestUrl="https://github.com/org/repo/pull/1",
            pullRequestHeadRef="issue-1090",
            pullRequestBaseRef="main",
            pullRequestHeadSha="a" * 40,
            pullRequestDraft=False,
        ),
    )
    assert decision.outcome == "already_completed"
    assert decision.mutation_allowed is False
    assert decision.existing_pull_request_url == "https://github.com/org/repo/pull/1"


def test_unfinished_pr_create_retries_after_push_success() -> None:
    contract = PublicationRecoveryContract.model_validate(_contract_kwargs())
    decision = reconcile_publication_state(
        contract,
        PublicationObservation(
            authoritative=True,
            authorityAvailable=True,
            remoteBranchExists=True,
            remoteHeadSha="a" * 40,
        ),
    )
    assert decision.outcome == "safe_to_retry"
    assert decision.reason_code == "matching_remote_head_reconciled"
    assert decision.mutation_allowed is True


@pytest.mark.parametrize(
    ("observation", "outcome", "reason"),
    [
        (
            {
                "authoritative": True,
                "authorityAvailable": True,
                "remoteBranchExists": True,
                "remoteHeadSha": "f" * 40,
            },
            "conflict",
            "remote_head_mismatch",
        ),
        (
            {
                "authoritative": True,
                "authorityAvailable": False,
            },
            "conflict",
            "publication_authority_unavailable",
        ),
        (
            {
                "authoritative": False,
                "authorityAvailable": True,
                "transientAbsenceOnly": True,
            },
            "ambiguous",
            "publication_observation_not_authoritative",
        ),
        (
            {
                "authoritative": True,
                "authorityAvailable": True,
                "conflictingEvidence": True,
            },
            "conflict",
            "conflicting_publication_evidence",
        ),
    ],
)
def test_bounded_outcomes_stay_truthful(observation, outcome, reason) -> None:
    contract = PublicationRecoveryContract.model_validate(_contract_kwargs())
    decision = reconcile_publication_state(
        contract, PublicationObservation.model_validate(observation)
    )
    assert decision.outcome == outcome
    assert decision.reason_code == reason
    assert decision.mutation_allowed is False


def test_mismatched_pr_identity_is_conflict_not_overwrite() -> None:
    contract = PublicationRecoveryContract.model_validate(_contract_kwargs())
    decision = reconcile_publication_state(
        contract,
        PublicationObservation(
            authoritative=True,
            authorityAvailable=True,
            remoteBranchExists=True,
            remoteHeadSha="a" * 40,
            pullRequestExists=True,
            pullRequestUrl="https://github.com/org/repo/pull/9",
            pullRequestHeadRef="other-branch",
            pullRequestBaseRef="main",
            pullRequestHeadSha="a" * 40,
            pullRequestDraft=False,
        ),
    )
    assert decision.outcome == "conflict"
    assert decision.reason_code == "pull_request_identity_mismatch"
    assert decision.mutation_allowed is False


def test_retry_keeps_same_operation_identity() -> None:
    common = {
        "source_workflow_id": "mm:source",
        "source_run_id": "run-source",
        "publication_kind": "pull_request",
        "repository": "MoonLadderStudios/MoonMind",
        "head_ref": "issue-1090",
        "base_ref": "main",
    }
    assert publication_operation_key(**common) == publication_operation_key(**common)
    changed = dict(common, head_ref="other")
    assert publication_operation_key(**changed) != publication_operation_key(**common)


def _fake_git(head_sha: str, remote_sha: str):
    calls: list[list[str]] = []

    async def run_command(command, *, cwd=None, check=True, env=None, **kwargs):
        args = [str(part) for part in command]
        calls.append(args)
        if args[:2] == ["git", "status"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if args[:3] == ["git", "rev-list", "--count"]:
            return SimpleNamespace(stdout="1\n", stderr="", returncode=0)
        if args[:2] == ["git", "ls-remote"]:
            return SimpleNamespace(stdout=f"{remote_sha}\trefs/heads/candidate\n", stderr="", returncode=0)
        if args[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(stdout=f"{head_sha}\n", stderr="", returncode=0)
        if args[:2] == ["git", "push"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    return run_command, calls


@pytest.mark.asyncio
async def test_push_uses_lease_and_verifies_exact_head(tmp_path) -> None:
    import subprocess

    subprocess.run(["git", "init", "--initial-branch=main"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "work.txt").write_text("work\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "work"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()

    run_command, calls = _fake_git(head_sha, head_sha)
    result = await PublishService().publish(
        job_id=uuid4(),
        instruction="Publish completed Omnigent repository work",
        publish_mode="branch",
        publish_base_branch="main",
        publication_branch_name="candidate",
        runtime_mode="omnigent",
        repo_dir=tmp_path,
        run_command=run_command,
        publish_existing_commits=True,
        verify_remote=True,
    )
    assert result is not None and result.remote_verified is True
    push = next(call for call in calls if call[:2] == ["git", "push"])
    assert any(part.startswith("--force-with-lease=") for part in push)
    assert "--force" not in push


@pytest.mark.asyncio
async def test_changed_remote_head_never_force_pushed(tmp_path) -> None:
    import subprocess

    subprocess.run(["git", "init", "--initial-branch=main"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "work.txt").write_text("work\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "work"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()

    run_command, _ = _fake_git(head_sha, "f" * 40)
    with pytest.raises(RuntimeError, match="exact remote-head verification"):
        await PublishService().publish(
            job_id=uuid4(),
            instruction="Publish completed Omnigent repository work",
            publish_mode="branch",
            publish_base_branch="main",
            publication_branch_name="candidate",
            runtime_mode="omnigent",
            repo_dir=tmp_path,
            run_command=run_command,
            publish_existing_commits=True,
            verify_remote=True,
        )


def test_immutable_candidate_survives_lost_ack_without_rerun() -> None:
    contract = PublicationRecoveryContract.model_validate(_contract_kwargs())
    evidence = PublicationRecoveryEvidence(
        sourceWorkflowId=contract.source_workflow_id,
        sourceRunId=contract.source_run_id,
        destinationWorkflowId="mm:publication",
        publicationIdempotencyKey=contract.continuation.publication_idempotency_key,
        reconciliationOutcome="already_completed",
        publicationOutcome="reconciled",
        expectedHeadSha="a" * 40,
        observedHeadSha="a" * 40,
        expectedTreeDigest=contract.continuation.expected_tree_digest,
        observedTreeDigest=contract.continuation.expected_tree_digest,
        expectedDiffDigest=contract.continuation.expected_diff_digest,
        observedDiffDigest=contract.continuation.expected_diff_digest,
        repository=contract.intent.repository,
        baseRef=contract.intent.base_ref,
        headRef=contract.intent.head_ref,
        pullRequestUrl="https://github.com/org/repo/pull/1",
        pullRequestDraft=False,
        githubAuthorityRef=contract.intent.github_authority_ref,
        secretScanRef="artifact://scan/clean",
        diagnosticsRef="artifact://diagnostics/publication",
        publicationObservationsRef="artifact://github/observations",
        sourceSemanticOutcome=contract.source_semantic_outcome,
        semanticContext=contract.target.semantic_context,
    )
    assert evidence.implementation_rerun is False
    assert evidence.verification_rerun is False

    restoration = {
        "destinationWorkspaceLocator": {"kind": "managed", "agentRunId": "dest"},
        "headSha": "a" * 40,
        "treeDigest": contract.continuation.expected_tree_digest,
        "diffDigest": contract.continuation.expected_diff_digest,
        "restorationEvidenceRef": "artifact://restore/evidence",
    }
    assert validate_restored_candidate(contract, restoration) == "artifact://restore/evidence"
    stale = dict(restoration, headSha="f" * 40)
    with pytest.raises(PublicationRecoveryError) as exc_info:
        validate_restored_candidate(contract, stale)
    assert exc_info.value.code == "PUBLICATION_CANDIDATE_MISMATCH"
