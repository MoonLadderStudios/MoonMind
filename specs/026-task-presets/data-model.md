# Data Model: Task Presets Strategy Alignment

## Overview

No schema changes are required. Alignment work updates seeded data content and keeps persistence model unchanged.

## Seed Document Contract (behavioral)

### `TaskPresetSeedDocument` (`api_service/data/task_step_templates/agentkit-orchestrate.yaml`)
- `inputs.orchestration_mode` (`runtime|docs`) drives mode-specific gate wording in `steps[].instructions`.
- Validation steps retain `--mode {{ inputs.orchestration_mode }}` placeholders so runtime/docs behavior is selected at expansion time, not hardcoded during seeding.
- Final step instructions enforce publish handoff semantics for runtime execution (report only, no commit/push/PR actions).

## Existing Tables (unchanged)

### `task_step_templates`
- Stores top-level preset identity (`slug`, `scope_type`, `scope_ref`), display metadata, and `required_capabilities`.
- `latest_version_id` points at active/selected version row.

### `task_step_template_versions`
- Stores immutable version payload (`version`, `inputs_schema`, `steps`, `annotations`, `required_capabilities`).
- Seed alignment migration updates the existing seeded `agentkit-orchestrate` version row payload from YAML.

## Alignment Data Changes

- `agentkit-orchestrate` template row:
  - Refresh `required_capabilities` and `is_active` from YAML.
- `agentkit-orchestrate` version row (`1.0.0`):
  - Refresh `steps`, `required_capabilities`, and `seed_source` from YAML, including mode-aware instruction text.

## Invariants

- Scope remains `global` for seeded preset.
- Version remains `1.0.0` for the seeded row updated by migration.
- Migration is best-effort and idempotent: missing seed file or missing target rows does not fail upgrade.
