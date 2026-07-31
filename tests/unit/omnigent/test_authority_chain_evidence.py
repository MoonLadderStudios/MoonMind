"""Unit tests for the bounded Omnigent authority-chain evidence projection.

Tracks MoonLadderStudios/MoonMind#3561: the unified workspace -> runtime ->
publication -> terminal -> cleanup -> lease authority chain must be complete,
compact, credential-free, and total (never raising on partial input).
"""

from __future__ import annotations

from moonmind.omnigent.authority_chain import (
    AUTHORITY_CHAIN_SCHEMA_VERSION,
    build_omnigent_authority_chain_evidence,
)


def _effective_launch() -> dict:
    return {
        "hostMode": "on_demand_docker",
        "snapshotRef": "omnigent-launch:sha256:" + "0" * 64,
        "executionProfileRef": "omnigent-codex@1",
        "launchPolicyRef": "codex-on-demand@1",
        "endpointRef": "default",
        "providerProfileId": "codex",
        "mountClasses": ["workspace", "oauth_home", "skills_tools"],
        "controlCapabilities": ["interrupt", "terminate", "clear_context"],
        "repositoryMutation": True,
        "policyAuthority": {"policyRef": "codex-on-demand@1"},
    }


def _workspace_resolution() -> dict:
    return {
        "locatorKind": "sandbox",
        "workspaceId": "ws-abc123",
        "relativePath": "repo",
        "identityVerified": True,
        "materialization": {
            "action": "materialized",
            "sourceKind": "github_https",
            "startingBranch": "main",
            "checkedOut": "main",
            "outputBranch": "agent/impl",
            "restoreInputs": [{"ref": "artifact://restore-1", "bytes": 12}],
        },
    }


def test_authority_chain_assembles_complete_bounded_projection() -> None:
    evidence = build_omnigent_authority_chain_evidence(
        effective_launch=_effective_launch(),
        workspace_resolution=_workspace_resolution(),
        repository="owner/repo",
        source_branch="main",
        output_branch="agent/impl",
        publish_mode="branch",
        required_capabilities=["gh", "git"],
        repository_mutation_required=True,
        github_mutation_required=True,
        profile_authorization={
            "providerProfileId": "codex",
            "providerLeaseRef": "provider-lease-1",
            "hostBindingRef": "omnigent-oauth:codex",
            "hostLeaseRef": "host-lease-1",
            "endpointRef": "default",
            "omnigentHostId": "host-1",
            "credentialGeneration": 3,
            "bridgeSessionId": "bridge-1",
        },
        result_output_refs=["artifact://out-1", "artifact://out-2"],
        result_metadata={
            "pushRef": "artifact://push-1",
            "pullRequestUrl": "https://github.com/owner/repo/pull/7",
            "resourceManifestRef": "artifact://resources",
        },
        terminal_status="completed",
        cleanup_mode="on_demand_remove",
        cleanup_completed=True,
        lease_released=True,
        janitor_required=False,
        release_ordering=[
            "host_cleanup_completed",
            "provider_lease_released",
            "terminal",
        ],
        reasons=[],
    )

    assert evidence["schemaVersion"] == AUTHORITY_CHAIN_SCHEMA_VERSION
    # Workspace intent + materialization survive.
    ws = evidence["workspace"]
    assert ws["locatorKind"] == "sandbox"
    assert ws["repository"] == "owner/repo"
    assert ws["sourceBranch"] == "main"
    assert ws["sourceCommit"] == "main"
    assert ws["candidateHead"] == "agent/impl"
    assert ws["materializationAction"] == "materialized"
    assert ws["restoreInputRefs"] == ["artifact://restore-1"]
    # Runtime authority refs + mount/capability classes.
    rt = evidence["runtime"]
    assert rt["hostMode"] == "on_demand_docker"
    assert rt["effectiveLaunchRef"].startswith("omnigent-launch:sha256:")
    assert rt["hostLeaseRef"] == "host-lease-1"
    assert rt["bridgeSessionId"] == "bridge-1"
    assert rt["mountClasses"] == ["workspace", "oauth_home", "skills_tools"]
    assert rt["capabilityClasses"] == ["gh", "git"]
    # Publication policy + declared outputs + downstream evidence refs.
    pub = evidence["publication"]
    assert pub["publishMode"] == "branch"
    assert pub["outputBranch"] == "agent/impl"
    assert pub["repositoryMutationAuthorized"] is True
    assert pub["publicationState"] == "authorized_pending_publication"
    assert pub["declaredOutputRefs"] == ["artifact://out-1", "artifact://out-2"]
    assert pub["evidenceRefs"]["pushRef"] == "artifact://push-1"
    assert pub["evidenceRefs"]["resourceManifestRef"] == "artifact://resources"
    # Terminal harvest + cleanup + release ordering.
    term = evidence["terminal"]
    assert term["harvestState"] == "completed"
    assert term["cleanupMode"] == "on_demand_remove"
    assert term["cleanupCompleted"] is True
    assert term["leaseReleased"] is True
    assert term["janitorRequired"] is False
    assert term["releaseOrdering"][-1] == "terminal"


