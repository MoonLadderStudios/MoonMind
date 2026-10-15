---
name: moonspec-align
description: Analyze and automatically remediate MoonSpec artifact inconsistencies across spec.md, plan.md, tasks.md, and related design files. Use when the user asks to run `/moonspec.align`, identify uncertainty, weigh tradeoffs in project context, edit artifacts without asking follow-up questions, resolve coverage gaps, or align generated MoonSpec documents before implementation.
metadata:
  required-capabilities:
    - git
---

# MoonSpec Align

Use this skill to perform an automated analyze-to-remediate workflow for an active MoonSpec feature.

## Scope

This skill edits MoonSpec execution artifacts, not application feature code. Typical targets are:

- `spec.md`
- `plan.md`
- `tasks.md`
- `research.md`
- `data-model.md`
- `quickstart.md`
- files under `contracts/`
- feature checklists when present

Alignment edits only temporary execution artifacts, never canonical `docs/` files. Preserve explicit task-authorized behavior changes even when existing documentation describes the old behavior; their required in-place documentation corrections belong to implementation, not alignment edits. Otherwise use the adopted source requirements to guide the derived artifacts.

Resolve ordinary artifact uncertainty from project context. Preserve actual `needs_human` authority decisions for their owner; do not invent approval or convert them into implementation work.

## Inputs

- Treat the user's text as optional guidance.
- Work from the active feature directory resolved by the prerequisite script.
- Require `spec.md`, `plan.md`, and `tasks.md`; stop with the prerequisite error if any are missing.
- Read `AGENTS.md` when present for project principles, repo constraints, and test discipline.
- Use absolute paths in reports.
- Preserve user-provided source requirements and original request text exactly when they are already recorded in artifacts.

## Setup

Run the prerequisite script from the repository root:

```bash
.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
```

Parse `FEATURE_DIR` and `AVAILABLE_DOCS`, then derive:

- `SPEC = FEATURE_DIR/spec.md`
- `PLAN = FEATURE_DIR/plan.md`
- `TASKS = FEATURE_DIR/tasks.md`
- optional docs from `AVAILABLE_DOCS`
- `REPO_GUIDANCE = AGENTS.md` when present

If shell arguments contain single quotes, use shell-safe escaping such as `'I'\''m Groot'`, or double quotes when possible.

## Extension Hooks

Before analysis, check `.specify/extensions.yml` for `hooks.before_align`. After remediation and reporting, check it for `hooks.after_align`.

If the YAML cannot be parsed or is invalid, skip hook checking silently. Ignore hooks where `enabled` is explicitly `false`; hooks without `enabled` are enabled. Do not evaluate non-empty `condition` expressions. Treat hooks with no condition, null condition, or empty condition as executable; skip hooks with non-empty conditions.

For executable hooks:

- Optional hooks: report the hook command and prompt, but do not pause for user input.
- Mandatory hooks: output `EXECUTE_COMMAND: {command}` and execute or delegate it according to the active hook execution environment before continuing.

## Context Loading

Load enough context to make edits confidently, but do not paste whole artifacts into the final response.

Read:

- `spec.md`: input/source request, user story, acceptance scenarios, edge cases, functional requirements, success criteria, key entities, assumptions, source design coverage.
- `plan.md`: technical context, Principles Check, project structure, phase notes, complexity tracking, and requirement status.
- `tasks.md`: phases, task IDs, dependencies, parallel markers, file paths, tests, implementation tasks.
- `AGENTS.md` when present: project principles, repo constraints, and test discipline.
- optional docs listed by the prerequisite script when findings refer to them.
- Markdown checklists under `FEATURE_DIR/checklists/` when present; the prerequisite script does not list them in `AVAILABLE_DOCS`.

Also inspect relevant repository files when needed to resolve uncertainty, especially test framework configuration, project layout, existing contracts, and naming conventions.

## Analysis Model

Build an internal model before editing:

- Requirements inventory keyed by explicit `FR-###`, `SC-###`, `DESIGN-REQ-###`, and stable slugs for unnumbered items.
- User-story inventory with acceptance scenarios and edge cases.
- Apply requirement statuses before all coverage and execution gates: `unverified` rows retain source IDs, reason and verifiableBy without executable tasks; `needs_human` rows retain the exact decision, owner, and action without executing it or claiming its completion. Only executable rows require code, tests, setup, and red-first evidence. Observed defects remain executable remediation; unavailable checks do not become defects. For all-Unverified artifacts, preserve disclosures and final verification only.
- Artifact authority order:
  1. Explicit task-authorized behavior changes.
  2. Applicable `AGENTS.md` requirements.
  3. Source design requirements adopted by the task.
  4. Original feature input preserved in `spec.md`.
  5. `spec.md` user-visible behavior and acceptance criteria.
  6. `plan.md` architecture and technical decisions.
  7. Design artifacts such as contracts, data model, research, and quickstart.
  8. `tasks.md` execution breakdown.
