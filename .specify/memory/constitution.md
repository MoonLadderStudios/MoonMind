# MoonMind Constitution

## Core Principles

### I. One-Click Deployment with Smart Defaults

MoonMind MUST provide a “fresh clone → running system” path that is simple, documented, and reliable.

Non-negotiable rules:

- The repo MUST define a canonical “one-click” operator path (for MoonMind, Docker Compose is the default target).
- A default deployment MUST start successfully using only:
  - documented prerequisites (e.g., Docker), and
  - a minimal, clearly documented set of required secrets (if any).
- All non-secret configuration MUST have smart defaults (safe, functional, and predictable).
- Optional integrations MUST be either:
  - disabled by default with safe no-op behavior, or
  - enabled by default only if they do not require secrets and do not increase operational risk.
- Any missing prerequisite MUST fail fast with an actionable error message (what is missing + how to fix it).

Rationale: MoonMind is an operator tool. Setup friction is a feature-killer.

### II. Powerful Runtime Configurability

MoonMind MUST be configurable at runtime without requiring code edits or image rebuilds for routine changes.

Non-negotiable rules:

- Operator-facing behavior MUST be controlled by configuration (env/config) rather than hardcoded constants.
- Configuration MUST have deterministic precedence (highest to lowest):
  1) explicit request payload / API parameter (when applicable),
  2) environment variables,
  3) config file,
  4) defaults.
- Each config option MUST be:
  - documented (purpose + default + examples),
  - namespaced consistently (e.g., `MOONMIND_*`), and
  - safe-by-default (no surprising network calls, no permissive security defaults).
- Runtime mode switches (e.g., worker runtime selection, adapter selection) MUST be observable in logs and/or run metadata.

Rationale: MoonMind runs in many environments; routine tuning must be easy and reversible.

### III. Modular and Extensible Architecture

MoonMind MUST remain easy to extend without rewriting the core.

Non-negotiable rules:

- New capabilities MUST be introduced behind clear module boundaries with explicit contracts.
- Core orchestration logic MUST depend on stable interfaces (contracts), not on vendor/CLI specifics.
- Adding a new integration SHOULD require:
  - a new adapter/module, and
  - minimal changes to the existing orchestration core.
- Cross-cutting changes (touching many modules) MUST be justified in the plan “Complexity Tracking” section.

Rationale: Extensibility is the product. Architecture must resist entanglement.

### IV. Avoid Exclusive Proprietary Vendor Lock-In

MoonMind MUST avoid designs that force one exclusive proprietary provider to use core functionality.

Non-negotiable rules:

- Vendor-specific behavior MUST live behind adapter interfaces so alternatives can be added without refactoring core flows.
- Data formats for artifacts, run state, and logs MUST be stored in portable, inspectable formats (e.g., JSON/YAML/text diffs).
- When introducing a vendor-specific feature, the plan MUST document:
  - what would change to support an alternative provider, and
  - what is intentionally vendor-specific (and why).

Rationale: MoonMind should remain deployable and evolvable across ecosystems.

### V. Self-Healing by Default

MoonMind MUST recover safely from common failures without manual babysitting.

Non-negotiable rules:

- All externally visible side effects MUST be designed to be retry-safe (idempotent or de-duplicated).
- Long-running workflows MUST persist enough state to resume, retry, or fail deterministically after worker restarts.
- Failure handling MUST be explicit:
  - retries and backoff where appropriate,
  - deterministic “needs human” terminal states when not recoverable,
  - error summaries that tell an operator what happened and what to do next.
- Health checks MUST exist for runtime-critical services and worker processes (startup checks + dependency checks).

Rationale: Operators will restart containers. The system must withstand it.

### VI. Facilitate Continuous Improvement

MoonMind MUST make it easy to improve itself and the projects it operates on.

Non-negotiable rules:

- Every run MUST end with a structured outcome summary:
  - success / no-op / failed,
  - primary reason (if failed),
  - key artifacts/links,
  - recommended next action.
