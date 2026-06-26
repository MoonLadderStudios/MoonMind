---
description: Verify the final implementation against the original feature request, specification, plan, tasks, and required tests.
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

**Check for extension hooks (before final verification)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.before_verify` key
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation
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

    Wait for the result of the hook command before proceeding to the Outline.
    ```
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently

## Outline

This command is the final Moon Spec check. It verifies that the completed implementation satisfies the original feature request preserved in `spec.md`, not just the later task list.

1. **Setup**: Run `{SCRIPT}` from repo root and parse FEATURE_DIR and AVAILABLE_DOCS list. All paths must be absolute. For single quotes in args like "I'm Groot", use escape syntax: e.g 'I'\''m Groot' (or double-quote if possible: "I'm Groot").

2. **Load verification sources**:
   - **Required**: `spec.md`, `plan.md`, `tasks.md`, `.specify/memory/constitution.md`
   - **If exists**: `research.md`, `data-model.md`, `contracts/`, `quickstart.md`, `checklists/`
   - Extract the original request from the `**Input**` field in `spec.md`
   - Extract the single user story, acceptance scenarios or `SCN-*`, functional requirements `FR-*`, success criteria `SC-*`, edge cases, assumptions, independent test, and source design mappings such as `DESIGN-REQ-*` or `DOC-REQ-*`
   - Extract constitution `MUST` constraints and any explicit quality gates
   - Treat `plan.md` and `tasks.md` as intent and process context only. They can help identify expected files, commands, and sequencing, but they are never proof that behavior is implemented.

3. **Build a verification inventory**:
   - Create an internal checklist keyed by spec IDs and traceable items:
     - One row per `FR-*`
     - One row per acceptance scenario or `SCN-*`
     - One row per observable `SC-*`
     - One row per constitution constraint or `CC-*`
     - One row per in-scope `DESIGN-REQ-*` or `DOC-REQ-*`
   - For each row, track expected behavior, likely production code touchpoints, expected unit or integration tests, current status, evidence, and remaining gap

4. **Inspect implementation evidence**:
   - Review changed and relevant source files named by `tasks.md`, `plan.md`, contracts, and quickstart
   - Confirm all tasks in `tasks.md` are marked complete as a process check, not as implementation evidence
   - Confirm production code exists for each functional requirement
   - Inspect production code before tests so behavior is verified directly, not inferred from test names alone
   - Inspect startup wiring, registration, configuration binding, migrations, routing, background jobs, public contracts, or other integration points when the requirement depends on them
   - Confirm unit tests exist for domain behavior and edge cases
   - Confirm integration tests exist for acceptance scenarios, external interfaces, persistence, workflows, or other system interactions
   - Confirm in-scope source design requirements have code-or-test traceability; copied requirement text in `spec.md` is not implementation evidence
   - Treat comments, TODOs, dead code, unreferenced helpers, and documentation-only changes as non-evidence unless the requirement is explicitly documentation-only
   - Confirm implementation does not add hidden scope that contradicts the original request

5. **Run verification commands when available**:
   - Run unit test commands from `plan.md`, `tasks.md`, or project conventions
   - Run integration test commands from `plan.md`, `tasks.md`, quickstart, or project conventions
   - Run quickstart validation when `quickstart.md` exists and can be executed safely
   - Do not edit tracked files during verification. Normal disposable test artifacts are acceptable only when already ignored by the project.
   - If a command is unsafe, unavailable, or requires missing credentials/services, record it as "Not run" with the exact reason

6. **Classify each verification item**:
   - Use these statuses:
     - `VERIFIED`: implementation and validation evidence satisfy the item
     - `PARTIAL`: some implementation exists, but behavior, wiring, or test coverage is incomplete
     - `MISSING`: no meaningful implementation evidence exists
     - `CONFLICT`: implementation contradicts the spec, original request, source design, or constitution
     - `NO_DETERMINATION`: the spec or repository evidence is too ambiguous to make a defensible call
   - Do not mark the feature complete unless every in-scope `FR-*`, constitution constraint, and source design requirement is `VERIFIED`
   - Missing scenario-driven unit or integration coverage for required behavior is at least a high-severity gap
   - Separate missing implementation from missing validation when both matter

7. **Compare implementation to the original request**:
   - For each requirement and acceptance scenario, classify evidence using the verification statuses above
   - Check whether success criteria are directly validated, indirectly supported, or unverified
   - Check whether assumptions made during specification still hold
   - Check whether integration tests cover the end-to-end behavior implied by the original request
   - Treat missing required unit or integration tests as a verification failure unless the spec explicitly makes that class irrelevant
   - Treat violated constitution `MUST` rules as blocking failures

8. **Produce the final verification report**:

   ```markdown
   # MoonSpec Verification Report

   **Feature**: [name]
   **Spec**: [path]
   **Original Request Source**: spec.md `Input`
   **Verdict**: FULLY_IMPLEMENTED | ADDITIONAL_WORK_NEEDED | NO_DETERMINATION
   **Confidence**: HIGH | MEDIUM | LOW

   ## Test Results

   | Suite | Command | Result | Notes |
   |-------|---------|--------|-------|
   | Unit | [command] | PASS/FAIL/NOT RUN | [notes] |
   | Integration | [command] | PASS/FAIL/NOT RUN | [notes] |

   ## Requirement Coverage

   | Requirement | Evidence | Status | Notes |
   |-------------|----------|--------|-------|
   | FR-001 | [file/test/reference] | VERIFIED/PARTIAL/MISSING/CONFLICT/NO_DETERMINATION | [notes] |

   ## Acceptance Scenario Coverage

   | Scenario | Evidence | Status | Notes |
   |----------|----------|--------|-------|

   ## Constitution And Source Design Coverage

   | Item | Evidence | Status | Notes |
   |------|----------|--------|-------|
   | DESIGN-REQ-001 / CC-001 | [file/test/reference] | VERIFIED/PARTIAL/MISSING/CONFLICT/NO_DETERMINATION | [notes] |

   ## Original Request Alignment

   - [Pass/fail summary against the verbatim original request]

   ## Gaps

   - [Blocking gaps first]

   ## Remaining Work

   - [Ordered, concrete code or test changes required before completion]

   ## Decision

   - FULLY_IMPLEMENTED only if implementation, unit tests, integration tests, source design requirements, constitution constraints, and original request alignment all verify.
   ```

9. **Report completion**:
   - If `FULLY_IMPLEMENTED`: state that final verification passed, list the test commands run, and call out any residual risk
   - If `ADDITIONAL_WORK_NEEDED`: list blocking gaps and the smallest credible next implementation or test slice
   - If `NO_DETERMINATION`: state exactly what evidence, inspection, or command execution is needed to reach a defensible decision

## Post-Execution Checks

**Check for extension hooks (after final verification)**:
Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.after_verify` key
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation
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
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently

## Key Rules

- Verification is read-only except for normal test artifacts.
- The original request in `spec.md` is the source of truth for final alignment.
- `spec.md` plus `.specify/memory/constitution.md` define the governing requirements.
- `plan.md` and `tasks.md` are useful context but never proof of implementation.
- Unit tests and integration tests are both expected evidence.
- Prefer direct, citeable repository evidence from production code, wiring, configuration, and tests.
- Do not mark the feature complete when required behavior is only inferred and not verified.
