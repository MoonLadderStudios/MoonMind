"""Bounded, credential-free authority-chain evidence for the Omnigent path.

Tracks MoonLadderStudios/MoonMind#3561. The profile-bound Omnigent coordinator
already emits per-stage lifecycle events (workspace resolution, credential mount,
host registration, resource harvest, host cleanup, provider-lease release,
terminal). Those are individually useful but do not, on their own, expose the
single unified workspace -> runtime -> publication -> terminal -> cleanup ->
lease authority chain that Workflow Detail and the protected release matrix need.

This module assembles that one bounded projection from evidence the coordinator
already holds. It is intentionally a pure, total function: it never raises, never
carries credentials or raw daemon paths, and never reaches back into provider
internals. The result is nested under a single ``authorityChain`` lifecycle key so
it survives the bridge-store metadata allowlist intact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from moonmind.utils.logging import redact_sensitive_payload

AUTHORITY_CHAIN_SCHEMA_VERSION = "omnigent-authority-chain-v1"

# Keep the projection compact so a large launch snapshot or output-ref list cannot
# inflate the lifecycle journal past the compact-history policy.
_MAX_STRING_CHARS = 512
_MAX_REF_LIST = 32
_MAX_REASONS = 20
_MAX_CLASSES = 32

# Result metadata keys that carry durable, non-sensitive publication/saved-work
# evidence references produced downstream. They are surfaced as refs only.
_PUBLICATION_REF_KEYS = (
    "commitRef",
    "pushRef",
    "pullRequestRef",
    "pullRequestUrl",
    "savedWorkRef",
    "terminalCheckpointRef",
    "publicationRecoveryRef",
    "resourceManifestRef",
    "captureManifestRef",
)


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    return text[:_MAX_STRING_CHARS]


def _ref_list(values: Any) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    out: list[str] = []
    for value in values:
        text = _text(value)
        if text is not None and text not in out:
            out.append(text)
        if len(out) >= _MAX_REF_LIST:
            break
    return out


def _class_list(values: Any) -> list[str]:
    return _ref_list(values)[:_MAX_CLASSES]


def _publication_state(
    *, publish_mode: str, repository_mutation: bool, terminal_status: str
) -> str:
    """Classify the publication disposition MoonMind owns for this run.

    The coordinator does not itself push; the actual commit/push/PR side effects
    are performed by the shared publication path after the run result returns.
    This records the disposition the Omnigent execution boundary authorized so the
    downstream side-effect evidence can be reconciled against it.
    """

    normalized = (publish_mode or "none").strip().lower()
    if terminal_status == "failed":
        return "not_published_failed_run"
    if normalized in {"", "none"}:
        return (
            "unpublished_saved_work_eligible"
            if repository_mutation
            else "read_only_no_publication"
        )
    return "authorized_pending_publication"


def build_omnigent_authority_chain_evidence(
    *,
    effective_launch: Mapping[str, Any] | None,
    workspace_resolution: Mapping[str, Any] | None,
    repository: str | None,
    source_branch: str | None,
    output_branch: str | None,
    publish_mode: str | None,
    required_capabilities: Sequence[str] = (),
    repository_mutation_required: bool = False,
    github_mutation_required: bool = False,
    profile_authorization: Mapping[str, Any] | None = None,
    result_output_refs: Sequence[str] = (),
    result_metadata: Mapping[str, Any] | None = None,
    terminal_status: str = "completed",
    cleanup_mode: str | None = None,
    cleanup_completed: bool = False,
    lease_released: bool = False,
    janitor_required: bool = False,
    release_ordering: Sequence[str] = (),
    reasons: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Assemble the unified bounded authority-chain projection.

    Every field is a compact scalar, ref, or ref-list; the entire payload is run
    through :func:`redact_sensitive_payload` before returning so a token, header,
    or auth path that slipped into any upstream evidence value is scrubbed rather
    than published. The function is total: missing inputs yield partial evidence,
    never an exception, so best-effort evidence emission can never break a run.
    """

    launch = effective_launch if isinstance(effective_launch, Mapping) else {}
    workspace = (
        workspace_resolution if isinstance(workspace_resolution, Mapping) else {}
    )
    authorization = (
        profile_authorization if isinstance(profile_authorization, Mapping) else {}
    )
    metadata = result_metadata if isinstance(result_metadata, Mapping) else {}
    materialization = (
        workspace.get("materialization")
        if isinstance(workspace.get("materialization"), Mapping)
        else {}
    )

    workspace_section = {
        "locatorKind": _text(workspace.get("locatorKind")),
        "workspaceId": _text(workspace.get("workspaceId")),
        "relativePath": _text(workspace.get("relativePath")),
        "identityVerified": bool(workspace.get("identityVerified"))
        if workspace.get("identityVerified") is not None
        else None,
        "repository": _text(repository),
        "sourceBranch": _text(source_branch or materialization.get("startingBranch")),
        "sourceCommit": _text(materialization.get("checkedOut")),
        "candidateHead": _text(
            materialization.get("outputBranch") or output_branch
        ),
        "materializationAction": _text(materialization.get("action")),
        "sourceKind": _text(materialization.get("sourceKind")),
        "restoreInputRefs": _ref_list(
            [
                entry.get("ref") if isinstance(entry, Mapping) else entry
                for entry in (materialization.get("restoreInputs") or [])
            ]
        ),
    }

    runtime_section = {
        "hostMode": _text(launch.get("hostMode")),
        "effectiveLaunchRef": _text(launch.get("snapshotRef"))
        or _text(authorization.get("effectiveLaunchRef")),
        "executionProfileRef": _text(launch.get("executionProfileRef")),
        "launchPolicyRef": _text(launch.get("launchPolicyRef")),
        "policyRef": _text(
            (launch.get("policyAuthority") or {}).get("policyRef")
            if isinstance(launch.get("policyAuthority"), Mapping)
            else None
        ),
        "endpointRef": _text(
            launch.get("endpointRef") or authorization.get("endpointRef")
        ),
        "providerProfileId": _text(
            launch.get("providerProfileId")
            or authorization.get("providerProfileId")
        ),
        "credentialGeneration": authorization.get("credentialGeneration")
        if isinstance(authorization.get("credentialGeneration"), int)
        else None,
        "providerLeaseRef": _text(authorization.get("providerLeaseRef")),
        "hostBindingRef": _text(authorization.get("hostBindingRef")),
        "hostLeaseRef": _text(authorization.get("hostLeaseRef")),
        "omnigentHostId": _text(authorization.get("omnigentHostId")),
        "bridgeSessionId": _text(authorization.get("bridgeSessionId")),
        "mountClasses": _class_list(launch.get("mountClasses")),
        "capabilityClasses": _class_list(required_capabilities),
        "controlCapabilities": _class_list(launch.get("controlCapabilities")),
    }

    publication_refs: dict[str, Any] = {}
    for key in _PUBLICATION_REF_KEYS:
        ref = _text(metadata.get(key))
        if ref is not None:
            publication_refs[key] = ref

    publication_section = {
        "publishMode": (publish_mode or "none").strip().lower() or "none",
        "outputBranch": _text(output_branch),
        "repositoryMutationAuthorized": bool(
            repository_mutation_required
            or (isinstance(launch.get("repositoryMutation"), bool)
                and launch.get("repositoryMutation"))
        ),
        "githubMutationRequired": bool(github_mutation_required),
        "publicationState": _publication_state(
            publish_mode=publish_mode or "none",
            repository_mutation=bool(repository_mutation_required),
            terminal_status=terminal_status,
        ),
        "declaredOutputRefs": _ref_list(result_output_refs),
        "evidenceRefs": publication_refs,
    }

    terminal_section = {
        "harvestState": terminal_status,
        "cleanupMode": _text(cleanup_mode),
        "cleanupCompleted": bool(cleanup_completed),
        "leaseReleased": bool(lease_released),
        "janitorRequired": bool(janitor_required),
        "releaseOrdering": _ref_list(release_ordering),
    }

    bounded_reasons: list[dict[str, Any]] = []
    for reason in reasons:
        if not isinstance(reason, Mapping):
            continue
        bounded_reasons.append(
            {
                "stage": _text(reason.get("stage")),
                "code": _text(reason.get("code")),
                "failureClass": _text(reason.get("failureClass")),
                "remediationAction": _text(reason.get("remediationAction")),
            }
        )
        if len(bounded_reasons) >= _MAX_REASONS:
            break

    evidence = {
        "schemaVersion": AUTHORITY_CHAIN_SCHEMA_VERSION,
        "workspace": workspace_section,
        "runtime": runtime_section,
        "publication": publication_section,
        "terminal": terminal_section,
        "reasons": bounded_reasons,
    }
    # Fail-closed secret hygiene: scrub the whole projection so any credential,
    # authorization header, or auth path that leaked into an upstream evidence
    # value is redacted rather than surfaced through Workflow Detail.
    scrubbed = redact_sensitive_payload(evidence)
    return scrubbed if isinstance(scrubbed, dict) else dict(evidence)


__all__ = [
    "AUTHORITY_CHAIN_SCHEMA_VERSION",
    "build_omnigent_authority_chain_evidence",
]