def test_authority_chain_classifies_no_publish_saved_work() -> None:
    evidence = build_omnigent_authority_chain_evidence(
        effective_launch=_effective_launch(),
        workspace_resolution=_workspace_resolution(),
        repository="owner/repo",
        source_branch="main",
        output_branch="agent/impl",
        publish_mode="none",
        required_capabilities=[],
        repository_mutation_required=True,
        terminal_status="completed",
    )
    assert (
        evidence["publication"]["publicationState"]
        == "unpublished_saved_work_eligible"
    )


def test_authority_chain_classifies_read_only_run() -> None:
    evidence = build_omnigent_authority_chain_evidence(
        effective_launch=_effective_launch(),
        workspace_resolution=_workspace_resolution(),
        repository="owner/repo",
        source_branch="main",
        output_branch=None,
        publish_mode="none",
        required_capabilities=[],
        repository_mutation_required=False,
        terminal_status="completed",
    )
    assert evidence["publication"]["publicationState"] == "read_only_no_publication"


def test_authority_chain_records_typed_failure_reasons() -> None:
    evidence = build_omnigent_authority_chain_evidence(
        effective_launch=_effective_launch(),
        workspace_resolution=None,
        repository="owner/repo",
        source_branch="main",
        output_branch="agent/impl",
        publish_mode="branch",
        repository_mutation_required=True,
        terminal_status="failed",
        cleanup_completed=False,
        lease_released=False,
        janitor_required=True,
        release_ordering=["host_cleanup_incomplete", "terminal"],
        reasons=[
            {
                "stage": "container_start",
                "code": "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED",
                "failureClass": "configuration_error",
                "remediationAction": "repair_host_image",
            }
        ],
    )
    assert evidence["publication"]["publicationState"] == "not_published_failed_run"
    assert evidence["terminal"]["janitorRequired"] is True
    assert evidence["reasons"][0]["code"] == (
        "OMNIGENT_WORKSPACE_MATERIALIZATION_FAILED"
    )
    assert evidence["reasons"][0]["failureClass"] == "configuration_error"


def test_authority_chain_scrubs_credential_like_leaks() -> None:
    """A credential that leaked into an upstream evidence value is scrubbed.

    The projection is credential-free by contract; the secret scan is the
    fail-closed backstop when an upstream value is not.
    """

    evidence = build_omnigent_authority_chain_evidence(
        effective_launch=_effective_launch(),
        workspace_resolution=_workspace_resolution(),
        repository="owner/repo",
        source_branch="main",
        output_branch="agent/impl",
        publish_mode="branch",
        repository_mutation_required=True,
        result_metadata={
            # A non-ref key is not surfaced at all, and the recursive scrub
            # ensures no bearer/token material appears anywhere in the output.
            "authorization": "Bearer sk-supersecrettoken",
            "ghToken": "ghp_" + "a" * 36,
        },
        terminal_status="completed",
    )
    flattened = repr(evidence)
    assert "supersecrettoken" not in flattened
    assert "ghp_" not in flattened
    # None of the non-allowlisted credential keys leak into evidence refs.
    assert evidence["publication"]["evidenceRefs"] == {}


def test_authority_chain_is_total_on_empty_input() -> None:
    evidence = build_omnigent_authority_chain_evidence(
        effective_launch=None,
        workspace_resolution=None,
        repository=None,
        source_branch=None,
        output_branch=None,
        publish_mode=None,
    )
    assert evidence["schemaVersion"] == AUTHORITY_CHAIN_SCHEMA_VERSION
    assert evidence["publication"]["publishMode"] == "none"
    assert evidence["workspace"]["locatorKind"] is None
    assert evidence["reasons"] == []
