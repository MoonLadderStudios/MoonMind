# Research: Task Steps System

## Decision 1: Model steps in canonical task contract with strict field allowlist
- Decision: Add `TaskStepSpec` to `task_contract.py` with only `id`, `title`, `instructions`, and `skill`; explicitly reject step-level runtime/model/effort/git/publish/container/repository overrides.
- Rationale: Keeps task controls task-scoped and prevents hidden behavior divergence across steps.
- Alternatives considered:
  - Arbitrary step dictionaries: rejected due to silent override risk and weak validation.
  - Separate step runtime schema: rejected for first rollout complexity and policy ambiguity.

## Decision 2: Preserve implicit single-step compatibility when `task.steps` missing/empty
- Decision: Worker resolves runtime steps to explicit list and falls back to one synthesized step using task objective and task-level/default skill when step list is absent/empty.
- Rationale: Maintains existing behavior for all current producers while enabling incremental adoption.
- Alternatives considered:
  - Require non-empty `task.steps` for all canonical tasks: rejected as backward-incompatible.

## Decision 3: Derive capabilities from task-level + step-level skill requirements
- Decision: Extend required capability derivation with union of `task.skill.requiredCapabilities` and each `task.steps[*].skill.requiredCapabilities`, plus existing runtime/git/publish/docker derivation.
- Rationale: Keeps worker claim policy accurate when a step introduces additional tool requirements.
- Alternatives considered:
  - Task-level only capability derivation: rejected because step-specific skill requirements would be ignored.

## Decision 4: Execute steps sequentially inside execute stage with first-failure short-circuit
- Decision: Add execute-stage loop that emits step plan/start/finish/fail events and invokes runtime exactly once per step; stop on first failure and skip publish.
- Rationale: Matches document contract while preserving one job and one publish decision.
- Alternatives considered:
  - One monolithic composed prompt: rejected due to poor per-step observability and failure isolation.
  - Parallel step execution: rejected because step ordering and shared workspace semantics would break.

## Decision 5: Preserve cancellation guarantees via boundary checks + in-flight cancellation event
- Decision: Keep active cancel event wiring and check cancellation before each step and after each invocation; cancellation acknowledges job and prevents terminal success/failure updates.
- Rationale: Reuses proven cooperative cancellation model with minimal behavior regression risk.
- Alternatives considered:
  - Poll-only between steps without handler cancel event: rejected because long-running steps would remain unresponsive.

## Decision 6: Materialize union of referenced non-auto skills in prepare stage
- Decision: Resolve all step/task skill ids, materialize them together once, then execute steps with effective-skill precedence (`step` -> `task` -> `auto`).
- Rationale: Avoids repeated materialization cost and ensures runtime has complete skill set before execute.
- Alternatives considered:
  - Materialize per step lazily: rejected due to repeated setup overhead and higher failure surface.

## Decision 7: First rollout explicitly rejects container+steps
- Decision: Raise deterministic contract error when `task.container.enabled=true` and `task.steps` is non-empty.
- Rationale: Avoids undefined behavior while container-step sequencing design remains out of scope.
- Alternatives considered:
  - Best-effort sequential container execution: rejected due to ambiguous command composition and publish semantics.

## Decision 8: Add queue submit UI steps editor with canonical payload emission
- Decision: Extend `/tasks/queue/new` form to add/remove/reorder optional steps and include `task.steps` in submitted canonical payload while keeping the publish default driven by `MOONMIND_DEFAULT_PUBLISH_MODE` (default `pr`).
- Rationale: Enables routine usage without raw JSON edits and aligns with document target UX.
- Alternatives considered:
  - Keep UI unchanged and require API/manual payload crafting: rejected for adoption friction.
