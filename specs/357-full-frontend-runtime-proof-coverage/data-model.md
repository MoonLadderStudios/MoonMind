# Data Model: Full Frontend Runtime Proof Coverage

## Validation Tier

Represents one level of runtime proof validation.

- `tier_id`: Stable identifier: `tier1_compile`, `tier2_automation`, or `tier3_entry_smoke`.
- `name`: Human-readable tier name.
- `required`: Whether the tier is required for THOR-406 completion.
- `status`: `passed`, `failed`, `blocked`, or `ci_only`.
- `command`: Exact command executed or attempted.
- `exit_code`: Process exit code when a command ran.
- `summary`: Concise result summary.

Validation rules:

- Every required tier must produce an evidence record.
- `ci_only` is valid only after local tooling is unavailable and Docker fallback has been attempted, or Docker fallback is explicitly unavailable with a concrete recorded reason.
- `passed` requires exit code `0` and enough evidence to satisfy the tier's required coverage.

## Runtime Evidence Record

Represents reviewer-facing proof for one validation tier.

- `tier_id`: Links to the validation tier.
- `command`: Exact command.
- `exit_code`: Exact exit code.
- `log_tactics_lines`: Key `LogTactics` lines captured from the run.
- `output_summary`: Short summary of relevant output.
- `artifact_path`: Optional path to a detailed local artifact.
- `blocked_reason`: Required when status is `blocked` or `ci_only`.

Validation rules:

- Evidence records must not depend on full raw log dumps for review.
- Each record must include at least one `LogTactics` line or an explicit reason no line could be emitted.
- Commands and exit codes must be preserved exactly.

## Frontend Flow Coverage Set

Represents the Tier 2 runtime behavior areas that must be covered.

- `home_startup`: Home startup coverage result.
- `generated_home_navigation`: Generated Home navigation coverage result.
- `play_panel`: Play panel coverage result.
- `options_panel`: Options panel coverage result.
- `modal_behavior`: Modal behavior coverage result.
- `online_coop_blocking`: Blocked Online Co-op coverage result.
- `generated_selection_telemetry`: Generated selection telemetry coverage result.

Validation rules:

- Tier 2 passes only when every required flow area is covered in the same documented validation run.
- Missing telemetry evidence fails Tier 2 even when visible menu flows pass.

## Frontend Entry Route

Represents the runtime entry point used by Tier 3 smoke validation.

- `route`: `/Game/Maps/MainMenu` or the active frontend entry route.
- `source`: `default`, `project_config`, or `explicit_override`.
- `resolved`: Whether the route exists in the target project.
- `smoke_status`: Result of entering the route.

Validation rules:

- `/Game/Maps/MainMenu` is preferred when present.
- An active project entry route is acceptable when documented in the evidence record.
