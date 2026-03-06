# Data Model — Task Proposal Targeting Policy

## 1. ProposalPolicy payload object
- **Location**: `CreateJobRequest.payload.task.proposalPolicy` (optionally stored inside canonical payload + `task_context.json`).
- **Fields**:
  - `targets: list[str]` → subset of `{"project", "moonmind"}`. Missing means fall back to `MOONMIND_PROPOSAL_TARGETS` env.
  - `maxItems: {"project": int, "moonmind": int}` → caps per-target proposal count. Defaults: project=3, moonmind=2 when omitted.
  - `minSeverityForMoonMind: str` → severity floor label (`info`, `medium`, `high`) mapped to detector output.
- **Validation**: reject unknown targets, negative counts, or severity not in the allowed set. Policy is immutable during a run and logged for audit.

## 2. EffectiveProposalPolicy runtime view
- **Location**: `moonmind/agents/codex_worker/worker.py` helper that merges (global defaults + per-task overrides + detection signals).
- **Derived Fields**:
  - `allow_project` / `allow_moonmind` bools.
  - `max_items_project` / `max_items_moonmind` ints.
  - `severity_floor` enumerated for gating.
  - `remaining_project_slots`, `remaining_moonmind_slots` counters used while iterating proposals from `task_proposals.json`.

## 3. MoonMind CI proposal metadata
- **Repository**: `MOONMIND_CI_REPOSITORY` env string (default `MoonLadderStudios/MoonMind`). The worker rewrites `taskCreateRequest.payload.repository` to this value whenever a proposal is flagged for MoonMind.
- **Category + Title**: forced to `run_quality` category with `[run_quality] <summary>` slug.
- **Tags**: normalized set drawn from `{retry, duplicate_output, missing_ref, conflicting_instructions, flaky_test, loop_detected, artifact_gap}`.
- **Origin Metadata Schema**:
  ```json
  {
    "triggerRepo": "org/project",
    "triggerJobId": "uuid",
    "triggerStepId": "prepare|execute|publish|verify", // optional
    "signal": {
      "severity": "high",
      "retries": 2,
      "duplicateRatio": 0.78,
      "missingRefs": ["specs/plan.md"],
      "conflictClasses": ["instructions"]
    },
    "branch": {
      "starting": "main",
      "working": "feature/foo"
    }
  }
  ```
- **Validation**: API requires `triggerRepo`, `triggerJobId`, and `signal` dict before persisting.

## 4. Review priority derivation
- **Input**: union of tags + `signal.severity` + counts inside origin metadata.
- **Mapping**:
  - `HIGH`: `loop_detected`, `missing_ref` with >0 entries, `conflicting_instructions`, retry exhaustion (>=2 back-to-back failures), or `signal.severity == "high"`.
  - `NORMAL`: `duplicate_output`, `retry` below exhaustion threshold, `artifact_gap`.
  - `LOW`: informational `flaky_test` without repeat occurrences.
- **Storage**: persisted in `task_proposals.review_priority`. Clients can still update via `/priority`, but create-time values originate from derived logic.

## 5. Dashboard filtering metadata
- **Client DTOs**: `TaskProposalModel.tags` already carries normalized tags; we add UI toggles to group by `repository`, `category`, `tags`, and show `origin.metadata.signal` payload alongside the detail view.
- **No schema migration**: All data lives inside existing JSON columns (`task_create_request`, `origin_metadata`, `tags`).
