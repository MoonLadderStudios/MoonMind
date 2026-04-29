# Quickstart: Normalize Step Type API and Executable Submission Payloads

## Focused Frontend Verification

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Expected result: Temporal task editing tests pass, including explicit Tool, Skill, and Preset draft reconstruction.

## Focused Backend Verification

```bash
./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py
```

Expected result: executable submission validation accepts Tool/Skill and rejects Preset, Activity, and mixed payloads.

## Final Verification

```bash
./tools/test_unit.sh
```

Expected result: full unit suite passes, or the exact environmental blocker is recorded in `verification.md`.
