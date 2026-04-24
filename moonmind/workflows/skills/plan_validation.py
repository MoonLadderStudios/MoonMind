"""Plan schema and DAG validation against pinned tool registry snapshots."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Mapping

from .skill_plan_contracts import (
    PlanDefinition,
    SkillDefinition,
    SkillInvocation,
    parse_plan_definition,
)
from .skill_registry import SkillRegistrySnapshot

class PlanValidationError(ValueError):
    """Raised when a plan violates structural or schema contracts."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

@dataclass(frozen=True, slots=True)
class ValidatedPlan:
    """Validated plan ready for deterministic interpretation."""

    plan: PlanDefinition
    topological_order: tuple[str, ...]

    @property
    def node_map(self) -> dict[str, SkillInvocation]:
        return {node.id: node for node in self.plan.nodes}

def _is_ref_object(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value.keys()) == {"ref"}
        and isinstance(value.get("ref"), Mapping)
    )

def _json_pointer_tokens(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PlanValidationError(
            "invalid_reference",
            f"json_pointer must start with '/': {pointer}",
        )
    tokens = pointer.split("/")[1:]
    return [token.replace("~1", "/").replace("~0", "~") for token in tokens]

def _schema_pointer_exists(schema: Mapping[str, Any], pointer: str) -> bool:
    try:
        tokens = _json_pointer_tokens(pointer)
    except PlanValidationError:
        return False

    current: Mapping[str, Any] | None = schema
    for token in tokens:
        if current is None:
            return False

        schema_type = current.get("type")
        if schema_type == "object" or (
            schema_type is None and isinstance(current.get("properties"), Mapping)
        ):
            properties = current.get("properties")
            additional = current.get("additionalProperties")
            if isinstance(properties, Mapping) and token in properties:
                child = properties[token]
                current = child if isinstance(child, Mapping) else None
                continue
            if isinstance(additional, Mapping):
                current = additional
                continue
            return False

        if schema_type == "array":
            items = current.get("items")
            if not token.isdigit() or not isinstance(items, Mapping):
                return False
            current = items
            continue

        return False

    return current is not None

def _validate_schema_shape(schema: Mapping[str, Any], *, path: str) -> None:
    schema_type = schema.get("type")
    if schema_type is None:
        return

    allowed = {"object", "string", "integer", "number", "boolean", "array"}
    if schema_type not in allowed:
        raise PlanValidationError(
            "invalid_schema",
            f"Unsupported schema type at {path}: {schema_type}",
        )

    if schema_type == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise PlanValidationError(
                "invalid_schema", f"properties must be an object at {path}"
            )
        required = schema.get("required", [])
        if required is not None and not isinstance(required, list):
            raise PlanValidationError(
                "invalid_schema", f"required must be an array at {path}"
            )
        for key, child in properties.items():
            if isinstance(child, Mapping):
                _validate_schema_shape(child, path=f"{path}/properties/{key}")

    if schema_type == "array":
        items = schema.get("items")
        if items is not None and not isinstance(items, Mapping):
            raise PlanValidationError(
                "invalid_schema", f"items must be an object at {path}"
            )
        if isinstance(items, Mapping):
            _validate_schema_shape(items, path=f"{path}/items")

def _validate_json_value(
    *,
    value: Any,
    schema: Mapping[str, Any],
    path: str,
    allow_refs: bool,
) -> None:
    if allow_refs and _is_ref_object(value):
        return

    if "enum" in schema and value not in schema["enum"]:
        raise PlanValidationError(
            "invalid_input",
            f"Value at {path} must match one of enum values",
        )

    schema_type = schema.get("type")
    if schema_type is None:
        return

    if schema_type == "object":
        if not isinstance(value, Mapping):
            raise PlanValidationError(
                "invalid_input", f"Value at {path} must be an object"
            )

        properties = schema.get("properties", {})
        required = schema.get("required", [])
        additional = schema.get("additionalProperties", True)

        if isinstance(required, list):
            for key in required:
                if key not in value:
                    raise PlanValidationError(
                        "invalid_input",
                        f"Missing required field '{key}' at {path}",
                    )

        if isinstance(properties, Mapping):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, Mapping):
                    _validate_json_value(
                        value=value[key],
                        schema=child_schema,
                        path=f"{path}/{key}",
                        allow_refs=allow_refs,
                    )

        if additional is False and isinstance(properties, Mapping):
            allowed = set(properties.keys())
            for key in value.keys():
                if key not in allowed:
                    raise PlanValidationError(
                        "invalid_input",
                        f"Unexpected field '{key}' at {path}",
                    )
        return

    if schema_type == "array":
        if not isinstance(value, list):
            raise PlanValidationError(
                "invalid_input", f"Value at {path} must be an array"
            )
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_json_value(
                    value=item,
                    schema=item_schema,
                    path=f"{path}/{index}",
                    allow_refs=allow_refs,
                )
        return

    if schema_type == "string" and not isinstance(value, str):
        raise PlanValidationError("invalid_input", f"Value at {path} must be a string")
    if schema_type == "integer" and not (
        isinstance(value, int) and not isinstance(value, bool)
    ):
        raise PlanValidationError(
            "invalid_input", f"Value at {path} must be an integer"
        )
    if schema_type == "number" and not (
        (isinstance(value, int) and not isinstance(value, bool))
        or isinstance(value, float)
    ):
        raise PlanValidationError("invalid_input", f"Value at {path} must be a number")
    if schema_type == "boolean" and not isinstance(value, bool):
        raise PlanValidationError("invalid_input", f"Value at {path} must be a boolean")

