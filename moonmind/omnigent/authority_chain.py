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
from urllib.parse import urlsplit, urlunsplit

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

# The subset of publication refs that constitute realized terminal evidence the
# publication owner produces after the run: a recorded commit, a verified push,
# or a created pull request. Their presence reconciles the authorized
# pre-publication snapshot into a published disposition.
_TERMINAL_PUBLICATION_EVIDENCE_KEYS = (
    "commitRef",
    "pushRef",
    "pullRequestRef",
    "pullRequestUrl",
)


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    return text[:_MAX_STRING_CHARS]


def _repository_identity(value: Any) -> str | None:
    """Project a credential-free canonical repository identity.

    An authored repository source may embed URL userinfo, for example
    ``https://alice:token@github.com/org/repo.git`` or an scp-style
    ``user:token@github.com:org/repo.git``. ``redact_sensitive_payload`` does not
    recognize userinfo under the ``repository`` key, so the username/password
    would otherwise persist verbatim in the durable lifecycle journal. Strip the
    userinfo here so only the credential-free host/path identity is recorded.
    """

    text = _text(value)
    if text is None:
        return None
    # scheme://[userinfo@]host/path — drop the userinfo component entirely.
    split = urlsplit(text)
    if split.scheme and split.netloc and "@" in split.netloc:
        host = split.hostname or ""
        if split.port:
            host = f"{host}:{split.port}"
        return _text(
            urlunsplit(
                (split.scheme, host, split.path, split.query, split.fragment)
            )
        )
    # scp-style ``[user[:pass]@]host:path`` (no scheme). Only strip userinfo that
    # carries an embedded credential (``user:pass@``); a bare ``git@`` username is
    # a conventional, non-sensitive identity and is preserved.
    if "://" not in text and "@" in text:
        userinfo, _, remainder = text.partition("@")
        if ":" in userinfo and "/" not in userinfo:
            return _text(remainder)
    return text


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
    *,
    publish_mode: str,
    repository_mutation: bool,
    terminal_status: str,
    publication_evidence_present: bool = False,
) -> str:
    """Classify the publication disposition MoonMind owns for this run.

    The coordinator invokes the shared runtime publication boundary before host
    cleanup so publication failures remain retryable lifecycle evidence. Until
    that owner produces terminal evidence, the projection records the disposition
    the Omnigent execution boundary *authorized*
    (``authorized_pending_publication``). Once realized commit/push/PR evidence is
    present in the result metadata, the state is reconciled to ``published`` so the
    chain reflects the publication owner's terminal outcome rather than a stale
    pre-publication snapshot.
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
    if publication_evidence_present:
        return "published"
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
    egress_attestation: Mapping[str, Any] | None = None,
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
    egress = egress_attestation if isinstance(egress_attestation, Mapping) else {}
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
        "repository": _repository_identity(repository),
        "sourceBranch": _text(source_branch or materialization.get("startingBranch")),
        # Prefer the immutable resolved revision (``git rev-parse HEAD`` captured at
        # materialization) so the authority evidence proves which source state ran,
        # not a movable branch ref. Fall back to ``checkedOut`` only when a resolved
        # commit is unavailable (for example an explicit detached-commit checkout).
        "sourceCommit": _text(
            materialization.get("resolvedCommit")
            or materialization.get("checkedOut")
        ),
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
        "egress": {
            "profileRef": _text(egress.get("profileRef")),
            "profileDigest": _text(egress.get("profileDigest")),
            "backendRef": _text(egress.get("backendRef")),
            "enforcerImplementation": _text(
                egress.get("enforcerImplementation")
            ),
            "networkRef": _text(egress.get("networkRef")),
            "gatewayRef": _text(egress.get("gatewayRef")),
            "appliedRuleDigest": _text(egress.get("appliedRuleDigest")),
            "attachmentRef": _text(egress.get("attachmentRef")),
            "validationState": _text(
                egress.get("validationState") or egress.get("validationResult")
            ),
            "validatedAt": _text(egress.get("validatedAt")),
        },
    }

    publication_refs: dict[str, Any] = {}
    for key in _PUBLICATION_REF_KEYS:
        ref = _text(metadata.get(key))
        if ref is not None:
            publication_refs[key] = ref
    publication_evidence_present = any(
        key in publication_refs for key in _TERMINAL_PUBLICATION_EVIDENCE_KEYS
    )

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
            publication_evidence_present=publication_evidence_present,
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
