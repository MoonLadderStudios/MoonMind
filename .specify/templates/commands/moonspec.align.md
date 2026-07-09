---
description: Analyze and automatically remediate MoonSpec artifact drift across spec.md, plan.md, tasks.md, and related design files without asking for user input.
scripts:
  sh: .specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
---

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Pre-Execution Checks

**Check for extension hooks (before alignment)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under `hooks.before_align`.
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally.
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable.
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation.
- For executable hooks:
  - Optional hooks: report the hook command and prompt, but do not pause for user input.
  - Mandatory hooks: output `EXECUTE_COMMAND: {command}` and execute or delegate it according to the active hook execution environment before continuing.
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently.

## Goal

Identify inconsistencies, duplications, ambiguities, underspecified items, coverage gaps, and principle conflicts across MoonSpec artifacts, then automatically implement conservative artifact improvements without asking the user for clarification.

This command MUST run only after `/moonspec.tasks` has successfully produced a complete `tasks.md`.

## Operating Constraints

**Artifact-editing command**: This command may modify MoonSpec artifacts, including `spec.md`, `plan.md`, `tasks.md`, `research.md`, `data-model.md`, `quickstart.md`, files under `contracts/`, and feature checklists when present.

**No user input loop**: Do **not** ask the user for clarification or permission before applying remediation. Resolve uncertainty by reading project context, weighing tradeoffs, choosing conservative defaults, documenting assumptions, and applying the least surprising coherent edit.

**No application implementation**: Do **not** edit application source code, production tests, migrations, or runtime configuration unless the user explicitly asks for implementation beyond artifact alignment.

**Principles authority**: `AGENTS.md`, when present, provides project principles, repo constraints, and testing discipline for this command. Principle conflicts are automatically high priority and require adjustment of the spec, plan, design artifacts, or tasks—not dilution, reinterpretation, or silent ignoring of the principle.

## Execution Steps

### 1. Initialize Alignment Context

Run `{SCRIPT}` once from repo root and parse JSON for `FEATURE_DIR` and `AVAILABLE_DOCS`. Derive absolute paths:

- SPEC = FEATURE_DIR/spec.md
- PLAN = FEATURE_DIR/plan.md
- TASKS = FEATURE_DIR/tasks.md
- REPO_GUIDANCE = AGENTS.md when present

Abort with an error message if any required file is missing. Instruct the user to run the missing prerequisite command.

For single quotes in args like "I'm Groot", use escape syntax: e.g. `'I'\''m Groot'` (or double-quote if possible: "I'm Groot").

### 2. Load Artifacts

Load enough context to make edits confidently:

**From spec.md:**

- Original request or source input
- User story and independent test
- Acceptance scenarios
- Edge cases
- Functional Requirements
- Success Criteria
- Key Entities and Assumptions
- Source design coverage such as `DESIGN-REQ-*` or `DOC-REQ-*`

**From plan.md:**

- Architecture and stack choices
- Testing strategy and tools
- Data model and contract references
- Phases and technical constraints
- Principles Check and complexity tracking

**From tasks.md:**

- Task IDs and descriptions
- Phase grouping
- Parallel markers `[P]`
- Referenced file paths
- Unit and integration test tasks
- Dependency and ordering notes

**From AGENTS.md, when present:**

- Project principles
- Repo constraints
- Testing discipline
- Source-of-truth expectations

**From optional docs:**

- `research.md`
- `data-model.md`
- `contracts/`
- `quickstart.md`
- `checklists/` when present

Also inspect relevant repository files when needed to resolve uncertainty, especially project layout, test framework configuration, existing contracts, and naming conventions.

### 3. Build Semantic Models

Create internal representations:

- **Requirements inventory**: For each `FR-*`, buildable `SC-*`, acceptance scenario, edge case, and in-scope source requirement, record a stable key.
- **Task coverage mapping**: Map each task to one or more requirements, scenarios, success criteria, source requirements, or design artifacts.
- **Artifact authority order**:
  1. Explicit source design requirements and relevant `AGENTS.md` principles
  2. Original feature input preserved in `spec.md`
  3. `spec.md` user-visible behavior and acceptance criteria
  4. `plan.md` architecture and technical decisions
  5. Design artifacts such as contracts, data model, research, and quickstart
  6. `tasks.md` execution breakdown
