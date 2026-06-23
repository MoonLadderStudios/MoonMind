# Story Breakdown: MoonMind resilience P-priority issues

- Source design: inline user request
- Source reference path: none (pasted design text)
- Source document class: declarative-text
- Story extraction date: 2026-06-23T12:01:11.120100Z
- Requested output mode: jira

## Design Summary

MoonMind resilience is defined as an operational envelope for long-running AI agent work, built around Temporal durability, managed runtime supervision, provider capacity policy, step ledgers, checkpoint evidence, sandboxing, artifacts, and outbound safety gates. The P-priority issues convert the strongest primitives into enforced, inspectable operator outcomes: explicit persisted policy, default checkpoint-backed recovery, structured provider failure events, cross-runtime managed-session conformance, and incident reconstruction observability.

## Coverage Points

- DESIGN-REQ-001: Temporal durability remains the orchestration backbone (constraint)
  - Source: Executive assessment / Durable orchestration
  - Resilience stories must preserve Temporal as the durable execution substrate with compact workflow metadata, routed activities, and replay-safe boundaries.
- DESIGN-REQ-002: Managed runtime supervision handles stuck CLI processes (requirement)
  - Source: Managed runtime supervision
  - Runtime behavior must account for heartbeat, timeout, output-progress, rate-limit, and stalled-progress signals rather than treating CLI agents like ordinary API calls.
- DESIGN-REQ-003: Self-heal behavior uses explicit budgets and failure classes (state-model)
  - Source: Self-heal primitives
  - Recovery behavior must keep attempt budgets, idle/wall-clock limits, no-progress thresholds, and failure classification observable and enforceable.
- DESIGN-REQ-004: Provider resilience uses slots, cooldowns, and scoped classification (integration)
  - Source: Provider resilience
  - Provider-profile managers own capacity, leases, cooldowns, assignment, and failure classification rather than relying on blind retry.
- DESIGN-REQ-005: Step execution separates retries, semantic re-execution, checkpoints, and side effects (state-model)
  - Source: Step execution, checkpointing, and idempotency
  - Step ledgers, immutable manifests, checkpoint refs, idempotency keys, and side-effect disposition are central recovery primitives.
- DESIGN-REQ-006: Recovery must fail closed instead of silently rerunning (constraint)
  - Source: P0 checkpoint-backed recovery
  - Checkpoint validation or restoration failures must block resume with evidence, not degrade to a full rerun.
- DESIGN-REQ-007: Safety governance and outbound scanning require broad enforcement (security)
  - Source: Defense in depth / P-priority recommendations
  - Sandboxing, file allowlists, provider profiles, secret redaction, side-effect gates, and outbound scanning must be enforced at high-risk boundaries.
- DESIGN-REQ-008: Resilience policy must be visible per run and step (artifact)
  - Source: P0 resilience policy
  - Operators need a versioned ResiliencePolicy envelope attached to workflow runs and step executions.
- DESIGN-REQ-009: Failed runs must produce recovery manifests (artifact)
  - Source: P0 checkpoint-backed recovery
  - Failed runs should leave a manifest with last accepted step, failed step, ordinal, checkpoint refs, validation result, side-effect dispositions, resume allowance, and blocked reason.
- DESIGN-REQ-010: Provider errors should use structured runtime-adapter events (integration)
  - Source: P1 provider structured events
  - Adapters should emit canonical provider event fields, with text matching retained as fallback only.
- DESIGN-REQ-011: Managed-session resilience should be uniform across runtimes (requirement)
  - Source: P1 cross-runtime conformance
  - Codex, Claude, Gemini, and future runtimes should pass one shared managed-session conformance suite before being surfaced as session-capable.
- DESIGN-REQ-012: Observability should reconstruct incidents end to end (observability)
  - Source: P1 incident reconstruction
  - Operators should answer policy, provider, credential, failed step, progress, changes, side effects, checkpoints, cost, trace, and log questions from correlated evidence.
