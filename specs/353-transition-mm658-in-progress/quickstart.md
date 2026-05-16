# Quickstart: Verify MM-658 Transitions to "In Progress"

This story is a one-shot agent run that drives `MM-658` to status `In Progress` through MoonMind's trusted Jira tool surface. The independent test confirms either a successful transition or a deterministic named-error stop.

## Prerequisites

- A MoonMind agent runtime with the trusted Jira tool surface registered (`jira.get_issue`, `jira.get_transitions`, `jira.transition_issue`).
- The Jira binding configured with credentials that can read and transition `MM-658` in the project that owns it. **Credentials are NOT exposed to the agent runtime** — they live in the trusted service.
- No additional services, fixtures, or migrations are required.

## Test Strategy

| Tier | What we run | When |
|---|---|---|
| Unit | `./tools/test_unit.sh` | Always before/after the run; confirms existing trusted Jira tool unit tests still pass (no new unit code is added by this story). |
| Hermetic Integration CI | `./tools/test_integration.sh` | Always; confirms no regression in the integration_ci suite. This story adds no new integration_ci coverage. |
| Provider Verification | `./tools/test_jules_provider.sh` | Manual / nightly only; not in CI per repo policy (see `CLAUDE.md`). Run when validating against a real Jira tenant. |
| Independent end-to-end | The agent run + post-run Jira check below | Always; the canonical proof that the story succeeded. |

## Independent End-to-End Test

### Step 1 — Capture pre-state

Read `MM-658` through the trusted Jira tool surface (or via Jira UI) and record the current `status.name`. Call this `priorStatus`.

### Step 2 — Run the workflow

Trigger the MoonMind agent run that consumes this spec. The agent will perform calls 1 → 4 from `contracts/transition-mm658.md` (skipping 2/3 on a no-op, skipping 3/4 on a `stopped:*` outcome).

### Step 3 — Verify the run report

Fetch the run report artifact and confirm the contract:

- `issueKey == "MM-658"`.
- Exactly one `action` value populated: `transitioned`, `noop_already_in_progress`, or `stopped`.
- Exactly one `outcome` populated.
- For `outcome="transitioned"`: `verifiedFinalStatus == "In Progress"`, `transition.id` and `transition.toStatusName` populated, `missingFields == []`, `availableTransitions == []`, `errorReason == null`.
- For `outcome="noop_already_in_progress"`: `priorStatus == "In Progress"`, `verifiedFinalStatus == "In Progress"`, `transition.*` are `null`.
- For any `stopped:*`: `transition.*` are `null`, an appropriate `errorClass` and a redacted `errorReason` (≤ 500 chars).
- For `stopped:no_matching_transition` and `stopped:ambiguous_transition`: `availableTransitions` is populated (id, name, target status name).
- For `stopped:missing_required_fields`: `missingFields` carries field IDs only — never field values.
- No secret-pattern strings appear anywhere in the report (grep the report for `ghp_`, `github_pat_`, `AIza`, `ATATT`, `AKIA`, `Bearer `, `token=`, `password=`, `BEGIN PRIVATE KEY`).

### Step 4 — Verify in Jira

Fetch `MM-658` directly from the Jira tracker (UI or trusted tool):

- If the run reported `transitioned` or `noop_already_in_progress`, the live Jira status of `MM-658` MUST be `In Progress`.
- If the run reported a `stopped:*` outcome, the live Jira status of `MM-658` MUST equal `priorStatus` from Step 1 (zero partial mutation, SC-002).
- `MM-658`'s description, labels, assignee, comments, and other fields MUST be unchanged versus Step 1 (FR-008, SC-004).

### Step 5 — Verify isolation

Spot-check at least one neighboring Jira issue (e.g., `MM-657` or `MM-659`) to confirm it is unmodified. The run MUST NOT have updated any other issue (SC-004).

## Commands

| Action | Command |
|---|---|
| Run unit suite | `./tools/test_unit.sh` |
| Run hermetic integration CI | `./tools/test_integration.sh` |
| Run provider verification (live Jira credentials required) | `./tools/test_jules_provider.sh` |

## Outcome Cheat Sheet

| Observed outcome | Live Jira status of MM-658 after the run | Acceptable? |
|---|---|---|
| `transitioned` | `In Progress` | Yes (SCN-001) |
| `noop_already_in_progress` | `In Progress` (unchanged from prior `In Progress`) | Yes (SCN-002) |
| `stopped:no_matching_transition` | `priorStatus` (unchanged) | Yes (SCN-003) |
| `stopped:ambiguous_transition` | `priorStatus` (unchanged) | Yes (SCN-004) |
| `stopped:issue_not_found` | n/a (issue not visible) | Yes — operator must investigate Jira credentials/visibility |
| `stopped:missing_required_fields` | `priorStatus` (unchanged) | Yes — surfaces required-field names without guessing values |
| `stopped:auth_or_permission` | `priorStatus` (unchanged) | Yes — sanitized error |
| `stopped:validation_failure` | `priorStatus` (unchanged) | Permanent Jira validation/policy failure; operator must inspect transition/request configuration rather than retrying as an outage |
| `stopped:tool_unavailable` | `priorStatus` (unchanged) | Yes — operator must enable the trusted Jira binding |
| `stopped:transient_failure` | `priorStatus` (unchanged) or partial — operator MUST recheck Jira | Acceptable as a stop, but requires a re-run when Jira is healthy |
| `stopped:final_status_mismatch` | Some other status (Jira advanced through an intermediate state) | Stop is correct; operator inspects Jira workflow design |

Any other observation is a defect: file it as a regression against this spec.
