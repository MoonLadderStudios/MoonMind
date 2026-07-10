---
description: Verify the final implementation against original instructions, a declarative document, an issue brief, or optional MoonSpec artifacts, plus repo guidance and required tests.
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

This command is the final MoonSpec check. It verifies that the completed implementation satisfies the original instructions or authoritative declarative source. MoonSpec feature artifacts are optional derived context, not required authority.

1. **Setup**: Resolve the verification baseline in this order:
   - explicit user instructions or an explicitly referenced declarative document
   - issue-brief verification inputs
   - an explicitly provided `spec.md` or feature directory
   - an active feature directory discovered from repository context
   Use absolute paths for file-backed sources. Do not require `spec.md`, `plan.md`, or `tasks.md` when another usable baseline exists.

2. **Load verification sources**:
   - **Required**: the selected original-instruction, declarative-document, issue-brief, or spec baseline
   - **If present**: `AGENTS.md` for project principles, repo constraints, and test discipline
   - **If present**: `spec.md`, `plan.md`, `tasks.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`, `checklists/`
   - Extract requirements, acceptance-critical behavior, constraints, non-goals, stable claims, edge cases, and test expectations directly from the selected baseline
   - When a spec exists, also extract its user story, `SCN-*`, `FR-*`, `SC-*`, independent test, and source design mappings such as `DESIGN-REQ-*` or `DOC-REQ-*`
   - Extract relevant AGENTS.md `MUST` constraints and any explicit quality gates when present
   - Treat `spec.md`, `plan.md`, and `tasks.md` as optional derived context. They can help identify scope, expected files, commands, and sequencing, but they are never required authority or proof that behavior is implemented.

3. **Build a verification inventory**:
   - Create an internal checklist keyed by source requirements and traceable items:
     - One row per explicit source-direct requirement or stable declarative claim
     - One row per `FR-*`, acceptance scenario or `SCN-*`, and observable `SC-*` when a spec provides them
     - One row per relevant AGENTS.md principle, repo constraint, or testing-discipline item that affects implementation or verification
     - One row per in-scope `DESIGN-REQ-*` or `DOC-REQ-*`
   - For each row, track expected behavior, likely production code touchpoints, expected unit or integration tests, current status, evidence, and remaining gap

4. **Inspect implementation evidence**:
   - Review changed and relevant source files discovered from the selected baseline, repository search, optional tasks or plan, contracts, and quickstart
   - When `tasks.md` exists, treat checked tasks as a process check, not as implementation evidence
   - Confirm production code exists for each functional requirement
   - Inspect production code before tests so behavior is verified directly, not inferred from test names alone
   - Inspect startup wiring, registration, configuration binding, migrations, routing, background jobs, public contracts, or other integration points when the requirement depends on them
   - Confirm unit tests exist for domain behavior and edge cases
   - Confirm integration tests exist for acceptance scenarios, external interfaces, persistence, workflows, or other system interactions
   - Confirm in-scope source design requirements have code-or-test traceability; copied requirement text in `spec.md` is not implementation evidence
   - Treat comments, TODOs, dead code, unreferenced helpers, and documentation-only changes as non-evidence unless the requirement is explicitly documentation-only
   - Confirm implementation does not add hidden scope that contradicts the original request, source design, spec, or relevant repo guidance

5. **Run verification commands when available**:
   - Run unit test commands from `AGENTS.md`, README, build or CI configuration, `plan.md`, `tasks.md`, or project conventions
   - Run integration test commands from `AGENTS.md`, README, build or CI configuration, `plan.md`, `tasks.md`, quickstart, or project conventions
   - Run quickstart validation when `quickstart.md` exists and can be executed safely
   - Do not edit tracked files during verification. Normal disposable test artifacts are acceptable only when already ignored by the project.
   - If a command is unsafe, unavailable, or requires missing credentials/services, record it as "Not run" with the exact reason
   - Treat unit, compile, typecheck, lint, and repo-local hermetic checks as controlling evidence.
   - Treat integration, e2e, smoke, quickstart, map-entry, UI/browser, deployment, or external-service tests as advisory when they require unavailable credentials, services, deployed environments, proprietary/binary assets, large fixtures, game/editor map assets such as `.umap` files, simulators, or unsupported local tools.
   - Advisory tests must be reported, but their unavailability or environment-caused failure must not fail verification by itself. If an advisory failure reveals a concrete in-scope implementation defect, gate on that underlying defect instead of the suite label.

