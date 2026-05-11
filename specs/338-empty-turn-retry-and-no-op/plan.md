# Plan 338 — Codex Empty-Turn Retry and Skill No-Op Contract

## Active Technologies

- Python 3.12 + Pydantic v2, Temporal Python SDK, pytest, existing MoonMind
  Codex managed-session runtime, existing batch-pr-resolver skill.
- No new persistent storage. Skill no-op signal lives in the artifact spool
  directory and is read once per turn at classification time. Run-level
  no-op disposition is recorded via existing Temporal memo / search
  attributes.

## Design

### Layered classification

The runtime is the source of truth for turn outcome. It already distinguishes
`completed`, `failed`, `running`, `interrupted`. We extend it with a single
new piece of information — a **disposition** carried in turn-response
metadata when relevant.

Classification flow on terminal-turn inspection:

1. If the Codex thread payload yields assistant text → `completed`,
   `metadata = {"assistantText": ...}`. (Unchanged.)
2. Else, if `skill_outcome.json` exists and declares `status="no_op"` →
   `completed`, `metadata = {"disposition": "no_op", "reason": ...}`.
3. Else, if rollout scan or thread payload yields a *structured error*
   (existing `error_text` path) → `failed`,
   `metadata = {"reason": ...}` with permanent classification.
4. Else (empty assistant text, no no-op signal, no structured error) →
   `failed`, `metadata = {"reason": "codex turn produced no assistant output"}`
   classified as **transient**.

The transient/permanent split is communicated to the activity wrapper via a
typed exception class (see “Retry surface” below). The runtime raises
**after** finalizing turn state (state.active_turn_id cleared,
last_turn_status="failed") so retries see a clean session.

### Skill outcome file location

The Codex managed runtime already writes its spool to a known directory
(`artifact_spool_path` in `CodexManagedSessionRuntime`). The skill writes to
`<artifact_spool>/skill_outcome.json`. The runtime reads it inside
`_completed_turn_without_assistant_outcome`, before falling through to the
default failure path.

Parse rules:

- File must be valid JSON, single object, `schema_version == 1`.
- Unknown fields ignored (forward-compatible).
- Invalid file (bad JSON, missing required fields, wrong `schema_version`)
  is logged and treated as **absent** (no no-op). This is fail-safe — a
  malformed declaration cannot upgrade a failure to a success.

### Retry surface

`agent_runtime.send_turn` activity wraps `CodexManagedSessionRuntime.send_turn`.
Today the runtime returns a Pydantic response with `status="failed"` and the
activity completes successfully from Temporal’s perspective — so the retry
policy never fires.

Change: the activity wrapper inspects the runtime response. When
`response.status == "failed"` and the runtime indicates **transient**
category, the wrapper raises `CodexTransientTurnError(reason)`. When
permanent, it raises `CodexPermanentTurnError(reason)`. Both are subclasses
of `ApplicationError`. The activity retry policy retries on the former and
marks the latter non-retryable.

The runtime communicates category via response metadata
(`metadata["failureClass"] = "transient" | "permanent"`). This is internal to
the activity↔runtime boundary; the workflow does not see it.

### Activity retry policy widening

`activity_catalog.py:874`:

```python
retries=_activity_retries(
    max_attempts=5,
    max_interval_seconds=600,
    non_retryable=("CodexPermanentTurnError",),
),
```

With the existing 5 s initial interval and 2.0× backoff (in
`agent_run.py:_retry_policy_for_route`), retry intervals are 5 s, 10 s, 20 s,
40 s, 80 s — five attempts span ~155 s. That is sufficient for transient
rate-limit windows without burning excessive wall-clock or API budget. We do
not change the initial interval in this story.

### Workflow handling of no-op

`codex_session_adapter.py` already classifies non-completed turn responses as
failure. We change the condition to:

- `turn_response.status == "completed"`: success path (unchanged), but
  the adapter checks `turn_response.metadata.get("disposition")`. If it is
  `"no_op"`, the agent run’s memo / search attributes record the disposition,
  and the run result summary carries the reason.
- `turn_response.status == "failed"`: failure path (unchanged).

The memo addition uses the existing memo-write seam in the agent run
workflow. No new memo / search attribute schema is introduced beyond
`mm_outcome_disposition` (string, optional, value `"no_op"` when applicable).

### Deleted code (Principle XIII)

The following entities are removed in the same change:

- `CodexSessionRuntimeState.last_assistant_text_missing` field (Pydantic
  attribute and alias `lastAssistantTextMissing`).
- `_handle` setdefault for `assistantTextMissing` (lines 506–507).
- `assistant_text_missing` kwarg on `_finalize_turn` and all call sites.
- `metadata["assistantTextMissing"]` write in `send_turn` and `_refresh_turn_state`.
- `assistantTextMissing` entry in `fetch_session_summary` metadata.
- All test assertions on `assistantTextMissing` (six tests in
  `test_codex_session_runtime.py`).

No alias, no compat shim. The flag never had downstream consumers outside
test assertions, so removal is mechanical.

### Batch-PR-resolver no-op declaration

