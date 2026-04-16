# Quickstart: Parent-Owned Merge Automation

## Focused Unit Validation

Run the focused tests while implementing:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py
./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py
```

Expected coverage:
- merge automation disabled preserves existing PR publish behavior;
- enabled merge automation builds a compact publish context;
- parent starts one deterministic child workflow for one publish context;
- retry/replay or duplicate publish does not start another child;
- parent remains `awaiting_external` while the child is active;
- child success allows parent success;
- child blocked, failed, expired, or canceled outcomes prevent parent success.

## Workflow Boundary Validation

Use the real parent workflow invocation shape with `publishMode = "pr"` and `mergeAutomation.enabled = true`.

Scenario:
1. Stub a successful publish result with repository, PR number, PR URL, base ref, head ref, head SHA, publication time, optional Jira issue key, and compact artifact ref.
2. Stub child workflow execution so the first case stays active long enough to observe parent `awaiting_external`.
3. Return a `merged` child outcome and assert the parent completes successfully.
4. Return a `blocked` or `failed` child outcome and assert the parent does not report success.
5. Assert downstream dependency metadata continues to reference the parent workflow id, not the child.

## Full Unit Suite

Before final verification, run:

```bash
./tools/test_unit.sh
```

## Hermetic Integration Suite

When Docker Compose is available in the environment, run:

```bash
./tools/test_integration.sh
```

If Docker is unavailable in a managed-agent workspace, report that integration verification is blocked by environment capability rather than substituting provider or credentialed tests.

## Verification Gate

Run `/moonspec-verify` for `specs/186-parent-owned-merge-automation/spec.md` and confirm:
- Jira issue MM-350 remains referenced in spec, tasks, verification, commit text, and pull request metadata.
- Every in-scope `DESIGN-REQ-*` maps to implementation and tests.
- Parent-owned child workflow semantics supersede detached completion semantics for this story.
