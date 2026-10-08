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
    from moonmind.workflows.executions.repository_contract import (
        github_repository_name_from_value,
    )

    spec = _spec(request)
    parameters = _parameters(request)
    workflow = parameters.get("workflow") or parameters.get("task")
    workflow = workflow if isinstance(workflow, Mapping) else {}
    candidates: list[Any] = []
    for projection in (
        spec,
        parameters.get("workspaceSpec"),
        parameters.get("workspace"),
        workflow.get("workspace"),
    ):
        if not isinstance(projection, Mapping):
            continue
        candidates.extend(
            (
                projection.get("repository"),
                projection.get("repo"),
                projection.get("repositoryTarget"),
            )
        )
        source = projection.get("workspaceSource")
        if isinstance(source, Mapping) and source.get("kind") == "repository":
            candidates.extend(
                (source.get("repository"), source.get("repositoryTarget"))
            )
    candidates.extend((parameters.get("repository"), workflow.get("repository")))
    sources: list[tuple[str, str]] = []
    explicit_authority: dict[str, str] = {}
    for candidate in candidates:
        value = str(candidate or "").strip()
        provider = None
        if isinstance(candidate, Mapping):
            provider = candidate.get("provider")
            # These are the authored target's provider/connection axes. A
            # legacy scalar omits them, but explicit conflicting authorities
            # cannot become equivalent merely because their display names are.
            for key in ("provider", "connectionRef"):
                raw = candidate.get(key)
                if raw is None or raw == "":
                    continue
                if not isinstance(raw, str):
                    raise WorkspaceIntentCompilationError(
                        "repository_intent_conflict",
                        f"repository {key} must be a string",
                    )
                declared = raw.strip()
                if key in explicit_authority and explicit_authority[key] != declared:
                    raise WorkspaceIntentCompilationError(
                        "repository_intent_conflict",
                        f"repository {key} projections conflict across the authored request",
                    )
                explicit_authority[key] = declared
            nested = candidate.get("repository")
            if isinstance(nested, Mapping):
                value = str(nested.get("name") or "").strip() or value
            elif candidate.get("name"):
                value = str(candidate["name"]).strip()
        if value:
            # GitHub's repository-name equivalence does not apply to Lore,
            # local paths, or other hosting services.
            github_name = (
                github_repository_name_from_value(value).lower()
                if provider in (None, "git")
                else ""
            )
            sources.append((value, github_name))
    if not sources:
        return ""
    source, github_name = sources[0]
    for candidate, candidate_github_name in sources[1:]:
        if candidate == source:
            continue
        if github_name and candidate_github_name == github_name:
            continue
        raise WorkspaceIntentCompilationError(
            "repository_intent_conflict",
            "repository projections conflict across the authored request",
        )
    return source


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
    for repository in (spec.get("repositoryTarget"), spec.get("repository")):
        if isinstance(repository, Mapping):
            value = str(repository.get("connectionRef") or "").strip()
            if value:
                return value
    # Preserve the payload shape already recorded by in-flight Temporal runs.
    # New Run workflow requests carry this authority under ``repositoryTarget``.
    value = str(spec.get("connectionRef") or "").strip()
    return value or None


def authored_anonymous_source(request: AgentExecutionRequest) -> bool:
    """Whether the operator explicitly chose an anonymous repository read."""
    spec = _spec(request)
    for authored in (
        spec.get("workspaceSource"),
        spec.get("repositoryTarget"),
        spec.get("repository"),
    ):
        if isinstance(authored, Mapping) and authored.get("accessMode") == "anonymous":
            return True
    return False


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


