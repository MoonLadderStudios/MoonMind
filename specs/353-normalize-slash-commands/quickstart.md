# Quickstart: Normalize Slash-Leading Instructions

## Test-First Validation

1. Run the focused task contract tests before implementation and confirm the new MM-684 cases fail:

```bash
pytest tests/unit/workflows/tasks/test_task_contract.py -q
```

Expected before implementation: new MM-684 tests fail because runtime command metadata is not produced or validated.

2. Implement the parser, metadata model, snapshot attachment, and supplied metadata validation in `moonmind/workflows/tasks/task_contract.py`.

3. Re-run the focused tests:

```bash
pytest tests/unit/workflows/tasks/test_task_contract.py -q
```

Expected after implementation: all task contract tests pass.

4. Run final unit verification:

```bash
./tools/test_unit.sh
```

Expected result: full unit suite passes in the managed-agent local test mode.

## End-to-End Story Checks

- `/review` task instructions preserve raw instructions and add task-level detected command metadata.
- `/simplify` step instructions preserve raw instructions and add step-level detected command metadata with target step id.
- `/future-command` is accepted as opaque for slash-pass-through runtimes.
- `\/review` records escaped literal metadata and does not require runtime recognition.
- `/src/app.ts is broken` does not become an executable runtime command.
- Conflicting frontend-supplied `runtimeCommand` metadata is rejected by backend normalization.

## Final MoonSpec Verification

Run final verification after implementation and tests:

```bash
/speckit.verify specs/353-normalize-slash-commands
```

Verification must confirm that `MM-684`, the canonical Jira preset brief, and all in-scope `DESIGN-REQ-*` mappings remain preserved.