def _collect_refs(value: Any, *, path: str = "/") -> list[tuple[str, str, str]]:
    refs: list[tuple[str, str, str]] = []

    if _is_ref_object(value):
        ref = value["ref"]
        node = str(ref.get("node") or "").strip()
        pointer = str(ref.get("json_pointer") or "").strip()
        refs.append((node, pointer, path))
        return refs

    if isinstance(value, Mapping):
        for key, item in value.items():
            refs.extend(_collect_refs(item, path=f"{path}{key}/"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            refs.extend(_collect_refs(item, path=f"{path}{index}/"))

    return refs

def _topological_sort(
    *,
    node_ids: list[str],
    edges: list[tuple[str, str]],
) -> tuple[str, ...]:
    in_degree: dict[str, int] = {node_id: 0 for node_id in node_ids}
    outgoing: dict[str, list[str]] = defaultdict(list)

    for from_node, to_node in edges:
        in_degree[to_node] += 1
        outgoing[from_node].append(to_node)

    ready = deque(
        sorted(node_id for node_id, degree in in_degree.items() if degree == 0)
    )
    order: list[str] = []

    while ready:
        current = ready.popleft()
        order.append(current)
        for successor in sorted(outgoing[current]):
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                ready.append(successor)

    if len(order) != len(node_ids):
        raise PlanValidationError("invalid_plan", "Plan graph must be acyclic")

    return tuple(order)

def _is_reachable(
    *,
    start: str,
    target: str,
    outgoing: Mapping[str, tuple[str, ...]],
) -> bool:
    if start == target:
        return True
    queue: deque[str] = deque([start])
    seen: set[str] = set()

    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        for successor in outgoing.get(node, ()):  # pragma: no branch - simple lookup
            if successor == target:
                return True
            queue.append(successor)
    return False

def validate_plan(
    *,
    plan: PlanDefinition,
    registry_snapshot: SkillRegistrySnapshot,
) -> ValidatedPlan:
    """Validate a parsed plan against the pinned registry snapshot."""

    expected_snapshot = plan.metadata.registry_snapshot
    if registry_snapshot.digest != expected_snapshot.digest:
        raise PlanValidationError(
            "invalid_plan",
            "Plan metadata registry snapshot digest does not match provided registry snapshot",
        )
    if registry_snapshot.artifact_ref != expected_snapshot.artifact_ref:
        raise PlanValidationError(
            "invalid_plan",
            "Plan metadata registry snapshot artifact_ref does not match provided registry snapshot",
        )

    node_map: dict[str, SkillInvocation] = {}
    for node in plan.nodes:
        if node.id in node_map:
            raise PlanValidationError(
                "invalid_plan", f"Duplicate node id in plan: {node.id}"
            )
        node_map[node.id] = node

    edge_pairs: list[tuple[str, str]] = []
    outgoing_list: dict[str, list[str]] = defaultdict(list)
    for edge in plan.edges:
        if edge.from_node not in node_map:
            raise PlanValidationError(
                "invalid_plan",
                f"Edge references unknown source node '{edge.from_node}'",
            )
        if edge.to_node not in node_map:
            raise PlanValidationError(
                "invalid_plan", f"Edge references unknown target node '{edge.to_node}'"
            )
        edge_pairs.append((edge.from_node, edge.to_node))
        outgoing_list[edge.from_node].append(edge.to_node)

    order = _topological_sort(node_ids=list(node_map.keys()), edges=edge_pairs)
    outgoing: dict[str, tuple[str, ...]] = {
        node_id: tuple(sorted(values)) for node_id, values in outgoing_list.items()
    }

    registry_skills: dict[tuple[str, str], SkillDefinition] = registry_snapshot.by_key
    for node in plan.nodes:
        definition = registry_skills.get(node.skill_key)
        if definition is None:
            raise PlanValidationError(
                "invalid_plan",
                f"Plan node '{node.id}' references unknown skill '{node.skill_name}:{node.skill_version}'",
            )

        _validate_schema_shape(definition.input_schema, path=f"{node.id}.inputs")
        _validate_schema_shape(definition.output_schema, path=f"{node.id}.outputs")
        _validate_json_value(
            value=node.inputs,
            schema=definition.input_schema,
            path=f"{node.id}.inputs",
            allow_refs=True,
        )

        refs = _collect_refs(node.inputs)
        for ref_node, pointer, ref_path in refs:
            if not ref_node:
                raise PlanValidationError(
                    "invalid_reference",
                    f"Reference at {node.id}{ref_path} is missing ref.node",
                )
            if not pointer:
                raise PlanValidationError(
                    "invalid_reference",
                    f"Reference at {node.id}{ref_path} is missing ref.json_pointer",
                )
            if ref_node not in node_map:
                raise PlanValidationError(
                    "invalid_reference",
                    f"Reference at {node.id}{ref_path} points to unknown node '{ref_node}'",
                )
            if ref_node == node.id:
                raise PlanValidationError(
                    "invalid_reference",
                    f"Reference at {node.id}{ref_path} cannot point to the same node",
                )

            if not _is_reachable(start=ref_node, target=node.id, outgoing=outgoing):
                raise PlanValidationError(
                    "invalid_reference",
                    f"Reference at {node.id}{ref_path} requires dependency path {ref_node} -> {node.id}",
                )

            referenced_skill = registry_skills[node_map[ref_node].skill_key]
            result_schema = {
                "type": "object",
                "properties": {
                    "outputs": referenced_skill.output_schema,
                    "output_artifacts": {"type": "array"},
                    "status": {"type": "string"},
                },
            }
            if not _schema_pointer_exists(result_schema, pointer):
                raise PlanValidationError(
                    "invalid_reference",
                    f"Reference at {node.id}{ref_path} points to invalid output path '{pointer}' on node '{ref_node}'",
                )

    return ValidatedPlan(plan=plan, topological_order=order)

def validate_plan_payload(
    *,
    payload: Mapping[str, Any],
    registry_snapshot: SkillRegistrySnapshot,
) -> ValidatedPlan:
    """Parse and validate a plan payload against the pinned registry snapshot."""

    parsed = parse_plan_definition(payload)
    return validate_plan(plan=parsed, registry_snapshot=registry_snapshot)

__all__ = [
    "PlanValidationError",
    "ValidatedPlan",
    "validate_plan",
    "validate_plan_payload",
]
