"""Omnigent external-agent request validation and target resolution.

MM-990 implements the bounded adapter contract slice from
``docs/Omnigent/OmnigentAdapter.md``: Omnigent-specific selection stays under
``parameters.omnigent``, workspace rules are host-type specific, repository
tasks normalize repository URLs into the managed session workspace, and target
resolution is deterministic.

Source issue traceability: MM-981 -> MM-990.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal
from urllib.parse import urlparse

from moonmind.omnigent.bridge_security import enforce_id_only_labels
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
    FailureClass,
    ProviderCapabilityDescriptor,
)
from moonmind.workflows.adapters.base_external_agent_adapter import (
    BaseExternalAgentAdapter,
)

HostType = Literal["managed", "external"]

_OMNIGENT_CAPABILITY = ProviderCapabilityDescriptor(
    providerName="omnigent",
    supportsCallbacks=False,
    supportsCancel=False,
    supportsResultFetch=False,
    defaultPollHintSeconds=15,
    execution_style="streaming_gateway",
)

_ROOT_SELECTION_KEYS = {
    "endpointRef",
    "endpoint_ref",
    "agent",
    "agentId",
    "agentName",
    "bundleRef",
    "harnessOverride",
    "session",
    "hostType",
    "hostId",
    "modelOverride",
    "reasoningEffort",
    "terminalLaunchArgs",
}


class OmnigentAdapterError(ValueError):
    """Adapter validation error carrying the canonical MoonMind failure class."""

    def __init__(self, message: str, *, failure_class: FailureClass) -> None:
        super().__init__(message)
        self.failure_class = failure_class


@dataclass(frozen=True, slots=True)
class OmnigentAgentSelection:
    agent_id: str | None = None
    agent_name: str | None = None
    bundle_ref: str | None = None
    harness_override: str | None = None


@dataclass(frozen=True, slots=True)
class OmnigentSessionSelection:
    host_type: HostType = "managed"
    host_id: str | None = None
    workspace: str | None = None
    title: str | None = None
    labels: dict[str, Any] = field(default_factory=dict)
    model_override: str | None = None
    reasoning_effort: str | None = None
    terminal_launch_args: list[str] = field(default_factory=list)
    collaboration_mode: str | None = None
    allow_empty_workspace: bool = False


@dataclass(frozen=True, slots=True)
class OmnigentExecutionSelection:
    endpoint_ref: str = "default"
    agent: OmnigentAgentSelection = field(default_factory=OmnigentAgentSelection)
    session: OmnigentSessionSelection = field(default_factory=OmnigentSessionSelection)
    prompt: dict[str, Any] = field(default_factory=dict)
    capture: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OmnigentResolvedTarget:
    agent_id: str
    source: Literal["agent_id", "agent_name", "bundle_ref", "default_agent_name"]
    agent_name: str | None = None


def build_omnigent_selection(
    request: AgentExecutionRequest,
) -> OmnigentExecutionSelection:
    """Validate and normalize the Omnigent-specific selection block."""

    if request.agent_kind != "external":
        raise OmnigentAdapterError(
            "Omnigent only supports external agent_kind",
            failure_class="user_error",
        )
    if request.agent_id.strip().lower() != "omnigent":
        raise OmnigentAdapterError(
            "Omnigent target selection must not alter top-level agentId; "
            "use agentId='omnigent'",
            failure_class="user_error",
        )

    parameters = dict(request.parameters or {})
    leaked = sorted(key for key in _ROOT_SELECTION_KEYS if key in parameters)
    if leaked:
        raise OmnigentAdapterError(
            "Omnigent selection fields must be nested under parameters.omnigent: "
            + ", ".join(leaked),
            failure_class="user_error",
        )

    raw = parameters.get("omnigent")
    if raw is None:
        raw_payload: Mapping[str, Any] = {}
    elif isinstance(raw, Mapping):
        raw_payload = raw
    else:
        raise OmnigentAdapterError(
            "parameters.omnigent must be an object",
            failure_class="user_error",
        )

    agent = _parse_agent(raw_payload.get("agent"))
    session = _parse_session(raw_payload.get("session"))
    session = _normalize_session_workspace(
        request=request,
        parameters=parameters,
        session=session,
    )
    _validate_session(session)

    return OmnigentExecutionSelection(
        endpoint_ref=_clean(raw_payload.get("endpointRef")) or "default",
        agent=agent,
        session=session,
        prompt=_mapping(
            raw_payload.get("prompt"),
            field_name="parameters.omnigent.prompt",
        ),
        capture=_mapping(
            raw_payload.get("capture"),
            field_name="parameters.omnigent.capture",
        ),
    )


async def resolve_omnigent_target(
    selection: OmnigentExecutionSelection,
    *,
    list_agents: Callable[[], Awaitable[list[Mapping[str, Any]]]],
    upload_agent_bundle: Callable[[str], Awaitable[Mapping[str, Any]]],
    default_agent_name: str | None,
) -> OmnigentResolvedTarget:
    """Resolve target agent in the MM-990 canonical order."""

    agent = selection.agent
    if agent.agent_id:
        return OmnigentResolvedTarget(agent_id=agent.agent_id, source="agent_id")

    if agent.agent_name:
        resolved = await _resolve_agent_name(agent.agent_name, list_agents=list_agents)
        return OmnigentResolvedTarget(
            agent_id=resolved,
            source="agent_name",
            agent_name=agent.agent_name,
        )

    if agent.bundle_ref:
        created = await upload_agent_bundle(agent.bundle_ref)
        created_id = _extract_agent_id(created)
        if created_id:
            return OmnigentResolvedTarget(
                agent_id=created_id,
                source="bundle_ref",
            )
        raise OmnigentAdapterError(
            "Omnigent bundle upload did not return an agent id",
            failure_class="integration_error",
        )

    default_name = _clean(default_agent_name)
    if default_name:
        resolved = await _resolve_agent_name(default_name, list_agents=list_agents)
        return OmnigentResolvedTarget(
            agent_id=resolved,
            source="default_agent_name",
            agent_name=default_name,
        )

    raise OmnigentAdapterError(
        "Unable to resolve Omnigent target agent",
        failure_class="integration_error",
    )


def build_omnigent_session_create_payload(
    *,
    request: AgentExecutionRequest,
    selection: OmnigentExecutionSelection,
    target: OmnigentResolvedTarget,
) -> dict[str, Any]:
    """Build the JSON session-create payload sent to Omnigent."""

    session = selection.session
    title = (
        session.title
        or _clean((request.parameters or {}).get("title"))
        or "MoonMind Agent Task"
    )
    payload: dict[str, Any] = {
        "agent_id": target.agent_id,
        "title": title,
        # §16 rule 4: session labels carry MoonMind ids and the idempotency key
        # but never secrets; caller-supplied labels are guarded before merge.
        "labels": enforce_id_only_labels(
            {
                "moonmind.correlation_id": request.correlation_id,
                "moonmind.idempotency_key": request.idempotency_key,
                **session.labels,
            }
        ),
        "host_type": session.host_type,
        "workspace": session.workspace,
        "model_override": session.model_override,
        "reasoning_effort": session.reasoning_effort,
        "terminal_launch_args": session.terminal_launch_args,
    }
    if session.host_type == "external":
        payload["host_id"] = session.host_id
    if session.collaboration_mode:
        payload["collaboration_mode"] = session.collaboration_mode
    return {key: value for key, value in payload.items() if value is not None}


class OmnigentExternalAdapter(BaseExternalAgentAdapter):
    """Registry entry for Omnigent; execution uses integration.omnigent.execute."""

    def __init__(self) -> None:
        super().__init__(accepted_agent_ids=frozenset({"omnigent"}))

    @property
    def provider_capability(self) -> ProviderCapabilityDescriptor:
        return _OMNIGENT_CAPABILITY

    async def do_start(
        self,
        request: AgentExecutionRequest,
        title: str,
        description: str,
        metadata: dict[str, Any],
    ) -> AgentRunHandle:
        raise RuntimeError(
            "Omnigent executes via integration.omnigent.execute only; "
            "start activity is not registered for this provider."
        )

    async def do_status(self, run_id: str) -> AgentRunStatus:
        raise RuntimeError(
            "Omnigent uses streaming execution; status polling is unused."
        )

    async def do_fetch_result(self, run_id: str) -> AgentRunResult:
        raise RuntimeError("Omnigent uses streaming execution; fetch_result is unused.")

    async def do_cancel(self, run_id: str) -> AgentRunStatus:
        raise RuntimeError("Omnigent cancels via activity cancellation on execute.")


def _parse_agent(raw: object) -> OmnigentAgentSelection:
    payload = _mapping(raw, field_name="parameters.omnigent.agent")
    return OmnigentAgentSelection(
        agent_id=_clean(payload.get("agentId")),
        agent_name=_clean(payload.get("agentName")),
        bundle_ref=_clean(payload.get("bundleRef")),
        harness_override=_clean(payload.get("harnessOverride")),
    )


def _parse_session(raw: object) -> OmnigentSessionSelection:
    payload = _mapping(raw, field_name="parameters.omnigent.session")
    host_type = _clean(payload.get("hostType")) or "managed"
    if host_type not in {"managed", "external"}:
        raise OmnigentAdapterError(
            "parameters.omnigent.session.hostType must be 'managed' or 'external'",
            failure_class="user_error",
        )
    labels = _mapping(
        payload.get("labels"),
        field_name="parameters.omnigent.session.labels",
    )
    launch_args = payload.get("terminalLaunchArgs") or []
    if not isinstance(launch_args, list) or not all(
        isinstance(item, str) for item in launch_args
    ):
        raise OmnigentAdapterError(
            "parameters.omnigent.session.terminalLaunchArgs must be a string array",
            failure_class="user_error",
        )
    return OmnigentSessionSelection(
        host_type=host_type,
        host_id=_clean(payload.get("hostId")),
        workspace=_clean(payload.get("workspace")),
        title=_clean(payload.get("title")),
        labels=dict(labels),
        model_override=_clean(payload.get("modelOverride")),
        reasoning_effort=_clean(payload.get("reasoningEffort")),
        terminal_launch_args=list(launch_args),
        collaboration_mode=_clean(payload.get("collaborationMode")),
        allow_empty_workspace=bool(payload.get("allowEmptyWorkspace", False)),
    )


def _normalize_session_workspace(
    *,
    request: AgentExecutionRequest,
    parameters: Mapping[str, Any],
    session: OmnigentSessionSelection,
) -> OmnigentSessionSelection:
    if session.host_type != "managed" or session.workspace:
        return session

    repository = _find_repository_url(request.workspace_spec)
    workspace_context = parameters.get("workspaceContext")
    if repository is None and isinstance(workspace_context, Mapping):
        repository = _find_repository_url(workspace_context)
    omnigent = parameters.get("omnigent")
    if repository is None and isinstance(omnigent, Mapping):
        nested_context = omnigent.get("workspaceContext")
        if isinstance(nested_context, Mapping):
            repository = _find_repository_url(nested_context)

    if repository is None:
        return session

    workspace = _repository_workspace_value(repository, request.workspace_spec)
    return replace(session, workspace=workspace)


def _validate_session(session: OmnigentSessionSelection) -> None:
    if session.host_type == "managed":
        if session.host_id:
            raise OmnigentAdapterError(
                "Managed Omnigent sessions reject caller-provided hostId",
                failure_class="user_error",
            )
        if not session.workspace and not session.allow_empty_workspace:
            raise OmnigentAdapterError(
                "Managed Omnigent sessions require a repository workspace or "
                "parameters.omnigent.session.allowEmptyWorkspace=true",
                failure_class="user_error",
            )
        if session.workspace and not _is_git_url_with_optional_branch(
            session.workspace
        ):
            raise OmnigentAdapterError(
                "Managed Omnigent session.workspace must be a git repository "
                "URL or absent",
                failure_class="user_error",
            )
        return

    if not session.host_id:
        raise OmnigentAdapterError(
            "External Omnigent sessions require hostId",
            failure_class="user_error",
        )
    if not session.workspace:
        raise OmnigentAdapterError(
            "External Omnigent sessions require an absolute host workspace path",
            failure_class="user_error",
        )
    if _is_git_url_with_optional_branch(session.workspace):
        raise OmnigentAdapterError(
            "External Omnigent session.workspace must be a host path, "
            "not a repository URL",
            failure_class="user_error",
        )
    if not session.workspace.startswith("/"):
        raise OmnigentAdapterError(
            "External Omnigent session.workspace must be an absolute host path",
            failure_class="user_error",
        )


async def _resolve_agent_name(
    agent_name: str,
    *,
    list_agents: Callable[[], Awaitable[list[Mapping[str, Any]]]],
) -> str:
    for agent in await list_agents():
        if _clean(agent.get("name")) == agent_name:
            agent_id = _extract_agent_id(agent)
            if agent_id:
                return agent_id
    raise OmnigentAdapterError(
        f"Unknown Omnigent agent name: {agent_name}",
        failure_class="user_error",
    )


def _extract_agent_id(payload: Mapping[str, Any]) -> str | None:
    return (
        _clean(payload.get("id"))
        or _clean(payload.get("agentId"))
        or _clean(payload.get("agent_id"))
        or None
    )


def _find_repository_url(payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for key in ("repository", "repositoryUrl", "repoUrl", "gitUrl"):
        value = payload.get(key)
        if isinstance(value, str) and _is_git_url_with_optional_branch(value):
            return value
        if isinstance(value, Mapping):
            nested = _find_repository_url(value)
            if nested:
                return nested
    return None


def _repository_workspace_value(
    repository: str,
    workspace_spec: Mapping[str, Any],
) -> str:
    branch = _clean(workspace_spec.get("branch")) or _clean(
        workspace_spec.get("startingBranch")
    )
    if branch and "#" not in repository:
        return f"{repository}#{branch}"
    return repository


def _is_git_url_with_optional_branch(value: str) -> bool:
    candidate = value.split("#", 1)[0]
    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https", "ssh", "git"} and parsed.netloc:
        return True
    if candidate.startswith("git@") and ":" in candidate:
        return True
    return False


def _mapping(raw: object, *, field_name: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise OmnigentAdapterError(
            f"{field_name} must be an object",
            failure_class="user_error",
        )
    return dict(raw)


def _clean(value: object | None) -> str | None:
    text = str(value or "").strip()
    return text or None


__all__ = [
    "OmnigentAdapterError",
    "OmnigentAgentSelection",
    "OmnigentExecutionSelection",
    "OmnigentExternalAdapter",
    "OmnigentResolvedTarget",
    "OmnigentSessionSelection",
    "build_omnigent_selection",
    "build_omnigent_session_create_payload",
    "resolve_omnigent_target",
]