- **Project context**: Language, frameworks, test tools, file structure, conventions, and safe validation commands.

### 4. Detection Passes

Identify high-signal findings. Aggregate related issues instead of creating noisy one-off findings.

- Duplication: near-duplicate requirements, scenarios, entities, design decisions, or tasks.
- Ambiguity: vague adjectives, unresolved placeholders, unclear actors, data ownership, failure behavior, or validation expectations.
- Underspecification: requirements missing objects or measurable outcomes, acceptance scenarios without observable results, edge cases without coverage, or tasks referencing undefined components.
- Principle alignment: conflicts with `AGENTS.md` `MUST` statements, missing mandated quality gates, missing unit/integration tests, or unjustified complexity.
- Coverage gaps: requirements or source requirements with no associated tasks, buildable success criteria not reflected in tasks or quickstart, or contracts not represented in tests.
- Inconsistency: terminology drift, entity drift, task ordering contradictions, test-first ordering contradictions, or conflicting technology/interface/persistence decisions.

### 5. Resolve Uncertainties

For each material uncertainty, choose a resolution without asking the user.

Use this decision process:

1. Name the uncertainty precisely.
2. Gather evidence from artifacts and repository context.
3. Identify 2-3 plausible resolutions.
4. Weigh pros and cons using:
   - conformance to source requirements and relevant principles
   - preservation of user-visible behavior
   - implementation blast radius
   - testability
   - consistency with existing architecture
   - reversibility
5. Choose the option that preserves higher-authority artifacts and requires the smallest coherent change.
6. Record the chosen assumption in the edited artifact when future implementers need to know it.

Default decisions:

- If `tasks.md` conflicts with `spec.md`, update `tasks.md` unless the spec is clearly incomplete relative to the original input.
- If `plan.md` conflicts with an explicit source design requirement or relevant `AGENTS.md` principle, update `plan.md`.
- If `spec.md` is ambiguous but the repo has a clear established convention, update `spec.md` with that convention as an assumption or measurable requirement.
- If two options are equally plausible and neither is required by higher authority, choose the simpler and more reversible option.
- If a finding cannot be remediated without inventing product scope, add a bounded assumption and a validation task instead of asking the user.

### 6. Apply Remediation

Edit artifacts in this order:

1. Principle conflicts.
2. Source requirement and original-input preservation issues.
3. Spec requirement, scenario, edge-case, entity, and success-criteria issues.
4. Plan and design artifact issues.
5. Task coverage, ordering, and test gaps.
6. Quickstart and checklist consistency.

Preferred remediation patterns:

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

### 7. Validate Alignment

After editing:

1. Rerun `{SCRIPT}`.
2. Rebuild the analysis model.
3. Verify:
   - every functional requirement has task coverage
   - every in-scope source requirement maps to a functional requirement and task
   - every buildable success criterion has task or quickstart coverage
   - test tasks cover required unit and integration behavior where applicable
   - no relevant `AGENTS.md` `MUST` conflict remains
   - no unresolved placeholders remain unless explicitly intentional and explained
   - task ordering is coherent
4. Run lightweight repository checks when the edits touched generated checks, test commands, or machine-readable contracts. If no relevant command exists, report that no additional command was available.

### 8. Produce Alignment Report

Output a concise Markdown report:

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

Include exact artifact paths. Mention remediation intentionally skipped because it would require application code or new product scope.

### 9. Post-Execution Checks

**Check for extension hooks (after alignment)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under `hooks.after_align`.
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally.
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable.
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation.
- For executable hooks:
  - Optional hooks: report the hook command and prompt.
  - Mandatory hooks: output `EXECUTE_COMMAND: {command}` and execute or delegate it.
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently.

## Operating Principles

- **Automated remediation**: Apply artifact edits directly after analysis.
- **No clarification loop**: Resolve uncertainty with project context and conservative assumptions.
- **Authority preserving**: Never weaken source design, original request intent, or relevant `AGENTS.md` principles to simplify downstream artifacts.
- **Traceability**: Maintain clear mappings from source requirements to spec requirements, tasks, tests, and validation.
- **Context efficiency**: Focus on high-signal findings and concise edit summaries.
- **Deterministic results**: Rerunning without changes should produce stable decisions and minimal diffs.
