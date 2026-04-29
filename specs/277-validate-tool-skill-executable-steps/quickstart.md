# Quickstart: Validate Tool and Skill Executable Steps

## Focused Test-First Loop

1. Add service tests for MM-557 validation behavior:

   ```bash
   ./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py
   ```

2. Confirm the new tests fail before implementation for missing Tool/Skill validation.

3. Implement schema and service validation in:

   ```text
   api_service/api/schemas.py
   api_service/services/task_templates/catalog.py
   api_service/services/task_templates/save.py
   ```

4. Re-run the focused test command until it passes.

## Final Verification

Run the managed unit suite when feasible:

```bash
./tools/test_unit.sh
```

Then run `/moonspec-verify` equivalent by checking `spec.md`, `plan.md`, `tasks.md`, changed code, and test evidence against MM-557.
