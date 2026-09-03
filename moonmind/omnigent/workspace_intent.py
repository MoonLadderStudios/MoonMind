"""The single workspace-intent compiler for normal Workflow submissions.

Create, edit, rerun/edit-for-rerun, schedule/recurring, and preset-expanded
authoring surfaces all converge on one ``AgentExecutionRequest``. This module is
the one place that reads the authored request and compiles it into a durable,
versioned :class:`WorkspaceIntentRecord` — before an Omnigent host or Docker
runtime is selected or mutated. The extraction helpers here are the canonical
readers for authored repository/branch/restore/capability intent; the coordinator
delegates to them so intent can never drift between compilation and host
materialization.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from moonmind.omnigent.repository_sources import (
    RepositorySourceError,
    normalize_repository_source,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.workspace_intent import (
    WORKSPACE_INTENT_LOCATOR_REQUIRED,
    WORKSPACE_INTENT_UNSAFE_INPUT,
    WorkspaceIntentAssetProjection,
    WorkspaceIntentRecord,
    assert_no_runtime_shortcut_keys,
)


class WorkspaceIntentCompilationError(ValueError):
    """Fail-closed compilation error raised before any host mutation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _spec(request: AgentExecutionRequest) -> Mapping[str, Any]:
    spec = request.workspace_spec
    return spec if isinstance(spec, Mapping) else {}


def _parameters(request: AgentExecutionRequest) -> Mapping[str, Any]:
    parameters = request.parameters
    return parameters if isinstance(parameters, Mapping) else {}


def authored_repository_source(request: AgentExecutionRequest) -> str:
    spec = _spec(request)
    parameters = _parameters(request)
    for candidate in (
        spec.get("repository"),
        spec.get("repo"),
        parameters.get("repository"),
    ):
        if isinstance(candidate, Mapping):
            nested = candidate.get("repository")
            if isinstance(nested, Mapping):
                value = str(nested.get("name") or "").strip()
                if value:
                    return value
        value = str(candidate or "").strip()
        if value:
            return value
    return ""


