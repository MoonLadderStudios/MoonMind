---
description: Analyze and automatically remediate Moon Spec artifact drift across spec.md, plan.md, tasks.md, and related design files without asking for user input.
scripts:
  sh: scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
  ps: scripts/powershell/check-prerequisites.ps1 -Json -RequireTasks -IncludeTasks
---

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Pre-Execution Checks

**Check for extension hooks (before alignment)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under `hooks.before_align`. If none exist, also check `hooks.before_analyze` for compatibility with analyze-style extensions.
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally.
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable.
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation.
- For each executable hook, output the following based on its `optional` flag:
  - **Optional hook** (`optional: true`):
    ```
    ## Extension Hooks

    **Optional Pre-Hook**: {extension}
    Command: `/{command}`
    Description: {description}

    Prompt: {prompt}
    To execute: `/{command}`
    ```
  - **Mandatory hook** (`optional: false`):
    ```
    ## Extension Hooks

    **Automatic Pre-Hook**: {extension}
    Executing: `/{command}`
    EXECUTE_COMMAND: {command}

    Wait for the result of the hook command before proceeding to the Goal.
    ```
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently.

## Goal

Identify inconsistencies, duplications, ambiguities, underspecified items, coverage gaps, and constitution conflicts across Moon Spec artifacts, then automatically implement conservative artifact improvements without asking the user for clarification.

This command MUST run only after `/speckit.tasks` has successfully produced a complete `tasks.md`.

## Operating Constraints

**Artifact-editing command**: This command may modify Moon Spec artifacts, including `spec.md`, `plan.md`, `tasks.md`, `research.md`, `data-model.md`, `quickstart.md`, files under `contracts/`, and feature checklists when present.

**No user input loop**: Do **not** ask the user for clarification or permission before applying remediation. Resolve uncertainty by reading project context, weighing tradeoffs, choosing conservative defaults, documenting assumptions, and applying the least surprising coherent edit.

**No application implementation**: Do **not** edit application source code, production tests, migrations, or runtime configuration unless the user explicitly asks for implementation beyond artifact alignment.

**Constitution Authority**: The project constitution (`/memory/constitution.md`) is **non-negotiable** within this command. Constitution conflicts are automatically CRITICAL and require adjustment of the spec, plan, design artifacts, or tasks—not dilution, reinterpretation, or silent ignoring of the principle. If a principle itself needs to change, that must occur in a separate, explicit constitution update outside `/speckit.align`.

## Execution Steps

### 1. Initialize Alignment Context

Run `{SCRIPT}` once from repo root and parse JSON for `FEATURE_DIR` and `AVAILABLE_DOCS`. Derive absolute paths:

- SPEC = FEATURE_DIR/spec.md
- PLAN = FEATURE_DIR/plan.md
- TASKS = FEATURE_DIR/tasks.md
- CONSTITUTION = `.specify/memory/constitution.md`

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
- Constitution checks and complexity tracking

**From tasks.md:**

- Task IDs and descriptions
- Phase grouping
- Parallel markers `[P]`
- Referenced file paths
- Unit and integration test tasks
- Dependency and ordering notes

**From optional docs:**

- `research.md`
- `data-model.md`
- `contracts/`
- `quickstart.md`
- `checklists/` when present

**From constitution:**

- Principle names
- `MUST` and `SHOULD` normative statements
- Required quality gates

Also inspect relevant repository files when needed to resolve uncertainty, especially project layout, test framework configuration, existing contracts, and naming conventions.

### 3. Build Semantic Models

Create internal representations:

- **Requirements inventory**: For each `FR-*`, buildable `SC-*`, acceptance scenario, edge case, and in-scope source requirement, record a stable key.
- **Task coverage mapping**: Map each task to one or more requirements, scenarios, success criteria, source requirements, or design artifacts.
- **Artifact authority order**:
  1. Constitution and explicit source design requirements
  2. Original feature input preserved in `spec.md`
  3. `spec.md` user-visible behavior and acceptance criteria
  4. `plan.md` architecture and technical decisions
  5. Design artifacts such as contracts, data model, research, and quickstart
  6. `tasks.md` execution breakdown
- **Project context**: Language, frameworks, test tools, file structure, conventions, and safe validation commands.

