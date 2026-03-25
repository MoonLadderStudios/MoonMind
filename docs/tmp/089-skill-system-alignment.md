# Skill System Alignment: Legacy vs Current Execution Model

## Problem

The `MoonMind.Run` execution loop in `run.py` has two plan node dispatch paths that reflect different eras of the skill system:

### Legacy Path: `tool.type == "skill"` (lines 463-511)

Dispatches plan nodes as **direct Temporal activities** (`mm.skill.execute` / `mm.tool.execute`). Skills are resolved from the pinned registry snapshot and executed on the activity fleet without going through `MoonMind.AgentRun`.

```python
elif tool_type == "skill":
    # --- Activity dispatch: existing skill path ---
    route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("mm.skill.execute")
    execution_result = await workflow.execute_activity(
        route.activity_type, invocation_payload, ...
    )
```

### Current Path: `tool.type == "agent_runtime"` (lines 443-461)

Dispatches plan nodes as **child `MoonMind.AgentRun` workflows**. The runtime planner in `worker_runtime.py` always generates `agent_runtime` nodes — even when a skill is the source of the work. The skill name is embedded as instructions:

```python
# worker_runtime.py line 104-108
skill_info = task_payload.get("skill") or task_payload.get("tool")
if skill_info and isinstance(skill_info, dict) and skill_info.get("name"):
    instructions = f"Execute skill '{skill_name}'"

# line 184-188 — always emits agent_runtime type
"tool": {"type": "agent_runtime", "name": runtime_mode, "version": "1.0"}
```

### Evidence of Legacy Status

- Line 425 of `run.py` explicitly labels `node.skill` as a "legacy alias":
  ```python
  "plan node tool definition is required (node.skill is legacy alias)"
  ```
- The runtime planner (`_build_runtime_planner`) never generates `tool.type: "skill"` nodes
- The `skill` branch requires `tool_name` and `tool_version`, which are no longer how the planner structures nodes

## Impact

1. **Dead code risk** — The `skill` branch in `run.py` is reachable only if a plan explicitly uses `tool.type: "skill"`, but no current plan generator produces such plans. It may still be exercised by older persisted plans or manually crafted payloads.

2. **Documentation drift** — The skill system docs may still describe skills as direct activity dispatches, when in practice they now route through `agent_runtime` → `MoonMind.AgentRun`.

3. **Multi-step Jules workflows** — For the new `sendMessage`-based multi-step design, all Jules steps will be `agent_runtime` nodes. The legacy `skill` branch is irrelevant to this flow, but its existence adds confusion about how heterogeneous plans (skills + agents) execute.

## Suggested Follow-Up

1. **Audit plan generators** — Confirm no active plan generator or external caller produces `tool.type: "skill"` nodes. Check if any persisted plans in the artifact store still reference this type.

2. **Deprecate or remove the `skill` branch** — If no active producers exist:
   - Add a deprecation warning log to the `skill` branch
   - Eventually remove it and simplify the execution loop to only handle `agent_runtime`

3. **Update skill documentation** — Align docs to describe the current model where skills are dispatched as `agent_runtime` plan nodes that go through `MoonMind.AgentRun`, not as direct activity invocations.

4. **Clean up `node.skill` alias** — The `skill` key on plan nodes is already called out as legacy. Consider removing the fallback to `node.get("skill")` in the node selection logic.