`.agents/skills/batch-pr-resolver/bin/batch_pr_resolver.py` writes
`skill_outcome.json` next to its existing result file. The decision rule is
made in `main()` after the result payload is computed:

```python
if payload["created"] == 0 and not payload["errors"]:
    skill_outcome = {
        "schema_version": 1,
        "status": "no_op",
        "reason": "no_open_prs_matched",
        "evidence": {
            "requested": payload["requested"],
            "skipped": payload["skipped"],
        },
    }
    _write_artifacts(artifacts_dir / "skill_outcome.json", skill_outcome)
```

The existing exit-code-1 path (errors present) does not write the file —
errors fail the run normally.

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| I — Orchestrate, don’t recreate | PASS | No change to the adapter contract surface; we only extend internal classification of an existing managed runtime. |
| II — One-click deployment | PASS | No new dependencies or services. |
| III — Avoid vendor lock-in | PASS | Skill no-op contract is runtime-neutral by design (file in artifact spool). Codex-specific runtime classification logic stays behind the Codex adapter. |
| IV — Own your data | PASS | `skill_outcome.json` is plain JSON in operator-controlled storage. |
| V — Skills first-class | PASS | The no-op contract is opt-in: skills MAY declare a no-op without changing how they declare success. Minimal new ceremony. |
| VI — Bittersweet lesson | PASS | We delete the superseded #2063 plumbing in the same change rather than carrying it forward. Failure classification is anchored by tests. |
| VII — Runtime configurability | PASS | No new operator-tunable knobs introduced here; the no-op contract is part of the skill author surface, not operator surface. |
| VIII — Modular & extensible | PASS | Classification lives entirely behind the Codex managed-session runtime boundary. |
| IX — Resilient by default | PASS | Story is explicitly motivated by failure-classification correctness and retry coverage. In-flight compatibility is preserved (runtime change is activity-side; completed activities retain recorded results). |
| X — Continuous improvement | PASS | Adds the structured success / no-op / failed distinction the principle calls for. |
| XI — Spec-driven | PASS | This document set. |
| XII — Canonical docs separate from migration | PASS | All migration-only notes live here under `specs/338-…/`. No `docs/` edits required. |
| XIII — Delete don’t deprecate | PASS | The `assistantTextMissing` plumbing is removed in the same change. No alias. |

## Complexity Tracking

None. The change set is contained: one runtime file, one activity catalog
line, one adapter file (memo write), one skill bin script, plus tests.

## Replay / In-Flight Safety

- The runtime change lives in activity-side Python code (`runtime/codex_session_runtime.py`).
  Temporal does not replay activity bodies; activity results are recorded as
  history events. Workflows with already-completed `send_turn` activities
  retain their original `CodexManagedSessionTurnResponse` payload.
- The activity catalog retry-policy change applies only to activities
  scheduled after deploy. In-flight scheduled-but-not-started activities will
  pick up the new policy on their next attempt; this is safe because the
  policy strictly widens retries.
- The deleted `assistantTextMissing` field exists on persisted
  `CodexSessionRuntimeState` JSON for sessions that ran under #2063. The
  Pydantic state model uses `populate_by_name = True` with aliased fields;
  removing the field means existing JSON containing `lastAssistantTextMissing`
  will be ignored on load (extra fields are allowed by default in v2 unless
  the model is configured to forbid them — confirm `model_config` allows
  this; if it forbids, add `extra="ignore"`). Acceptance test covers this.
- Memo / search-attribute additions (`mm_outcome_disposition`) are
  forward-compatible: workflows that don’t set the attribute behave as today.

## Validation

- Unit tests under `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`:
  - Six PR #2063 tests flipped back to assert `failed` and
    `metadata["failureClass"] == "transient"`.
  - New tests: no-op signal honored when assistant text empty; malformed
    `skill_outcome.json` falls through to default failure path; no-op signal
    ignored when assistant text present.
- Workflow boundary test under
  `tests/integration/services/temporal/workflows/test_agent_run_codex_session_rollout.py`:
  - Empty turn → activity retries → eventual failure raises
    `CodexSessionRunFailedError`.
  - Empty turn + `skill_outcome.json{status:"no_op"}` → workflow ends with
    `succeeded` and run memo carries `mm_outcome_disposition="no_op"`.
- Activity unit test (new): `send_turn` activity wrapper translates
  `failureClass=transient` runtime response into `CodexTransientTurnError`,
  and `failureClass=permanent` into `CodexPermanentTurnError`.
- Skill test: `batch-pr-resolver` writes `skill_outcome.json` iff
  `created==0 and not errors`.

## Risks / Open Questions

- **Risk:** A skill that legitimately produces no assistant text but doesn’t
  yet opt in to `skill_outcome.json` will start failing after this change.
  *Mitigation:* `batch-pr-resolver` is the only such skill confirmed today;
  it is updated in the same change. Other skills always produce assistant
  text. If a regression surfaces, the fix is to add `skill_outcome.json`
  emission to the affected skill — same pattern.
- **Open:** Should the retry initial-interval be raised from 5 s to 30 s for
  `send_turn` specifically (rate-limit windows often require ≥30 s)? Out of
  scope for this story; tracked as a possible follow-up tuning change.