### 4. Detection Passes

Identify high-signal findings. Aggregate related issues instead of creating noisy one-off findings.

#### A. Duplication Detection

- Identify near-duplicate requirements, scenarios, entities, design decisions, or tasks.
- Mark lower-quality phrasing for consolidation.

#### B. Ambiguity Detection

- Flag vague adjectives such as fast, scalable, secure, intuitive, reliable, and robust when no measurable criterion exists.
- Flag unresolved placeholders such as TODO, TKTK, ???, `<placeholder>`, and `[NEEDS CLARIFICATION: ...]`.
- Flag unclear actors, data ownership, failure behavior, or validation expectations.

#### C. Underspecification

- Requirements with verbs but missing objects or measurable outcomes.
- Acceptance scenarios without expected observable result.
- Edge cases without requirement or task coverage.
- Success criteria requiring buildable work but missing validation path.
- Tasks referencing files, components, commands, or contracts not defined in spec or plan.

#### D. Constitution Alignment

- Any artifact conflicting with a constitution `MUST` principle.
- Missing mandated sections, quality gates, unit tests, integration tests, or verification requirements.

#### E. Coverage Gaps

- Requirements with zero associated tasks.
- In-scope source requirements with no mapped functional requirement or task.
- Buildable success criteria not reflected in tasks or quickstart.
- Required contracts not represented in tests or implementation tasks.

#### F. Inconsistency

- Terminology drift across artifacts.
- Data entities referenced in plan but absent in spec or data model, or vice versa.
- Task ordering contradictions.
- Test-first ordering contradictions.
- Conflicting technology, interface, persistence, or validation decisions.

### 5. Resolve Uncertainties

For each material uncertainty, choose a resolution without asking the user.

Use this decision process:

1. Name the uncertainty precisely.
2. Gather evidence from artifacts and repository context.
3. Identify 2-3 plausible resolutions.
4. Weigh pros and cons using:
   - conformance to constitution and source requirements
   - preservation of user-visible behavior
   - implementation blast radius
   - testability
   - consistency with existing architecture
   - reversibility
5. Choose the option that preserves higher-authority artifacts and requires the smallest coherent change.
6. Record the chosen assumption in the edited artifact when future implementers need to know it.

Default decisions:

- If `tasks.md` conflicts with `spec.md`, update `tasks.md` unless the spec is clearly incomplete relative to the original input.
- If `plan.md` conflicts with the constitution or explicit source design requirement, update `plan.md`.
- If `spec.md` is ambiguous but the repo has a clear established convention, update `spec.md` with that convention as an assumption or measurable requirement.
- If two options are equally plausible and neither is required by higher authority, choose the simpler and more reversible option.
- If a finding cannot be remediated without inventing product scope, add a bounded assumption and a validation task instead of asking the user.

### 6. Apply Remediation

Edit artifacts in this order:

1. Constitution conflicts.
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
   - no constitution `MUST` conflict remains
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
- If it exists, read it and look for entries under `hooks.after_align`. If none exist, also check `hooks.after_analyze` for compatibility with analyze-style extensions.
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally.
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable.
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation.
- For each executable hook, output the following based on its `optional` flag:
  - **Optional hook** (`optional: true`):
    ```
    ## Extension Hooks

    **Optional Hook**: {extension}
    Command: `/{command}`
    Description: {description}

    Prompt: {prompt}
    To execute: `/{command}`
    ```
  - **Mandatory hook** (`optional: false`):
    ```
    ## Extension Hooks

    **Automatic Hook**: {extension}
    Executing: `/{command}`
    EXECUTE_COMMAND: {command}
    ```
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently.

## Operating Principles

- **Automated remediation**: Apply artifact edits directly after analysis.
- **No clarification loop**: Resolve uncertainty with project context and conservative assumptions.
- **Authority preserving**: Never weaken constitution, source design, or original request intent to simplify downstream artifacts.
- **Traceability**: Maintain clear mappings from source requirements to spec requirements, tasks, tests, and validation.
- **Context efficiency**: Focus on high-signal findings and concise edit summaries.
- **Deterministic results**: Rerunning without changes should produce stable decisions and minimal diffs.
