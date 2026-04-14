---
name: moonspec-align
description: Analyze and automatically remediate Moon Spec artifact inconsistencies across spec.md, plan.md, tasks.md, and related design files. Use when the user asks to run an automated `/speckit.analyze`-style workflow that identifies uncertainty, weighs tradeoffs in project context, edits artifacts without asking follow-up questions, resolves coverage gaps, or aligns generated Moon Spec documents before implementation.
---

# MoonSpec Align

Use this skill to perform an automated analyze-to-remediate workflow for an active Moon Spec feature.

## Scope

This skill edits Moon Spec artifacts, not application feature code. Typical targets are:

- `spec.md`
- `plan.md`
- `tasks.md`
- `research.md`
- `data-model.md`
- `quickstart.md`
- files under `contracts/`
- feature checklists when present

Do not ask the user for clarification. Resolve uncertainty by reading project context, making conservative decisions, documenting assumptions, and applying the least surprising artifact changes.

## Inputs

- Treat the user's text as optional guidance.
- Work from the active feature directory resolved by the prerequisite script.
- Require `spec.md`, `plan.md`, and `tasks.md`; stop with the prerequisite error if any are missing.
- Use absolute paths in reports.
- Preserve user-provided source requirements and original request text exactly when they are already recorded in artifacts.

## Setup

Run the prerequisite script from the repository root:

```bash
scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
```

On PowerShell projects, use:

```powershell
scripts/powershell/check-prerequisites.ps1 -Json -RequireTasks -IncludeTasks
```

Parse `FEATURE_DIR` and `AVAILABLE_DOCS`, then derive:

- `SPEC = FEATURE_DIR/spec.md`
- `PLAN = FEATURE_DIR/plan.md`
- `TASKS = FEATURE_DIR/tasks.md`
- optional docs from `AVAILABLE_DOCS`
- `CONSTITUTION = .specify/memory/constitution.md`

If shell arguments contain single quotes, use shell-safe escaping such as `'I'\''m Groot'`, or double quotes when possible.

## Extension Hooks

Before analysis, check `.specify/extensions.yml` for `hooks.before_analyze`. After remediation and reporting, check it for `hooks.after_analyze`.

If the YAML cannot be parsed or is invalid, skip hook checking silently. Ignore hooks where `enabled` is explicitly `false`; hooks without `enabled` are enabled. Do not evaluate non-empty `condition` expressions. Treat hooks with no condition, null condition, or empty condition as executable; skip hooks with non-empty conditions.

For executable hooks:

- Optional hooks: report the hook command and prompt, but do not pause for user input.
- Mandatory hooks: output `EXECUTE_COMMAND: {command}` and execute or delegate it according to the active hook execution environment before continuing.

## Context Loading

Load enough context to make edits confidently, but do not paste whole artifacts into the final response.

Read:

- `spec.md`: input/source request, user story, acceptance scenarios, edge cases, functional requirements, success criteria, key entities, assumptions, source design coverage.
- `plan.md`: technical context, constitution checks, project structure, phase notes, complexity tracking.
- `tasks.md`: phases, task IDs, dependencies, parallel markers, file paths, tests, implementation tasks.
- `.specify/memory/constitution.md`: principles and normative `MUST`/`SHOULD` statements.
- optional docs listed by the prerequisite script when findings refer to them.

Also inspect relevant repository files when needed to resolve uncertainty, especially test framework configuration, project layout, existing contracts, and naming conventions.

## Analysis Model

Build an internal model before editing:

- Requirements inventory keyed by explicit `FR-###`, `SC-###`, `DESIGN-REQ-###`, and stable slugs for unnumbered items.
- User-story inventory with acceptance scenarios and edge cases.
- Task coverage mapping from task IDs to requirements, scenarios, success criteria, and design artifacts.
- Artifact authority order:
  1. Constitution and explicit source design requirements.
  2. Original feature input preserved in `spec.md`.
  3. `spec.md` user-visible behavior and acceptance criteria.
  4. `plan.md` architecture and technical decisions.
  5. Design artifacts such as contracts, data model, research, and quickstart.
  6. `tasks.md` execution breakdown.
- Project context: language, frameworks, test tools, existing file structure, and conventions.

## Detection Passes

Identify high-signal findings. Aggregate related issues instead of creating noisy one-off findings.

- Duplication: overlapping requirements, repeated tasks, duplicate terminology definitions.
- Ambiguity: vague terms such as fast, scalable, secure, intuitive, robust without measurable criteria; unresolved placeholders; unclear actors or data ownership.
- Underspecification: requirements missing objects or outcomes, success criteria without validation path, edge cases with no acceptance coverage, tasks referencing undefined components.
- Constitution alignment: conflicts with `MUST` statements, missing mandated quality gates, unjustified complexity.
- Coverage gaps: requirements or buildable success criteria with no tasks, tests absent for required behavior, contracts not represented in quickstart or tasks.
- Inconsistency: terminology drift, entity drift, contradictory stack choices, test strategy mismatch, task ordering conflicts, implementation tasks before required tests.

## Uncertainty And Tradeoff Handling

For each material uncertainty, choose a resolution without asking the user.

Use this decision process:

1. Name the uncertainty precisely.
2. Gather available evidence from the artifacts and repository.
3. List 2-3 plausible resolutions.
4. Weigh pros and cons using project context:
   - conformance to constitution and source requirements
   - preservation of user-visible behavior
   - implementation blast radius
   - testability
   - consistency with existing architecture
   - reversibility
5. Choose the option that preserves higher-authority artifacts and requires the smallest coherent change.
6. Record the chosen assumption in the edited artifact when future implementers need to know it.

Default choices:

- If `tasks.md` conflicts with `spec.md`, update `tasks.md` unless the spec is clearly incomplete relative to the original input.
- If `plan.md` conflicts with the constitution or explicit source design requirement, update `plan.md`.
- If `spec.md` is ambiguous but the repo has a clear established convention, update `spec.md` with that convention as an assumption or measurable requirement.
- If two options are equally plausible and neither is required by higher authority, choose the simpler and more reversible option.
- If a finding cannot be remediated without inventing product scope, add a bounded assumption and a validation task instead of asking the user.

## Remediation Rules

Apply edits after analysis. Do not stop at a report.

Prefer these remediation patterns:

- Merge duplicate requirements and update downstream references.
- Replace vague adjectives with measurable, testable criteria when context supports a defensible threshold.
- Add missing acceptance criteria for already-implied behavior.
- Add tasks for uncovered requirements, edge cases, contracts, and buildable success criteria.
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
- Do not edit application source code unless the user explicitly asks for implementation beyond Moon Spec artifacts.

## Editing Order

Use a stable top-down order so reruns are predictable:

1. Constitution conflicts.
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
   - every functional requirement has task coverage
   - every buildable success criterion has task or quickstart coverage
   - test tasks cover required unit and integration behavior where applicable
   - no constitution `MUST` conflict remains
   - no unresolved placeholders remain unless explicitly intentional and explained
   - task ordering is coherent
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