- DESIGN-REQ-013: Mission Control and artifacts remain the evidence surface (observability)
  - Source: Bottom line / Observability
  - Durable artifacts, trace refs, logs, and Mission Control deep links should expose what happened without making raw worker internals the source of truth.
- DESIGN-REQ-014: Only explicit P-priority resilience issues become Jira stories here (non-goal)
  - Source: What remains to be implemented / Out of scope
  - Lower-priority findings such as HA guidance, readiness, developer guardrails, RAG integration, and Responses API parity are acknowledged but not converted into this P0/P1 story set.

## Ordered Story Candidates

### STORY-001: Persist per-run resilience policy envelopes (P0)

- Short name: policy-envelope
- Source reference: inline user request
- Source sections: P0: Make resilience policy explicit and persisted, What is not working well enough / Resilience policy is spread across too many places
- Description: As a MoonMind operator, I need every workflow run and step execution to carry the exact versioned resilience policy that governed it, so failure review does not require reconstructing behavior from scattered constants, environment variables, provider profiles, and workflow defaults.
- Independent test: Submit or construct a workflow run with known resilience configuration and verify run-level and step-level records expose the exact policy version and values used by workflow/activity boundaries without storing raw secrets or large payloads in workflow history.
- Dependencies: none
- Acceptance criteria:
  - Every new workflow run records a versioned ResiliencePolicy reference or compact envelope before step execution begins.
  - Every step execution can be traced to the policy values governing attempts, timeouts, no-progress handling, provider cooldowns, checkpoint requirements, side-effect idempotency, outbound scanning, observability, and cost attribution.
  - Policy values are deterministic for the run and do not require inference from environment variables, hard-coded constants, or provider-manager state after the fact.
  - The policy contract preserves Temporal payload discipline: large details are artifact-backed and secrets are references only.
  - Boundary-level tests cover the real worker/activity invocation shape and fail-fast handling for unsupported or missing policy values.
- Requirements:
  - Define a typed, versioned resilience policy contract.
  - Attach the policy to workflow run and step execution records or artifacts.
  - Preserve Temporal determinism and compact metadata rules.
  - Make the applied policy inspectable for forensic review.
- Needs clarification: none
- Owned coverage:
  - DESIGN-REQ-001: STORY-001 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-002: STORY-001 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-003: STORY-001 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-004: STORY-001 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-005: STORY-001 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-007: STORY-001 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-008: STORY-001 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-014: STORY-001 explicitly owns this point through its scope, requirements, and acceptance criteria.

### STORY-002: Default failed runs to checkpoint-backed recovery manifests (P0)

- Short name: recovery-manifest
- Source reference: inline user request
- Source sections: P0: Make checkpoint-backed recovery the default failed-run path, Step execution, checkpointing, and idempotency
- Description: As a MoonMind operator, I need failed runs to end with a recovery manifest backed by checkpoint evidence, so I can resume from the last valid point or understand exactly why resume is blocked without risking a silent full rerun.
- Independent test: Run or simulate a failed multi-step execution with one valid checkpoint and one invalid checkpoint case, then verify the failed execution produces a manifest that allows resume only for the valid case and blocks resume with an exact reason for the invalid case.
- Dependencies: STORY-001
- Acceptance criteria:
  - Every failed run produces a recovery manifest artifact or compact reference before terminal failure is reported.
  - The manifest names the last accepted step, failed logical step, execution ordinal, checkpoint refs, validation result, side-effect dispositions, resume allowance, and blocked reason when applicable.
  - Resume does not silently degrade to a full rerun when checkpoint validation or restoration fails.
  - Side effects are classified as accepted, discarded, blocked, or needing compensation before resume is allowed.
  - Tests cover missing, corrupted, unauthorized, and workspace-policy-incompatible checkpoint evidence.