- The system SHOULD capture improvement signals (for example: repeated retries, loops, ambiguous prompts, missing files, flaky tests)
  and route them into a reviewable backlog (e.g., proposals / improvements queue).
- Continuous improvement suggestions MUST be opt-in for application (reviewable; no silent auto-commit to important repos).

Rationale: MoonMind is an automation engine; it must learn from real execution.

### VII. Spec-Driven Development Is the Source of Truth

MoonMind development MUST be spec-driven, with clear contracts and traceability.

Non-negotiable rules:

- Any non-trivial change MUST begin with specification artifacts under `specs/<id>-<feature>/`:
  - `spec.md` (requirements & acceptance scenarios),
  - `plan.md` (implementation strategy),
  - `tasks.md` (incremental execution plan).
- `spec.md` MUST remain technology-agnostic; implementation details belong in `plan.md`.
- Every `plan.md` MUST include a “Constitution Check” with PASS/FAIL coverage for each principle.
  - If any principle is violated, the plan MUST document the violation and mitigation in “Complexity Tracking”.
- Implementation MUST not silently drift from the spec:
  - if reality changes, update the spec/plan/tasks to match.

Rationale: Specs are how MoonMind stays maintainable while evolving quickly.

### VIII. Skills Are First-Class and Easy to Add

MoonMind MUST make skills straightforward to create, register, test, and use across runtimes.

Non-negotiable rules:

- Skills MUST be discoverable and composable (usable as steps in larger workflows).
- Adding a skill SHOULD be “low ceremony”:
  - minimal boilerplate,
  - clear registration location,
  - clear contract for inputs/outputs and side effects.
- Skills MUST declare:
  - required inputs,
  - produced outputs/artifacts,
  - external dependencies,
  - failure modes and expected operator actions.
- Skill execution SHOULD be runtime-neutral at the workflow level (with runtime adapters implementing the specifics).

Rationale: Skills are the unit of scale for MoonMind automation.

## Non-Negotiable Product & Operational Constraints

- **Security / secret hygiene**:
  - Secrets MUST NOT be written into artifacts, logs, or PR text.
  - Secret inputs MUST be passed via approved secret channels (env/secret stores) and redacted in output.
- **Observability**:
  - Workflows MUST emit enough structured metadata to diagnose issues (run IDs, stage names, outcomes, durations).
  - Operators MUST be able to answer: “what happened?” without reading raw worker internals.
- **Compatibility & migration**:
  - Breaking changes to public APIs/contracts MUST include a migration plan and a deprecation window where feasible.
  - Compatibility aliases are acceptable when they reduce operator pain, but MUST be observable and tracked.

## Development Workflow & Quality Gates

- **Constitution is a gate**:
  - Every `/speckit.plan` output MUST include the Constitution Check gate, and it MUST be re-checked after Phase 1 design.
- **Validation is required**:
  - Each feature MUST define at least one independent validation path (automated tests or a deterministic quickstart/manual validation).
- **Clarity over cleverness**:
  - Prefer explicit contracts, explicit adapters, and explicit errors over implicit fallback behavior.
- **Exceptions must be visible**:
  - If a plan violates a MUST principle, it MUST be documented as a violation with a mitigation and a path back to compliance.

## Governance

- This constitution is the highest authority for MoonMind engineering practices and overrides lower-level conventions.
- Amendments MUST be made via a documented PR that includes:
  - the reason for the change,
  - any expected impacts on templates/automation,
  - a version bump following semantic intent:
    - MAJOR: backward-incompatible governance/principle redefinition,
    - MINOR: new principle/section or material expansion,
    - PATCH: clarifications/wording only.
- Plans MUST treat MUST statements as non-negotiable gates.
  - If a MUST is violated, the plan MUST record it in “Complexity Tracking” and explain why the simpler alternative was rejected.
