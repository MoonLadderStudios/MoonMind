# Quickstart: Operations Controls Exposed as Authorized Commands

## Targeted TDD Flow

1. Add failing API tests for `/api/system/worker-pause`:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_system_operations.py
```

2. Add failing service tests for command validation, audit persistence, and subsystem signal delegation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/test_system_operations.py
```

3. Add or update UI tests for Settings -> Operations worker controls:

```bash
./tools/test_unit.sh --ui-args frontend/src/components/settings/OperationsSettingsSection.test.tsx
```

4. Add a hermetic integration test for the configured route shape:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_integration.sh
```

5. Implement the backend route/service and UI payload adjustments.

6. Run final required unit verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Manual Smoke Scenario

1. Start MoonMind locally.
2. Open `/tasks/settings?section=operations`.
3. Confirm worker state and recent operation history load.
4. Submit Pause Workers with mode, reason, and confirmation.
5. Confirm the response shows paused/draining or quiesced state and a recent pause audit entry.
6. Submit Resume Workers with reason.
7. Confirm the response shows running state and a recent resume audit entry.
8. Attempt the POST as a non-admin user when auth is enabled and confirm the command is rejected.

## Traceability Check

```bash
rg -n "MM-542|DESIGN-REQ-002|DESIGN-REQ-013|DESIGN-REQ-014" specs/272-operations-controls-authorized-commands
```