- Requirements:
  - Make checkpoint-backed recovery the default failed-run path.
  - Validate checkpoint evidence before allowing resume.
  - Represent side-effect disposition in the recovery contract.
  - Expose recovery evidence through durable artifacts and execution-linked metadata.
- Needs clarification: none
- Owned coverage:
  - DESIGN-REQ-001: STORY-002 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-005: STORY-002 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-006: STORY-002 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-007: STORY-002 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-009: STORY-002 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-013: STORY-002 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-014: STORY-002 explicitly owns this point through its scope, requirements, and acceptance criteria.

### STORY-003: Emit structured provider failure events from runtime adapters (P1)

- Short name: provider-events
- Source reference: inline user request
- Source sections: P1: Move provider failure handling from string matching to structured events, Provider resilience is modeled as slots, cooldowns, and classification
- Description: As a MoonMind operator, I need runtime adapters to emit canonical structured provider failure events, so cooldowns, retry decisions, and operator guidance do not depend primarily on brittle provider or CLI wording.
- Independent test: Inject representative structured provider errors and legacy text-only errors for at least two runtimes and verify cooldown, auth, capacity, and retry recommendations are derived from structured fields first and fallback markers second.
- Dependencies: none
- Acceptance criteria:
  - Runtime adapter contracts include canonical provider failure event fields.
  - Provider-profile cooldown decisions prefer retry_after_seconds/reset_at/quota_scope over string markers when present.
  - Auth, rate-limit, capacity, and credential-scope failures produce distinct sanitized operator summaries.
  - Raw provider details are stored by reference or sanitized artifact, not leaked into ordinary logs or workflow payloads.
  - Tests cover structured events, fallback text markers, unknown provider_error_class, and missing retry metadata.
- Requirements:
  - Define structured provider failure event contract.
  - Update adapter and provider-manager boundaries to consume canonical event data.
  - Retain text matching as fallback only.
  - Preserve sanitized diagnostics and secret-safety behavior.
- Needs clarification: none
- Owned coverage:
  - DESIGN-REQ-003: STORY-003 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-004: STORY-003 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-010: STORY-003 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-012: STORY-003 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-014: STORY-003 explicitly owns this point through its scope, requirements, and acceptance criteria.

### STORY-004: Add cross-runtime managed-session conformance suite (P1)

- Short name: runtime-conformance
- Source reference: inline user request
- Source sections: P1: Build cross-runtime managed-session conformance, Runtime resilience is not yet uniform
- Description: As a MoonMind maintainer, I need Codex, Claude, Gemini, and future managed-session runtimes to pass one shared conformance suite, so runtime resilience capabilities are truthful, uniform, and regression-tested before Mission Control exposes them as session-capable.
- Independent test: Run the conformance suite against a mock compliant adapter and a deliberately incomplete adapter; verify the compliant adapter passes and the incomplete adapter reports precise missing capabilities without being surfaced as session-capable.
- Dependencies: STORY-001
- Acceptance criteria:
  - A shared managed-session conformance suite covers launch, turn control, interrupt, reset/epoch, resume, terminate, rate-limit, no-progress, checkpoint, scan, and correlation behavior.
  - Runtime adapters expose enough metadata for the suite to determine session-capability truthfully.
  - Non-conforming runtimes fail with actionable capability gaps rather than being treated as partially session-capable.
  - The suite includes boundary-level tests for adapter invocation shapes and trace/artifact correlation.
  - Existing Codex managed-session behavior remains covered by the suite.
- Requirements:
  - Define cross-runtime managed-session conformance expectations.
  - Run conformance at adapter boundaries.
  - Prevent unsupported runtimes from being surfaced as session-capable.
  - Cover checkpoint, outbound scan, and observability behavior uniformly.
- Needs clarification: none
- Owned coverage:
  - DESIGN-REQ-001: STORY-004 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-002: STORY-004 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-005: STORY-004 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-006: STORY-004 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-007: STORY-004 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-011: STORY-004 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-013: STORY-004 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-014: STORY-004 explicitly owns this point through its scope, requirements, and acceptance criteria.