def authored_starting_branch(request: AgentExecutionRequest) -> str | None:
    spec = _spec(request)
    repository = spec.get("repository")
    if isinstance(repository, Mapping):
        branch = repository.get("branch")
        if isinstance(branch, Mapping):
            value = str(branch.get("name") or "").strip()
            if value:
                return value
    for candidate in (
        spec.get("startingBranch"),
        spec.get("branch"),
        spec.get("baseBranch"),
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return None


def authored_target_branch(request: AgentExecutionRequest) -> str | None:
    value = str(_spec(request).get("targetBranch") or "").strip()
    return value or None


def authored_checkout_commit(request: AgentExecutionRequest) -> str | None:
    spec = _spec(request)
    repository = spec.get("repository")
    if isinstance(repository, Mapping):
        revision = repository.get("revision")
        if isinstance(revision, Mapping):
            for key in ("commitSha", "revisionSignature"):
                value = str(revision.get(key) or "").strip()
                if value:
                    return value
    for candidate in (spec.get("checkoutCommit"), spec.get("baseCommit")):
        value = str(candidate or "").strip()
        if value:
            return value
    return None


def authored_connection_ref(request: AgentExecutionRequest) -> str | None:
    spec = _spec(request)
    repository = spec.get("repositoryTarget")
    if not isinstance(repository, Mapping):
        # Already-recorded requests may carry the typed target directly under
        # ``repository``. New Run workflow requests use ``repositoryTarget``.
        repository = spec.get("repository")
    if not isinstance(repository, Mapping):
        return None
    value = str(repository.get("connectionRef") or "").strip()
    return value or None


def authored_revision_kind(request: AgentExecutionRequest) -> str | None:
    repository = _spec(request).get("repository")
    if not isinstance(repository, Mapping):
        return None
    revision = repository.get("revision")
    if not isinstance(revision, Mapping):
        return None
    value = str(revision.get("kind") or "").strip()
    return value or None


def authored_restore_input_refs(request: AgentExecutionRequest) -> tuple[str, ...]:
    raw = _spec(request).get("restoreInputRefs")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(
        dict.fromkeys(str(value).strip() for value in raw if str(value).strip())
    )


def authored_attachment_refs(request: AgentExecutionRequest) -> tuple[str, ...]:
    raw = _spec(request).get("attachmentRefs")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(
        dict.fromkeys(str(value).strip() for value in raw if str(value).strip())
    )


def authored_required_capabilities(request: AgentExecutionRequest) -> tuple[str, ...]:
    raw = _parameters(request).get("requiredCapabilities")
    if not isinstance(raw, list):
        return ()
    return tuple(
        dict.fromkeys(
            str(value).strip().lower() for value in raw if str(value).strip()
        )
    )


def authored_publish_mode(request: AgentExecutionRequest) -> str:
    value = str(_parameters(request).get("publishMode") or "none").strip().lower()
    return value or "none"


def authored_repository_mutation_required(request: AgentExecutionRequest) -> bool:
    parameters = _parameters(request)
    if bool(parameters.get("repositoryMutationRequired")):
        return True
    if str(parameters.get("repositoryOperation") or "").strip().lower() == "write":
        return True
    if authored_publish_mode(request) not in {"", "none"}:
        return True
    skill = parameters.get("skill")
    if isinstance(skill, Mapping):
        side_effect = skill.get("sideEffect")
        if isinstance(side_effect, Mapping) and str(
            side_effect.get("kind") or ""
        ).strip():
            return True
    return False


def authored_github_mutation_required(request: AgentExecutionRequest) -> bool:
    if "gh" not in authored_required_capabilities(request):
        return False
    parameters = _parameters(request)
    if authored_publish_mode(request) not in {"", "none"}:
        return True
    skill = parameters.get("skill")
    if not isinstance(skill, Mapping):
        return False
    side_effect = skill.get("sideEffect")
    return isinstance(side_effect, Mapping) and bool(
        str(side_effect.get("kind") or "").strip()
    )


def _authored_saved_work_policy(request: AgentExecutionRequest) -> str | None:
    value = str(_parameters(request).get("savedWorkPolicy") or "").strip()
    return value or None


def _authored_publication_destination(
    request: AgentExecutionRequest,
) -> str | None:
    parameters = _parameters(request)
    for candidate in (
        parameters.get("publicationDestination"),
        parameters.get("publishTarget"),
        authored_target_branch(request),
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return None


def _classify_repository(source: str) -> str | None:
    """Classify an authored repository source through the canonical classifier.

    Reuses the provider-neutral repository-source authority so durable intent
    and Workflow Detail can never diverge from how the workspace is actually
    cloned. That classifier resolves the ``owner/repo`` shorthand to
    ``github_https`` and matches GitHub on the exact URL host, avoiding the
    substring test that would both misreport a normal GitHub clone as
    ``[local-source]`` and mislabel lookalike hosts (``github.com.evil.com``) as
    GitHub.
    """

    if not source:
        return None
    try:
        _normalized, kind = normalize_repository_source(source)
    except RepositorySourceError:
        # An unsupported/unclassifiable authored source is treated as local so
        # bounded evidence redacts it rather than leaking a raw worker-local path.
        return "local"
    return kind


def _asset_projection(
    payload: Mapping[str, Any],
    *,
    default_name: str | None = None,
) -> WorkspaceIntentAssetProjection | None:
    name = str(payload.get("name") or default_name or "").strip()
    if not name:
        return None
    version = payload.get("version") or payload.get("skillVersion")
    digest = (
        payload.get("digest")
        or payload.get("contentDigest")
        or payload.get("inputContractDigest")
    )
    return WorkspaceIntentAssetProjection(
        name=name,
        version=str(version).strip() if version else None,
        digest=str(digest).strip() if digest else None,
    )


def _skill_projections(
    request: AgentExecutionRequest,
) -> list[WorkspaceIntentAssetProjection]:
    projections: list[WorkspaceIntentAssetProjection] = []
    skill = request.skill if isinstance(request.skill, Mapping) else {}
    if skill:
        projection = _asset_projection(skill)
        if projection is not None:
            projections.append(projection)
    return projections


def _tool_projections(
    request: AgentExecutionRequest,
) -> list[WorkspaceIntentAssetProjection]:
    projections: list[WorkspaceIntentAssetProjection] = []
    raw = _parameters(request).get("tools")
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, Mapping):
                projection = _asset_projection(item)
                if projection is not None:
                    projections.append(projection)
    return projections


def _partition_restore_refs(
    refs: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    """Keep artifact-backed restore inputs distinct from external-state refs.

    An ``artifact://`` reference is a durable artifact input, never a filesystem
    path; provider-native external-state references (``external-state:`` or
    ``ext-state:``) prove session/provider continuity and must not be conflated
    with an artifact input.
    """

    artifact_refs: list[str] = []
    external_state_refs: list[str] = []
    for ref in refs:
        lowered = ref.lower()
        if lowered.startswith(("external-state:", "ext-state:", "provider-state:")):
            external_state_refs.append(ref)
        else:
            artifact_refs.append(ref)
    return artifact_refs, external_state_refs


def compile_workspace_intent(
    request: AgentExecutionRequest,
    *,
    workflow_id: str,
    step_execution_id: str,
    run_id: str | None = None,
    logical_step_id: str | None = None,
    created_at: datetime | None = None,
) -> WorkspaceIntentRecord:
    """Compile one authored request into the durable workspace-intent record.

    Fails closed with :class:`WorkspaceIntentCompilationError` on any authored
    runtime-specific shortcut (bind path, Docker socket/volume, arbitrary host
    id) or credential-shaped value, before the host is selected or mutated.
    """

    # 1. Reject runtime-specific shortcuts smuggled through the authored payload
    #    before deriving any host authority from it.
    try:
        assert_no_runtime_shortcut_keys(request.workspace_spec)
        assert_no_runtime_shortcut_keys(request.parameters)
    except ValueError as exc:
        raise WorkspaceIntentCompilationError(
            WORKSPACE_INTENT_UNSAFE_INPUT, str(exc)
        ) from exc

    # 2. The canonical workspace identity is the typed locator, never a caller
    #    bind path or volume name.
    locator = _spec(request).get("workspaceLocator")
    if not isinstance(locator, Mapping):
        raise WorkspaceIntentCompilationError(
            WORKSPACE_INTENT_LOCATOR_REQUIRED,
            "workspaceSpec.workspaceLocator is required to compile workspace intent",
        )

    repository = authored_repository_source(request) or None
    resolved_repository = _spec(request).get("resolvedRepositoryTarget")
    if resolved_repository is not None:
        raise WorkspaceIntentCompilationError(
            WORKSPACE_INTENT_UNSAFE_INPUT,
            "workspaceSpec.resolvedRepositoryTarget is runtime-owned and cannot "
            "be authored",
        )
    restore_refs = authored_restore_input_refs(request)
    restore_input_refs, external_state_refs = _partition_restore_refs(restore_refs)

    try:
        record = WorkspaceIntentRecord(
            createdAt=created_at or datetime.now(tz=UTC),
            workflowId=workflow_id,
            runId=run_id,
            logicalStepId=logical_step_id,
            stepExecutionId=step_execution_id,
            repository=repository,
            repositoryKind=_classify_repository(repository or ""),
            connectionRef=authored_connection_ref(request),
            checkoutCommit=authored_checkout_commit(request),
            revisionKind=authored_revision_kind(request),
            remoteTipExpectation=(
                resolved_repository.get("remoteTipExpectation")
                if resolved_repository
                else
                {"kind": "read_only"}
                if authored_revision_kind(request)
                else {
                    "kind": "must_equal",
                    "revision": {
                        "provider": (
                            "lore"
                            if authored_revision_kind(request) == "lore_revision"
                            else "git"
                        ),
                        "repositoryId": repository,
                        "commitSha": authored_checkout_commit(request),
                    },
                }
                if repository
                else None
            ),
            resolvedRepositoryTarget=(
                dict(resolved_repository) if resolved_repository else None
            ),
            startingBranch=authored_starting_branch(request),
            targetBranch=authored_target_branch(request),
            inputRefs=list(request.input_refs),
            attachmentRefs=list(authored_attachment_refs(request)),
            resolvedSkillsetRef=request.resolved_skillset_ref,
            skillProjections=_skill_projections(request),
            toolProjections=_tool_projections(request),
            restoreInputRefs=restore_input_refs,
            externalStateRefs=external_state_refs,
            repositoryMutation=authored_repository_mutation_required(request),
            publishMode=authored_publish_mode(request),
            savedWorkPolicy=_authored_saved_work_policy(request),
            publicationDestination=_authored_publication_destination(request),
            requiredCapabilities=list(authored_required_capabilities(request)),
            workspaceLocator=locator,
        )
    except ValueError as exc:
        raise WorkspaceIntentCompilationError(
            WORKSPACE_INTENT_UNSAFE_INPUT, str(exc)
        ) from exc
    return record


__all__ = [
    "WorkspaceIntentCompilationError",
    "authored_attachment_refs",
    "authored_checkout_commit",
    "authored_github_mutation_required",
    "authored_publish_mode",
    "authored_repository_mutation_required",
    "authored_repository_source",
    "authored_required_capabilities",
    "authored_restore_input_refs",
    "authored_starting_branch",
    "authored_target_branch",
    "compile_workspace_intent",
]
