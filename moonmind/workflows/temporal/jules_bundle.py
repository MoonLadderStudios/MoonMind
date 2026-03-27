"""Helpers for one-shot Jules bundle compilation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

JULES_AGENT_IDS = frozenset({"jules", "jules_api"})


@dataclass(frozen=True)
class JulesBundleSpec:
    bundle_id: str
    bundled_node_ids: tuple[str, ...]
    representative_node: dict[str, Any]
    compiled_brief: str
    manifest: dict[str, Any]


def _runtime_agent_id(node: dict[str, Any]) -> str:
    tool = node.get("tool") or {}
    inputs = node.get("inputs") or {}
    runtime = inputs.get("runtime") or {}
    return str(
        runtime.get("mode")
        or runtime.get("agent_id")
        or inputs.get("targetRuntime")
        or tool.get("name")
        or ""
    ).strip().lower()


def is_jules_agent_runtime_node(node: dict[str, Any]) -> bool:
    tool = node.get("tool") or {}
    if str(tool.get("type") or "").strip().lower() != "agent_runtime":
        return False
    return _runtime_agent_id(node) in JULES_AGENT_IDS


def eligible_for_bundle(
    first_node: dict[str, Any],
    next_node: dict[str, Any],
) -> bool:
    if not (is_jules_agent_runtime_node(first_node) and is_jules_agent_runtime_node(next_node)):
        return False

    first_inputs = first_node.get("inputs") or {}
    next_inputs = next_node.get("inputs") or {}

    keys = ("repository", "repo", "startingBranch", "targetBranch", "branch", "publishMode")
    for key in keys:
        if (first_inputs.get(key) or "") != (next_inputs.get(key) or ""):
            return False
    return True


def group_consecutive_jules_nodes(
    ordered_nodes: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    for node in ordered_nodes:
        if not is_jules_agent_runtime_node(node):
            if current:
                groups.append(current)
                current = []
            continue
        if not current:
            current = [node]
            continue
        if eligible_for_bundle(current[-1], node):
            current.append(node)
            continue
        groups.append(current)
        current = [node]

    if current:
        groups.append(current)
    return groups


def build_bundle_spec(
    nodes: list[dict[str, Any]],
    *,
    workflow_id: str,
    workflow_run_id: str,
) -> JulesBundleSpec:
    if len(nodes) < 2:
        raise ValueError("Jules bundle spec requires at least two nodes")

    first = nodes[0]
    first_inputs = dict(first.get("inputs") or {})
    bundled_node_ids = tuple(str(node.get("id") or "").strip() or f"node-{idx + 1}" for idx, node in enumerate(nodes))
    bundle_id = f"{workflow_id}:jules-bundle:{bundled_node_ids[0]}:{bundled_node_ids[-1]}"

    repo = str(first_inputs.get("repository") or first_inputs.get("repo") or "").strip() or "(unknown)"
    starting_branch = str(first_inputs.get("startingBranch") or first_inputs.get("branch") or "main").strip() or "main"
    target_branch = str(first_inputs.get("targetBranch") or "").strip() or None
    publish_mode = str(first_inputs.get("publishMode") or "none").strip() or "none"

    checklist_entries: list[str] = []
    manifest_items: list[dict[str, str]] = []
    for idx, node in enumerate(nodes, start=1):
        inputs = node.get("inputs") or {}
        raw_instruction = str(inputs.get("instructions") or "").strip()
        instruction = raw_instruction or f"Complete logical work item {node.get('id') or idx}."
        checklist_entries.append(f"{idx}. {instruction}")
        manifest_items.append(
            {
                "nodeId": bundled_node_ids[idx - 1],
                "instruction": instruction,
            }
        )

    compiled_brief = "\n".join(
        [
            "You are implementing a multi-part repository task as one cohesive change.",
            "",
            "Mission:",
            f"Complete the bundled Jules work represented by logical nodes: {', '.join(bundled_node_ids)}.",
            "",
            "Repository Context:",
            f"- Repository: {repo}",
            f"- Starting branch: {starting_branch}",
            f"- Target branch: {target_branch or starting_branch}",
            f"- Publish mode: {publish_mode}",
            "- Runtime: Jules via MoonMind",
            "",
            "Execution Rules:",
            "- Complete the work as one cohesive implementation.",
            "- Follow the ordered checklist below sequentially.",
            "- Make the minimum necessary changes.",
            "- Avoid unrelated refactors.",
            "- If blocked or unsafe, stop and explain the blocker rather than guessing.",
            "- Track which checklist items were completed in the final summary.",
            "",
            "Ordered Checklist:",
            *checklist_entries,
            "",
            "Validation Checklist:",
            "- Run relevant validation for the changed files when possible.",
            "- If validation cannot run, say so and explain why.",
            "",
            "Final Response Requirements:",
            "- Summarize the changes made.",
            "- State which checklist items were completed.",
            "- Note incomplete items, blockers, assumptions, or follow-up recommendations.",
            "- Include validation results.",
        ]
    )

    manifest = {
        "bundleId": bundle_id,
        "bundleStrategy": "one_shot_jules",
        "bundledNodeIds": list(bundled_node_ids),
        "repository": repo,
        "startingBranch": starting_branch,
        "targetBranch": target_branch,
        "publishMode": publish_mode,
        "executionRules": [
            "complete_as_one_cohesive_change",
            "ordered_checklist_execution",
            "avoid_unrelated_refactors",
            "report_blockers_instead_of_guessing",
        ],
        "orderedChecklist": manifest_items,
        "validationChecklist": [
            "run_relevant_validation_when_possible",
            "report_unrun_validation_with_reason",
        ],
        "correlationId": workflow_id,
        "idempotencyKey": f"{workflow_id}:{bundle_id}:{workflow_run_id}",
    }

    synthetic_inputs = dict(first_inputs)
    synthetic_inputs["instructions"] = compiled_brief
    synthetic_inputs["bundleId"] = bundle_id
    synthetic_inputs["bundledNodeIds"] = list(bundled_node_ids)
    synthetic_inputs["bundleStrategy"] = "one_shot_jules"

    representative_node = {
        "id": bundle_id,
        "tool": dict(first.get("tool") or {}),
        "inputs": synthetic_inputs,
        "options": dict(first.get("options") or {}),
    }

    return JulesBundleSpec(
        bundle_id=bundle_id,
        bundled_node_ids=bundled_node_ids,
        representative_node=representative_node,
        compiled_brief=compiled_brief,
        manifest=manifest,
    )


__all__ = [
    "JULES_AGENT_IDS",
    "JulesBundleSpec",
    "build_bundle_spec",
    "eligible_for_bundle",
    "group_consecutive_jules_nodes",
    "is_jules_agent_runtime_node",
]
