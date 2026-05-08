# Quickstart: Prepare Target-Aware Inputs

## Scope

Validate MM-631 after implementation by proving that objective-scoped and current-step-scoped prepared context reaches each runtime step and delegated `AgentRun` child without unrelated step leakage.

## Unit Test Strategy

Run the full unit suite before finalizing:

```bash
./tools/test_unit.sh
```

Focused unit iteration should cover:

```bash
./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py
./tools/test_unit.sh tests/unit/moonmind/vision/test_service.py
./tools/test_unit.sh tests/unit/agents/codex_worker/test_attachment_materialization.py
./tools/test_unit.sh tests/unit/agents/codex_worker/test_worker.py
./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py
```

Expected unit coverage:
- Structured `inputAttachments` remain text/binary separated.
- Prepared manifest/result models preserve objective and step targets.
- Derived image context remains secondary context behind refs.
- Step prepared context filtering includes objective plus current step only.
- Adapter-visible payloads preserve prepared target bindings.
- Prepare failures are explicit and bounded.

## Integration Test Strategy

Run hermetic integration CI before finalizing when Docker is available:

```bash
./tools/test_integration.sh
```

Focused integration scenarios should cover:
- A task with one objective attachment and two step-scoped attachments.
- Runtime prepare writes a manifest or manifest artifact ref before first affected step execution.
- Step 1 receives objective context plus Step 1 context and no Step 2 context.
- A delegated child `AgentRun` receives only the represented step context.
- A missing or unauthorized attachment causes prepare failure before step dispatch.

If a Temporal time-skipping workflow test is too slow for `integration_ci`, keep it as local workflow boundary coverage and document the reason in final verification.

## End-to-End Validation Shape

1. Construct a task-shaped payload with:
   - `task.inputAttachments`: one objective image ref.
   - `task.steps[0].inputAttachments`: one step image ref.
   - `task.steps[1].inputAttachments`: a different step image ref.
2. Execute the runtime prepare path.
3. Confirm the prepared manifest includes all three refs with correct target kind and step refs.
4. Confirm generated image context is referenced separately from instruction text.
5. Dispatch Step 1 and inspect the step or child request boundary.
6. Confirm Step 1 contains objective context plus Step 1 context only.
7. Dispatch Step 2 and confirm the inverse filtering.
8. Repeat with a missing attachment and confirm no affected step dispatch occurs.

## Traceability Checks

Final verification must confirm:
- `MM-631` is preserved in `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`, `tasks.md`, verification output, commit text, and PR metadata.
- The original Jira preset brief remains available in `spec.md`.
- Source design mappings from `docs/Tasks/TaskArchitecture.md` remain mapped to tests and implementation evidence.
