# Research: Jira Import Actions

## Decision: Use the existing shared Jira browser as the only import surface

**Rationale**: The Create page already has a shared Jira browser with project, board, column, issue-list, and issue-detail state. Adding import actions there preserves one browser surface at a time and avoids duplicating Jira state near each editable field.

**Alternatives considered**:

- Add inline Jira pickers next to every target field. Rejected because it would duplicate loading state, error handling, and target-selection behavior.
- Add a separate Jira-driven task model. Rejected because the Create page remains MoonMind-native and Jira is only an instruction source.

## Decision: Derive import text from normalized issue detail already returned by MoonMind

**Rationale**: Issue detail already contains normalized description, acceptance criteria, and recommended target-specific imports. Import actions can use that data without additional network calls and without parsing Jira rich text in the browser.

**Alternatives considered**:

- Fetch a fresh issue payload on import. Rejected because selection already loads issue detail and an extra request would add latency and a new failure point.
- Let the browser derive text from raw Jira rich-text content. Rejected because the trusted MoonMind Jira boundary owns normalization.

## Decision: Default import modes by target

**Rationale**: Preset objective imports should default to Preset brief because the preset field drives high-level task objective resolution. Step imports should default to Execution brief because step instructions need actionable execution context.

**Alternatives considered**:

- Use one global default for all targets. Rejected because preset objectives and step instructions have different user intent.
- Remember the last mode across targets. Rejected for this phase because it can surprise operators when switching between preset and step targets.

## Decision: Implement Replace and Append as explicit write actions

**Rationale**: Selection and preview must remain read-only. Separate buttons make the write semantics clear and prevent accidental draft mutation.

**Alternatives considered**:

- Import immediately when selecting an issue. Rejected because the spec requires explicit import and because issue preview should be inspectable without side effects.
- Use a single import button with a separate replace/append selector. Rejected because two action buttons make the final write operation more direct and testable.

## Decision: Route step imports through the existing manual step update behavior

**Rationale**: The existing step update path already knows when template-bound step identity must detach after instruction edits. Jira import should behave exactly like a manual edit to preserve template metadata correctness.

**Alternatives considered**:

- Write directly to step state for Jira import. Rejected because it could bypass established template-detachment behavior.
- Keep template identity after Jira import. Rejected because customized instructions no longer match the original template step.

## Decision: Keep task submission unchanged

**Rationale**: This phase only copies Jira text into existing Create page fields. The current task submission contract already resolves objectives and serializes steps from those fields.

**Alternatives considered**:

- Persist Jira provenance in the task payload. Rejected for this phase because there is no downstream consumer and it would expand the submission contract.
- Add a Jira-native task type. Rejected because the Create page remains a task composition surface, not a Jira workbench.

## Decision: Validate through focused Create page tests plus normal frontend checks

**Rationale**: The risk is UI state transition correctness: target selection, import text generation, append/replace behavior, objective precedence, reapply messaging, and template detachment. Existing Create page tests already mock runtime config, Jira fetches, preset application, and submission payloads.

**Alternatives considered**:

- Add backend tests. Rejected for this phase unless implementation changes backend runtime config or browser endpoints.
- Rely only on manual browser testing. Rejected because regressions in Create page state transitions are high risk and already covered well by Vitest patterns.
