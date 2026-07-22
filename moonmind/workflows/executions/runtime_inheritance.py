"""Resolve child-agent runtime inheritance for ``POST /api/executions``.

When a parent workflow creates follow-up work via the execution API, the child
should run with the same effective runtime/provider profile as the parent
unless the request explicitly overrides it.  This module owns the contract
the executions router uses to detect and authorise inheritance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from moonmind.workflows.executions.runtime_defaults import normalize_runtime_id

# Inheritance directive values accepted on the wire.
INHERIT_CALLER = "caller"
INHERIT_PARENT = "parent"
_VALID_DIRECTIVES = frozenset({INHERIT_CALLER, INHERIT_PARENT})

# Scopes that gate workflow-principal inheritance.
SCOPE_CREATE_CHILD = "executions:create-child"
SCOPE_INHERIT_RUNTIME = "executions:inherit-runtime"


class RuntimeInheritanceError(Exception):
    """Raised when a request specifies inheritance but cannot be honoured.

    The router converts this into a 422 with ``code=invalid_runtime_inheritance``.
    """

    def __init__(self, message: str, *, code: str = "invalid_runtime_inheritance"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExecutionPrincipal:
    """Caller identity used to gate child-agent runtime inheritance.

    ``user_id`` and ``is_superuser`` describe the OIDC/user principal that
    FastAPI resolved.  ``workflow_id`` / ``run_id`` / ``agent_run_id`` are
    populated when the caller asserts a workflow identity through trusted
    transport headers, *and* that identity has been verified (the executions
    router verifies ownership via ``TemporalExecutionService.describe_execution``).
    ``scopes`` carries the capabilities granted to the principal.
    """

    user_id: Optional[str] = None
    is_superuser: bool = False
    workflow_id: Optional[str] = None
    run_id: Optional[str] = None
    agent_run_id: Optional[str] = None
    scopes: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_workflow_principal(self) -> bool:
        return bool(self.workflow_id)

    def has_scope(self, scope: str) -> bool:
        return self.is_superuser or scope in self.scopes


@dataclass(frozen=True)
class InheritedRuntime:
    """Effective runtime fields copied from the parent execution.

    All fields are normalised – ``target_runtime`` is canonical (e.g.
    ``codex_cli``), and the remaining fields are taken from the parent's
    ``parameters`` (resolved values, not raw request fields).
    """

    target_runtime: Optional[str] = None
    model: Optional[str] = None
    effort: Optional[str] = None
    profile_id: Optional[str] = None
    execution_profile_ref: Optional[str] = None
    omnigent: Optional[dict[str, Any]] = None
    source_workflow_id: Optional[str] = None


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalise_directive(value: Any) -> Optional[str]:
    text = _coerce_str(value)
    if text is None:
        return None
    lowered = text.lower()
    if lowered not in _VALID_DIRECTIVES:
        raise RuntimeInheritanceError(
            f"Unsupported runtimeInheritance value: {text!r}. "
            f"Expected one of: {sorted(_VALID_DIRECTIVES)}"
        )
    return lowered


def _workflow_payload_from_request(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    workflow_payload = payload.get("workflow")
    if isinstance(workflow_payload, Mapping):
        return workflow_payload
    legacy_payload = payload.get("task")
    return legacy_payload if isinstance(legacy_payload, Mapping) else {}


def extract_inheritance_directive(
    payload: Mapping[str, Any] | None,
    task_payload: Mapping[str, Any] | None = None,
) -> tuple[Optional[str], Optional[str]]:
    """Return ``(directive, parent_workflow_id)`` from a workflow-shaped payload.

    Accepts both shapes documented in the story:

    * ``payload.runtimeInheritance = "caller" | "parent"``
    * ``payload.runtime.inherit = "caller" | "parent"``

    Returns ``(None, None)`` when no directive is present.  Raises
    ``RuntimeInheritanceError`` on malformed values.
    """

    if payload is None:
        payload = {}
    if task_payload is None:
        task_payload = _workflow_payload_from_request(payload)

    candidates: list[Any] = [
        payload.get("runtimeInheritance"),
        payload.get("runtime_inheritance"),
        task_payload.get("runtimeInheritance"),
        task_payload.get("runtime_inheritance"),
    ]
    runtime_node = task_payload.get("runtime") if isinstance(task_payload, Mapping) else None
    if isinstance(runtime_node, Mapping):
        candidates.append(runtime_node.get("inherit"))
        candidates.append(runtime_node.get("inheritance"))
    payload_runtime = payload.get("runtime") if isinstance(payload, Mapping) else None
    if isinstance(payload_runtime, Mapping):
        candidates.append(payload_runtime.get("inherit"))
        candidates.append(payload_runtime.get("inheritance"))

    directive: Optional[str] = None
    for candidate in candidates:
        normalised = _normalise_directive(candidate)
        if normalised is None:
            continue
        if directive is not None and directive != normalised:
            raise RuntimeInheritanceError(
                "Conflicting runtimeInheritance directives in request payload."
            )
        directive = normalised

    parent_id = (
        _coerce_str(payload.get("parentWorkflowId"))
        or _coerce_str(payload.get("parent_workflow_id"))
        or _coerce_str(task_payload.get("parentWorkflowId"))
        or _coerce_str(task_payload.get("parent_workflow_id"))
    )

    return directive, parent_id


def has_explicit_child_runtime(
    payload: Mapping[str, Any] | None,
    task_payload: Mapping[str, Any] | None = None,
) -> bool:
    """Return True when the request carries any explicit runtime selector.

    Explicit selectors short-circuit inheritance – the existing runtime
    resolution path validates and applies them.
    """

    if payload is None:
        payload = {}
    if task_payload is None:
        task_payload = _workflow_payload_from_request(payload)

    if _coerce_str(payload.get("targetRuntime")):
        return True

    runtime_node = task_payload.get("runtime") if isinstance(task_payload, Mapping) else None
    if isinstance(runtime_node, Mapping):
        for key in (
            "mode",
            "model",
            "effort",
            "modelTier",
            "tierFallback",
            "providerProfileRef",
            "providerProfile",
            "profileId",
            "executionProfileRef",
        ):
            if _coerce_str(runtime_node.get(key)):
                return True

    for key in ("providerProfileRef", "profileId", "providerProfile"):
        if _coerce_str(payload.get(key)) or _coerce_str(task_payload.get(key)):
            return True

    return False


def _extract_parent_runtime_fields(record: Any) -> InheritedRuntime:
    """Pull the parent's *effective* runtime from a stored execution record.

    ``record.parameters`` is authoritative for resolved values: the
    executions router writes ``targetRuntime`` (canonical), the resolved
    ``model`` (via ``resolve_effective_model``), ``effort``, and
    ``profileId``/``workflow.runtime.executionProfileRef`` into ``parameters``
    when the parent workflow was created.  Memo/search attributes are used as
    a fallback for executions that pre-date that path.
    """

    parameters = dict(getattr(record, "parameters", None) or {})
    workflow_block = (
        parameters.get("workflow")
        if isinstance(parameters.get("workflow"), Mapping)
        else parameters.get("task")
        if isinstance(parameters.get("task"), Mapping)
        else {}
    )
    workflow_runtime = (
        dict(workflow_block.get("runtime") or {})
        if isinstance(workflow_block, Mapping)
        and isinstance(workflow_block.get("runtime"), Mapping)
        else {}
    )
    memo = dict(getattr(record, "memo", None) or {})
    search_attributes = dict(getattr(record, "search_attributes", None) or {})

    target_runtime = (
        _coerce_str(parameters.get("targetRuntime"))
        or _coerce_str(workflow_runtime.get("mode"))
        or _coerce_str(memo.get("targetRuntime"))
        or _coerce_str(search_attributes.get("mm_target_runtime"))
        or _coerce_str(search_attributes.get("mm_runtime"))
    )
    if target_runtime:
        target_runtime = normalize_runtime_id(target_runtime)

    model = (
        _coerce_str(parameters.get("model"))
        or _coerce_str(workflow_runtime.get("model"))
    )
    effort = (
        _coerce_str(parameters.get("effort"))
        or _coerce_str(workflow_runtime.get("effort"))
    )
    profile_id = (
        _coerce_str(parameters.get("profileId"))
        or _coerce_str(workflow_runtime.get("profileId"))
        or _coerce_str(workflow_runtime.get("providerProfile"))
    )
    execution_profile_ref = (
        _coerce_str(workflow_runtime.get("executionProfileRef")) or profile_id
    )
    raw_omnigent = (
        workflow_runtime.get("omnigent")
        if isinstance(workflow_runtime.get("omnigent"), Mapping)
        else parameters.get("omnigent")
    )
    omnigent = dict(raw_omnigent) if isinstance(raw_omnigent, Mapping) else None

    workflow_id = _coerce_str(getattr(record, "workflow_id", None))

    return InheritedRuntime(
        target_runtime=target_runtime,
        model=model,
        effort=effort,
        profile_id=profile_id,
        execution_profile_ref=execution_profile_ref,
        omnigent=omnigent,
        source_workflow_id=workflow_id,
    )


async def _load_authorised_parent(
    *,
    service: Any,
    parent_workflow_id: str,
    principal: ExecutionPrincipal,
) -> Any:
    """Load *parent_workflow_id* and verify the principal may inherit from it.

    Authorisation rule: either the principal already asserts the same
    workflow id (verified task identity) or the principal is the workflow's
    owner.  Superusers are allowed without ownership check.  Raises
    ``RuntimeInheritanceError`` when the parent cannot be loaded or the
    caller lacks authority.
    """

    if principal.workflow_id == parent_workflow_id:
        # Already verified during principal resolution.
        try:
            return await service.describe_execution(parent_workflow_id)
        except Exception as exc:  # pragma: no cover - defensive
            raise RuntimeInheritanceError(
                f"Parent execution {parent_workflow_id!r} was not found.",
                code="parent_execution_not_found",
            ) from exc

    try:
        record = await service.describe_execution(parent_workflow_id)
    except Exception as exc:
        raise RuntimeInheritanceError(
            f"Parent execution {parent_workflow_id!r} was not found.",
            code="parent_execution_not_found",
        ) from exc

    if principal.is_superuser:
        return record

    record_owner_id = _coerce_str(getattr(record, "owner_id", None))
    if record_owner_id and principal.user_id and record_owner_id == principal.user_id:
        return record

    raise RuntimeInheritanceError(
        "Caller is not authorised to inherit runtime from "
        f"{parent_workflow_id!r}.",
        code="runtime_inheritance_forbidden",
    )


async def resolve_child_runtime_inheritance(
    *,
    request_payload: Mapping[str, Any],
    task_payload: Mapping[str, Any] | None,
    principal: ExecutionPrincipal,
    service: Any,
) -> Optional[InheritedRuntime]:
    """Resolve runtime inheritance for a child task create request.

    Returns the ``InheritedRuntime`` to apply, or ``None`` when the
    existing default runtime resolution should run unchanged.

    Resolution order:

    1. If ``runtimeInheritance = "caller"`` and the principal is a task
       principal with the required scopes, copy the caller task's runtime.
    2. If ``runtimeInheritance = "parent"`` and ``parentWorkflowId`` is
       provided, copy the parent's runtime after verifying ownership.
    3. Otherwise return ``None``.

    The inherited runtime is applied non-destructively by
    ``apply_inherited_runtime_to_payload`` – explicit fields on the request
    are preserved and inheritance only fills in the gaps. This means a
    caller that opts into inheritance via the directive will pick up the
    parent's model, effort, and provider profile even when it has stamped
    an explicit ``targetRuntime`` or partial ``task.runtime`` block.
    """

    if task_payload is None and isinstance(request_payload, Mapping):
        task_payload = (
            request_payload.get("task")
            if isinstance(request_payload.get("task"), Mapping)
            else {}
        )
    task_payload = task_payload or {}

    directive, parent_workflow_id_hint = extract_inheritance_directive(
        request_payload, task_payload
    )
    if directive is None:
        return None

    if directive == INHERIT_CALLER:
        if not principal.is_workflow_principal:
            raise RuntimeInheritanceError(
                'runtimeInheritance="caller" requires a workflow-scoped principal.',
                code="runtime_inheritance_requires_workflow_principal",
            )
        if not principal.has_scope(SCOPE_CREATE_CHILD):
            raise RuntimeInheritanceError(
                "Caller lacks scope executions:create-child.",
                code="runtime_inheritance_forbidden",
            )
        if not principal.has_scope(SCOPE_INHERIT_RUNTIME):
            raise RuntimeInheritanceError(
                "Caller lacks scope executions:inherit-runtime.",
                code="runtime_inheritance_forbidden",
            )
        parent_workflow_id = principal.workflow_id
        assert parent_workflow_id is not None  # narrowed by is_workflow_principal
        parent_record = await _load_authorised_parent(
            service=service,
            parent_workflow_id=parent_workflow_id,
            principal=principal,
        )
        return _extract_parent_runtime_fields(parent_record)

    if directive == INHERIT_PARENT:
        if not parent_workflow_id_hint:
            raise RuntimeInheritanceError(
                'runtimeInheritance="parent" requires parentWorkflowId.',
                code="runtime_inheritance_requires_parent",
            )
        parent_record = await _load_authorised_parent(
            service=service,
            parent_workflow_id=parent_workflow_id_hint,
            principal=principal,
        )
        return _extract_parent_runtime_fields(parent_record)

    return None


def apply_inherited_runtime_to_payload(
    *,
    payload: dict[str, Any],
    task_payload: dict[str, Any],
    inherited: InheritedRuntime,
) -> None:
    """Stamp ``inherited`` runtime fields onto a normalised workflow payload.

    Mutates ``payload`` and ``task_payload`` in place.  Applied strictly
    non-destructively: any field the caller already set (``targetRuntime``,
    ``workflow.runtime.{mode,model,effort,executionProfileRef,profileId,
    providerProfile}``, or top-level ``profileId`` / ``providerProfile``)
    is preserved; inheritance only fills in the gaps.  The downstream
    runtime resolution path then sees the merged request.

    Note: when the caller has supplied an explicit profile selector
    (``providerProfileRef`` / ``profileId`` / ``providerProfile`` anywhere in the payload),
    ``executionProfileRef`` is *also* left blank.  Downstream selection
    prefers ``executionProfileRef`` over ``profileId``/``providerProfile``
    (see ``run.py``), so backfilling the parent's ref would silently
    override the child's explicit profile.
    """

    runtime_block = (
        dict(task_payload.get("runtime"))
        if isinstance(task_payload.get("runtime"), Mapping)
        else {}
    )

    explicit_target_runtime = _coerce_str(
        payload.get("targetRuntime")
    ) or _coerce_str(runtime_block.get("mode"))
    explicit_profile_id = (
        _coerce_str(runtime_block.get("providerProfileRef"))
        or _coerce_str(runtime_block.get("profileId"))
        or _coerce_str(runtime_block.get("providerProfile"))
        or _coerce_str(runtime_block.get("executionProfileRef"))
        or _coerce_str(payload.get("providerProfileRef"))
        or _coerce_str(payload.get("profileId"))
        or _coerce_str(payload.get("providerProfile"))
        or _coerce_str(task_payload.get("providerProfileRef"))
        or _coerce_str(task_payload.get("profileId"))
        or _coerce_str(task_payload.get("providerProfile"))
    )

    if inherited.target_runtime and not explicit_target_runtime:
        payload["targetRuntime"] = inherited.target_runtime
        runtime_block["mode"] = inherited.target_runtime
    if inherited.model and not _coerce_str(runtime_block.get("model")):
        runtime_block["model"] = inherited.model
    if inherited.effort and not _coerce_str(runtime_block.get("effort")):
        runtime_block["effort"] = inherited.effort
    if (
        inherited.execution_profile_ref
        and not _coerce_str(runtime_block.get("executionProfileRef"))
        and not explicit_profile_id
    ):
        runtime_block["executionProfileRef"] = inherited.execution_profile_ref
    if inherited.profile_id and not explicit_profile_id:
        runtime_block["profileId"] = inherited.profile_id
    if inherited.omnigent:
        explicit_omnigent = (
            runtime_block.get("omnigent")
            if isinstance(runtime_block.get("omnigent"), Mapping)
            else payload.get("omnigent")
        )
        if not isinstance(explicit_omnigent, Mapping):
            runtime_block["omnigent"] = dict(inherited.omnigent)

    if runtime_block:
        task_payload["runtime"] = runtime_block


__all__ = [
    "ExecutionPrincipal",
    "INHERIT_CALLER",
    "INHERIT_PARENT",
    "InheritedRuntime",
    "RuntimeInheritanceError",
    "SCOPE_CREATE_CHILD",
    "SCOPE_INHERIT_RUNTIME",
    "apply_inherited_runtime_to_payload",
    "extract_inheritance_directive",
    "has_explicit_child_runtime",
    "resolve_child_runtime_inheritance",
]