- Project context: language, frameworks, test tools, existing file structure, and conventions.

## Detection Passes

Identify high-signal findings. Aggregate related issues instead of creating noisy one-off findings.

- Duplication: overlapping requirements, repeated tasks, duplicate terminology definitions.
- Ambiguity: vague terms such as fast, scalable, secure, intuitive, robust without measurable criteria; unresolved placeholders; unclear actors or data ownership.
- Underspecification: requirements missing objects or outcomes, success criteria without validation path, edge cases with no acceptance coverage, tasks referencing undefined components.
- Principle alignment: conflicts with `AGENTS.md` `MUST` statements, missing mandated quality gates, unjustified complexity.
- Coverage gaps: executable rows lack required tasks or feasible tests. Do not add tasks for `unverified` or `needs_human` rows.
- Inconsistency: terminology drift, entity drift, contradictory stack choices, test strategy mismatch, task ordering conflicts, implementation tasks before required tests.

## Uncertainty And Tradeoff Handling

For ordinary artifact uncertainty, choose a resolution from context; preserve an actual missing authority decision as `needs_human`.

Use this decision process:

1. Name the uncertainty precisely.
2. Gather available evidence from the artifacts and repository.
3. List 2-3 plausible resolutions.
4. Weigh pros and cons using project context:
   - conformance to source requirements and relevant principles
   - preservation of user-visible behavior
   - implementation blast radius
   - testability
   - consistency with existing architecture
   - reversibility
5. Choose the option that preserves higher-authority artifacts and requires the smallest coherent change.
6. Record the chosen assumption in the edited artifact when future implementers need to know it.

Default choices:

- Resolve task, spec, plan, and source conflicts using the artifact authority order above. Bring stale upstream artifacts into line with an explicitly task-authorized change before aligning downstream artifacts; otherwise use the adopted requirements.
- If `spec.md` is ambiguous but the repo has a clear established convention, update `spec.md` with that convention as an assumption or measurable requirement.
- If two options are equally plausible and neither is required by higher authority, choose the simpler and more reversible option.
- Do not invent product scope or authority; preserve any actual unresolved owner decision as `needs_human`.

## Remediation Rules

Apply edits after analysis. Do not stop at a report.

Prefer these remediation patterns:

- Merge duplicate requirements and update downstream references.
- Replace vague adjectives with measurable, testable criteria when context supports a defensible threshold.
- Add missing acceptance criteria for already-implied behavior.
- Add tasks only for uncovered executable rows, including real code, test, and required documentation gaps. Preserve non-executable disclosures and authority handoffs.
- Add or reorder test tasks so tests precede implementation when the workflow requires test-first development.
- Align terminology across artifacts with the term used by the highest-authority artifact.
- Update `quickstart.md` so it validates the same story, contracts, and test commands represented by `tasks.md`.
- Update `research.md` when a technical decision or tradeoff is resolved during alignment.
- Update `plan.md` when architecture, test strategy, or constraints need to match the spec and repository.

Constraints:

- Do not remove source design mappings or original user input.
- Do not silently weaken requirements to make tasks easier.
- Do not make broad rewrites unrelated to identified findings.
- Do not create multiple user stories inside a single `spec.md`.
- Do not invent dependencies or architectural choices that conflict with the repo.
- Do not edit application source code unless the user explicitly asks for implementation beyond MoonSpec artifacts.

## Editing Order

Use a stable top-down order so reruns are predictable:

1. Principle conflicts.
2. Source requirement and original-input preservation issues.
3. Spec requirement, scenario, edge-case, entity, and success-criteria issues.
4. Plan and design artifact issues.
5. Task coverage, ordering, and test gaps.
6. Quickstart and checklist consistency.

After each artifact class is edited, re-check downstream mappings before moving on.

## Validation

After editing:

1. Rerun the prerequisite script.
2. Rebuild the analysis model.
3. Verify:
   - every adopted requirement remains traceable to executable work, an Unverified disclosure, or a `needs_human` handoff
   - executable rows have applicable task, test, or quickstart coverage
   - no unresolved applicable `AGENTS.md` conflict remains after honoring explicit task overrides
   - no unresolved placeholders remain unless explicitly intentional and explained
   - feature checklists remain consistent with the updated artifacts; do not mark unverified work complete
   - task ordering is coherent and executable coverage is complete; do not repair a disclosure-only row by inventing tasks
4. Run lightweight repository tests or formatting commands when the edits touched generated checks, test commands, or machine-readable contracts. If no relevant command exists, say so.

## Report

Final response structure:

```markdown
## MoonSpec Alignment

Updated:
- [artifact path]: [short summary]

Key decisions:
- [uncertainty]: chose [decision] because [project-context rationale]

Remaining risks:
- [risk or "None found"]

Validation:
- [command]: [result]
```

Keep the report concise. Include exact artifact paths and mention any remediation you intentionally skipped because it would require application code or new product scope.