def authored_github_operations(
    request: AgentExecutionRequest, *, for_publication: bool = False
) -> tuple[str, ...]:
    """Compile required actions, never grants, for the selected GitHub credential.

    Tool availability is not write intent. Generic actions and Skill-owned auto
    publication declare their operations explicitly; branch/PR publication has
    an existing exact operation contract. The publisher needs only destination
    authority, not source reads or an agent's later merge action.
    """
    from typing import get_args

    from moonmind.workflows.executions.repository_contract import RepositoryOperation

    def invalid(message: str) -> None:
        raise WorkspaceIntentCompilationError("github_action_intent_invalid", message)

    def declared(source: Mapping[str, Any]) -> tuple[str, ...]:
        if "githubOperations" not in source:
            return ()
        raw = source["githubOperations"]
        if not isinstance(raw, list) or any(
            not isinstance(value, str) or value not in get_args(RepositoryOperation)
            for value in raw
        ):
            invalid(
                "githubOperations must be a list of supported repository operations"
            )
        return tuple(dict.fromkeys(raw))

    repository_source = authored_repository_source(request)
    parameters = _parameters(request)
    # Runtime parameters are an extensible JSON mapping, not typed authoring
    # fields. Validate before normalizing so truthy malformed values cannot
    # mean mutation to the workspace owner but read-only credential access here.
    mutation_required = parameters.get("repositoryMutationRequired")
    if mutation_required is not None and not isinstance(mutation_required, bool):
        invalid("repositoryMutationRequired must be a boolean")
    repository_operation = parameters.get("repositoryOperation")
    if repository_operation is not None and not isinstance(repository_operation, str):
        invalid("repositoryOperation must be read or write")
    repository_operation = str(repository_operation or "").strip().lower()
    if repository_operation not in {"", "read", "write"}:
        invalid("repositoryOperation must be read or write")
    raw_publish_mode = parameters.get("publishMode")
    if raw_publish_mode is not None and not isinstance(raw_publish_mode, str):
        invalid("publishMode must be none, branch, pr, or auto")
    publish_mode = authored_publish_mode(request)
    if publish_mode not in {"none", "branch", "pr", "auto"}:
        invalid("publishMode must be none, branch, pr, or auto")
    publication = {
        "branch": ("write", "branch_write"),
        "pr": ("write", "branch_write", "review_request"),
    }.get(publish_mode, ())
    if for_publication and publication:
        return publication

    skill: dict[str, Any] = {}
    selected_skill = ""
    for candidate in (request.skill, parameters.get("skill")):
        if candidate is None:
            continue
        if not isinstance(candidate, Mapping):
            invalid("skill action metadata must be an object")
        identity = candidate.get("name", candidate.get("id", ""))
        if not isinstance(identity, str):
            invalid("selected Skill identity must be a string")
        identity = identity.strip().lower()
        if identity:
            if selected_skill and selected_skill != identity:
                invalid("selected Skill identities conflict across request projections")
            selected_skill = identity
        for source_key in ("publish", "sideEffect", "inputs", "args"):
            if source_key not in candidate or candidate[source_key] is None:
                continue
            if not isinstance(candidate[source_key], Mapping):
                invalid(f"skill.{source_key} must be an object")
            # Canonical authoring uses args; the runtime planner uses inputs.
            # Both must retain the same finish authority at this boundary.
            key = "inputs" if source_key == "args" else source_key
            combined = dict(skill.get(key, {}))
            for field, value in candidate[source_key].items():
                if field == "githubOperations" and field in combined:
                    combined[field] = list(
                        dict.fromkeys((*declared(combined), *declared(candidate[source_key])))
                    )
                else:
                    if (
                        field in {"kind", "mode", "finishMode"}
                        and field in combined
                        and combined[field] != value
                    ):
                        invalid(
                            f"skill.{key}.{field} conflicts across request projections"
                        )
                    combined[field] = value
            skill[key] = combined
    publish = skill.get("publish", {})
    side_effect = skill.get("sideEffect", {})
    skill_publish_mode = publish.get("mode")
    if skill_publish_mode is not None and not isinstance(skill_publish_mode, str):
        invalid("skill.publish.mode must be none, branch, pr, or auto")
    skill_publish_mode = str(skill_publish_mode or "none").strip().lower() or "none"
    if skill_publish_mode not in {"none", "branch", "pr", "auto"}:
        invalid("skill.publish.mode must be none, branch, pr, or auto")
    publish_operations = declared(publish)
    parameter_operations = declared(parameters)
    skill_operations = declared(side_effect)
    kind = side_effect.get("kind", "")
    if not isinstance(kind, str):
        invalid("skill.sideEffect.kind must be a string")
    kind = kind.strip().lower()

    if kind == "merge_pull_request":
        inputs = skill.get("inputs", {})
        if not isinstance(inputs, Mapping):
            invalid("merge_pull_request inputs must be an object")
        finish_mode = inputs.get("finishMode", "merge")
        # Match the resolved Skill's merge default for omitted, null, or blank
        # values, without treating False, 0, or other non-strings as defaults.
        if finish_mode is None or (
            isinstance(finish_mode, str) and not finish_mode.strip()
        ):
            finish_mode = "merge"
        if not isinstance(finish_mode, str) or finish_mode not in {"merge", "fix_only"}:
            invalid("merge_pull_request finishMode must be merge or fix_only")
        if finish_mode != "merge" and "merge_request" in (
            *parameter_operations,
            *publish_operations,
            *skill_operations,
        ):
            invalid("merge_request conflicts with the selected non-merge finishMode")
        if finish_mode == "merge":
            skill_operations = (*skill_operations, "merge_request")

    operations = tuple(
        dict.fromkeys((*parameter_operations, *publish_operations, *skill_operations))
    )
    gh_required = "gh" in authored_required_capabilities(request)
    # Auto is provider-neutral. Apply its GitHub declaration rule only when
    # GitHub transport/tooling or explicit GitHub action metadata is involved;
    # local and other-provider publishers must not acquire GitHub authority.
    github_action_relevant = (
        gh_required
        or _classify_repository(repository_source) == "github_https"
        or kind == "merge_pull_request"
        or any(
            "githubOperations" in source
            for source in (parameters, publish, side_effect)
        )
    )
    if (
        github_action_relevant
        and (publish_mode == "auto" or skill_publish_mode == "auto")
        and not any(op != "read" for op in publish_operations + parameter_operations)
    ):
        invalid("auto publication requires explicit githubOperations")
    if (
        gh_required
        and kind not in {"", "enqueue_children", "merge_pull_request"}
        and not any(op != "read" for op in operations)
    ):
        invalid("GitHub side effects require explicit githubOperations")
    generic_write = bool(mutation_required) or repository_operation == "write"
    if (
        gh_required
        and generic_write
        and not publication
        and not any(op != "read" for op in operations)
    ):
        invalid("GitHub mutation requires explicit githubOperations")
    if for_publication:
        return tuple(dict.fromkeys((*publication, *publish_operations)))
    # Explicit Skill/agent operations apply to authenticated git as well as gh
    # rather than disappearing when only repository transport was declared.
    return tuple(
        dict.fromkeys(("read", *(publication if gh_required else ()), *operations))
    )


def authored_github_mutation_required(request: AgentExecutionRequest) -> bool:
    return any(operation != "read" for operation in authored_github_operations(request))


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
    "authored_github_operations",
    "authored_publish_mode",
    "authored_repository_mutation_required",
    "authored_repository_source",
    "authored_required_capabilities",
    "authored_restore_input_refs",
    "authored_starting_branch",
    "authored_target_branch",
    "compile_workspace_intent",
]