### STORY-005: Correlate run evidence for incident reconstruction (P1)

- Short name: incident-reconstruction
- Source reference: inline user request
- Source sections: P1: Turn observability into incident reconstruction, Observability is not yet deep enough for hard incidents
- Description: As a MoonMind operator investigating a failed run, I need policy, provider, credential, step, progress, change, side-effect, checkpoint, cost, trace, log, and artifact evidence correlated in one incident reconstruction path, so I can answer what happened without reading raw worker internals.
- Independent test: Create or simulate a failed execution with provider failure, step output, blocked side effect, checkpoint, and cost metadata, then verify the incident view/projection links all evidence with stable correlation IDs and no raw secrets.
- Dependencies: STORY-001, STORY-002, STORY-003
- Acceptance criteria:
  - A failed run exposes correlated evidence for policy, provider/profile/credential source, failed step, progress signals, workspace changes, accepted/blocked side effects, checkpoint restore candidate, cost, trace spans, logs, and artifacts.
  - Trace IDs or equivalent correlation IDs propagate through API, workflow, activity, provider, side-effect, log, artifact, and step-manifest boundaries.
  - Mission Control or report projections link to durable artifacts rather than duplicating large evidence in workflow history.
  - Secret-bearing values and raw provider payloads are redacted, referenced, or artifact-gated according to policy.
  - Tests cover a hard incident with both accepted and blocked side effects plus a provider failure event.
- Requirements:
  - Propagate stable correlation identifiers end to end.
  - Add trace refs to step manifests and incident projections.
  - Include cost-attribution settings and observed cost where available.
  - Link Mission Control/report surfaces to durable evidence.
- Needs clarification: none
- Owned coverage:
  - DESIGN-REQ-001: STORY-005 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-002: STORY-005 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-004: STORY-005 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-005: STORY-005 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-007: STORY-005 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-008: STORY-005 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-009: STORY-005 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-010: STORY-005 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-012: STORY-005 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-013: STORY-005 explicitly owns this point through its scope, requirements, and acceptance criteria.
  - DESIGN-REQ-014: STORY-005 explicitly owns this point through its scope, requirements, and acceptance criteria.

## Coverage Matrix

- DESIGN-REQ-001: STORY-001, STORY-002, STORY-004, STORY-005
- DESIGN-REQ-002: STORY-001, STORY-004, STORY-005
- DESIGN-REQ-003: STORY-001, STORY-003
- DESIGN-REQ-004: STORY-001, STORY-003, STORY-005
- DESIGN-REQ-005: STORY-001, STORY-002, STORY-004, STORY-005
- DESIGN-REQ-006: STORY-002, STORY-004
- DESIGN-REQ-007: STORY-001, STORY-002, STORY-004, STORY-005
- DESIGN-REQ-008: STORY-001, STORY-005
- DESIGN-REQ-009: STORY-002, STORY-005
- DESIGN-REQ-010: STORY-003, STORY-005
- DESIGN-REQ-011: STORY-004
- DESIGN-REQ-012: STORY-003, STORY-005
- DESIGN-REQ-013: STORY-002, STORY-004, STORY-005
- DESIGN-REQ-014: STORY-001, STORY-002, STORY-003, STORY-004, STORY-005

## Dependencies

- STORY-001: none
- STORY-002: STORY-001
- STORY-003: none
- STORY-004: STORY-001
- STORY-005: STORY-001, STORY-002, STORY-003

## Out of Scope

- Production HA guidance, deeper readiness checks, developer guardrails, RAG integration, Mission Control recovery UX beyond evidence links, and Responses API parity: These are mentioned in the design but are not explicit P0/P1 recommended Jira issues for this breakdown.

## Coverage Gate

PASS - every major design point is owned by at least one story.
