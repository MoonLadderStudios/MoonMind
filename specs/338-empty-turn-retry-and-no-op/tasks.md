# Tasks 338 — Codex Empty-Turn Retry and Skill No-Op Contract

Each task is intended to be a focused commit. Run unit tests after each
runtime change.

## T1 — Add typed runtime exceptions and failureClass metadata

Files: `moonmind/workflows/temporal/runtime/codex_session_runtime.py`

- Define `CodexTransientTurnError(ApplicationError)` and
  `CodexPermanentTurnError(ApplicationError)` (subclasses of
  `temporalio.exceptions.ApplicationError`, with `non_retryable=True` on the
  permanent variant). Place these adjacent to the runtime class so they are
  importable by the activity wrapper.
- Extend the runtime so terminal-turn classification produces a
  `failureClass: "transient" | "permanent"` value alongside the existing
  `(status, error_text)` tuple, without yet wiring it into the response
  metadata.

Tests: none yet (refactor only).

## T2 — Revert PR #2063 runtime classification

Files: `moonmind/workflows/temporal/runtime/codex_session_runtime.py`,
       `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`

- Flip `_rollout_terminal_outcome_from_scan` and
  `_completed_turn_without_assistant_outcome` return values back to `"failed"`
  for the three empty-output cases (lines 1175, 1207, 1209 today).
- Re-flip the six PR #2063 unit tests to assert `failed` instead of `ready` /
  `completed` and remove their `assistantTextMissing` assertions.

Run: `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_codex_session_runtime.py`.

## T3 — Delete `assistantTextMissing` plumbing (Principle XIII)

Files: `moonmind/workflows/temporal/runtime/codex_session_runtime.py`,
       `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`

- Remove `CodexSessionRuntimeState.last_assistant_text_missing` and its
  alias.
- Remove the `_handle` setdefault for `assistantTextMissing`.
- Remove `assistant_text_missing` kwarg on `_finalize_turn` and its three
  call sites.
- Remove `metadata["assistantTextMissing"]` in `send_turn` and
  `_refresh_turn_state`.
- Remove `assistantTextMissing` entry in `fetch_session_summary` metadata.
- Delete corresponding test assertions.

Confirm `CodexSessionRuntimeState` model accepts persisted JSON that still
contains `lastAssistantTextMissing` (Pydantic v2 default is to ignore extra
fields on load; verify and pin if needed).

Run: full unit test pass — `./tools/test_unit.sh`.

## T4 — Implement no-op contract reader

Files: `moonmind/workflows/temporal/runtime/codex_session_runtime.py`

- Inside `_completed_turn_without_assistant_outcome`, before falling through
  to the “produced no assistant output” failure return, attempt to read
  `<artifact_spool>/skill_outcome.json`.
- Parse rules: single JSON object; `schema_version == 1`; `status == "no_op"`
  triggers success path with disposition. Any parse error or unexpected
  schema is logged once and treated as absent.
- On no-op, return `("completed", None)` and signal disposition + reason via
  a new field on `_CompletedTurnInspection` (or equivalent return shape) so
  the caller can attach metadata.

Tests (new in `test_codex_session_runtime.py`):
- Valid no-op file → status `completed`, metadata `disposition="no_op"`,
  reason carried through.
- Malformed JSON → fail-safe to default failure.
- Missing `schema_version` or wrong value → fail-safe to default failure.
- `status="failed"` declared in file with empty assistant text → still
  classifies as failure (no upgrade attempt).
- Assistant text present + no-op file present → assistant text wins (no
  disposition).

## T5 — Translate failure category at the activity wrapper

Files: `moonmind/workflows/temporal/activity_runtime.py`,
       `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`
       (or new file under `tests/unit/services/temporal/activities/`)

- In the `agent_runtime.send_turn` activity body, after the runtime returns,
  inspect the response. When `status == "failed"`, raise
  `CodexTransientTurnError(reason)` or `CodexPermanentTurnError(reason)`
  according to the runtime’s `failureClass`.
- The activity must finalize runtime state before raising (this is already
  what the runtime does internally; verify).

Tests:
- Mocked runtime returning `failed/transient` → activity raises
  `CodexTransientTurnError`.
- Mocked runtime returning `failed/permanent` → activity raises
  `CodexPermanentTurnError` with non-retryable application error semantics.

## T6 — Widen send_turn retry policy

Files: `moonmind/workflows/temporal/activity_catalog.py`

- Change line 874 (the `send_turn` activity retries) to:
  `_activity_retries(max_attempts=5, max_interval_seconds=600, non_retryable=("CodexPermanentTurnError",))`.

Tests: existing activity-catalog tests assert retry policy shape; update if
needed.

## T7 — Workflow no-op disposition recording

Files: `moonmind/workflows/adapters/codex_session_adapter.py`,
       `moonmind/workflows/temporal/workflows/agent_run.py`
       (memo write site)

- When a successful turn response carries
  `metadata["disposition"] == "no_op"`, the adapter forwards the disposition
  to the agent run’s memo via the existing memo-write seam:
  `mm_outcome_disposition = "no_op"`.
- The agent run’s result summary carries `outcome="no_op"` and the reason
  from the turn metadata.
- Successful turns without no-op disposition are unchanged.

Tests (new in `tests/integration/services/temporal/workflows/test_agent_run_codex_session_rollout.py`):
- Empty turn → activity retries up to limit → `CodexSessionRunFailedError`
  raised, workflow ends `failed`.
- Empty turn with no-op skill outcome → workflow ends `succeeded` with
  memo `mm_outcome_disposition="no_op"`.

## T8 — `batch-pr-resolver` opt-in

Files: `.agents/skills/batch-pr-resolver/bin/batch_pr_resolver.py`,
       `tests/unit/.../test_batch_pr_resolver.py`
       (find the existing test file; create one if absent — there should
       already be coverage for this skill)

- After computing `payload`, if `payload["created"] == 0 and not payload["errors"]`,
  write `skill_outcome.json` with the schema in spec §R2.
- Do not write when `errors` is non-empty.

Tests:
- `_write_artifacts` is invoked for `skill_outcome.json` only in the no-op
  case.
- Exit code remains 0 in the no-op case (already true today).

## T9 — Update the agent skill docs

Files: `docs/Tasks/AgentSkillSystem.md` (or the most authoritative
       skill-author doc; verify path).

- Add a short subsection: “Declaring an intentional no-op”. Describe the
  `skill_outcome.json` schema and the runtime semantics: a skill MAY emit
  the file to declare a deliberate no-op; absence falls back to default
  classification (assistant text required for success).
- Keep the doc declarative (Constitution Principle XII): describe the
  contract, not the migration story.

## T10 — Manual verification

- Trigger `batch-pr-resolver` in an environment where it should be a no-op
  (no open PRs in the target repo, or all open PRs filtered). Confirm:
  - The agent run ends `succeeded` with memo `mm_outcome_disposition="no_op"`.
  - `artifacts/skill_outcome.json` is present.
- Trigger `batch-pr-resolver` and force a fail-fast (e.g., temporarily
  invalidate auth). Confirm:
  - `send_turn` retries 5 times with the expected backoff in Temporal
    history.
  - On exhaustion the workflow ends `failed` and memo carries no `no_op`
    disposition.

Document the verification run IDs in this file once executed.