6. **Classify each verification item**:
   - Use these statuses:
     - `VERIFIED`: implementation and validation evidence satisfy the item
     - `PARTIAL`: some implementation exists, but behavior, wiring, or test coverage is incomplete
     - `MISSING`: no meaningful implementation evidence exists
     - `CONFLICT`: implementation contradicts the spec, original request, source design, or relevant repo guidance
     - `NO_DETERMINATION`: the selected baseline or repository evidence is too ambiguous to make a defensible call
   - Do not mark the feature complete unless every in-scope source requirement, relevant AGENTS.md principle, and source design requirement is `VERIFIED`
   - Missing scenario-driven unit coverage or repo-local hermetic validation for required behavior is at least a high-severity gap
   - Missing integration/e2e/map/deployment coverage is non-blocking when the required assets, services, credentials, or runtime fixtures are unavailable in the current checkout/runtime
   - Separate missing implementation from missing validation when both matter

7. **Compare implementation to the original request**:
   - For each requirement and acceptance scenario, classify evidence using the verification statuses above
   - Check whether success criteria are directly validated, indirectly supported, or unverified
   - Check whether assumptions made during specification still hold
   - Check whether integration tests cover the end-to-end behavior implied by the original request
   - Treat missing required unit tests or repo-local hermetic checks as a verification failure unless the selected verification baseline explicitly makes that class irrelevant
   - Do not choose a failing verdict solely because advisory integration/e2e/map/deployment validation cannot run or fails due to unavailable non-repo assets, services, credentials, or tooling. Use `FULLY_IMPLEMENTED` when all controlling evidence verifies; record advisory limitations as residual risk or non-blocking gaps.
   - Treat violated AGENTS.md `MUST` rules as blocking failures

8. **Produce the final verification report**:

   ```markdown
   # MoonSpec Verification Report

   **Feature**: [name]
   **Spec**: [path or N/A for source-direct/issue-brief mode]
   **Original Request Source**: [original instructions, declarative document path, issue brief, or spec.md `Input`]
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

   ## Story Scope, Principles, And Source Claim Coverage

   | Item | Evidence | Status | Notes |
   |------|----------|--------|-------|
   | DOC-REQ-001 / AGENTS.md principle | [file/test/reference] | VERIFIED/PARTIAL/MISSING/CONFLICT/NO_DETERMINATION | [notes] |

   ## Original Request Alignment

   - [Pass/fail summary against the verbatim original request]

   ## Gaps

   - [Blocking gaps first]

   ## Remaining Work

   - [Ordered, concrete code or test changes required before completion]

   ## Decision

   - FULLY_IMPLEMENTED only if implementation, unit tests, repo-local hermetic checks, in-scope source claims, relevant AGENTS.md principles, and original request alignment all verify. Advisory integration/e2e/map/deployment evidence should be included when available but does not block completion when unavailable for environment or asset reasons.
   ```

9. **Report completion**:
   - If `FULLY_IMPLEMENTED`: state that final verification passed, list the test commands run, and call out any residual risk
   - If `ADDITIONAL_WORK_NEEDED`: list blocking gaps and the smallest credible next implementation or test slice
   - If `NO_DETERMINATION`: state exactly what evidence, inspection, or command execution is needed to reach a defensible decision

## Post-Execution Checks

**Check for extension hooks (after final verification)**:
- Check if `.specify/extensions.yml` exists in the project root.
- If it exists, read it and look for entries under the `hooks.after_verify` key
- If the YAML cannot be parsed or is invalid, skip hook checking silently and continue normally
- Filter out hooks where `enabled` is explicitly `false`. Treat hooks without an `enabled` field as enabled by default.
- For each remaining hook, do **not** attempt to interpret or evaluate hook `condition` expressions:
  - If the hook has no `condition` field, or it is null/empty, treat the hook as executable
  - If the hook defines a non-empty `condition`, skip the hook and leave condition evaluation to the HookExecutor implementation
- For executable hooks:
  - Optional hooks: report the hook command and prompt.
  - Mandatory hooks: output `EXECUTE_COMMAND: {command}` and execute or delegate it.
- If no hooks are registered or `.specify/extensions.yml` does not exist, skip silently

## Key Rules

- Verification is read-only except for normal test artifacts.
- Original instructions, a declarative source document, an issue brief, or an optional `spec.md` may define the bounded verification scope.
- `spec.md`, `plan.md`, and `tasks.md` are disposable derived artifacts. Their absence never blocks verification when another usable baseline exists.
- When `spec.md` names a canonical source document, interpret the story against that document's in-scope stable claims; the canonical document remains the durable desired-state authority unless verified drift is handed off to doc reconciliation.
- Do not require unrelated claims from a larger canonical design to verify for this story, but do not let the temporary spec silently override an in-scope canonical conflict.
- Relevant AGENTS.md guidance defines repo principles, constraints, and test discipline for the story.
- `plan.md` and `tasks.md` are optional context but never proof of implementation.
- Unit tests and repo-local hermetic checks are controlling expected evidence. Integration/e2e/map/deployment checks are expected evidence when available, but they are advisory and non-blocking when they depend on unavailable non-repo assets, services, credentials, or tooling.
- Prefer direct, citeable repository evidence from production code, wiring, configuration, and tests.
- Do not mark the feature complete when required behavior is only inferred and not verified.
