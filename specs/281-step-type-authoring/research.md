# Research: Present Step Type Authoring

## Decision 1: Reuse Existing Step Type Draft State

Decision: Treat the current `step.stepType` Create page draft state as the implementation foundation for MM-562.

Rationale: The current code already renders one Step Type selector per step and switches Tool, Skill, and Preset configuration areas from that value.

Evidence: `frontend/src/entrypoints/task-create.tsx` renders the `Step Type` select and conditional panels; `frontend/src/entrypoints/task-create.test.tsx` covers selector options and switching.

## Decision 2: Add Helper Copy Beside the Selector

Decision: Add concise helper copy under the Step Type selector describing Tool, Skill, and Preset choices.

Rationale: MM-562 specifically requires documented helper text or equivalent concise copy. Adding one dynamic helper preserves the compact editor while satisfying the acceptance criterion.

Alternatives considered: Expanding every option label with long text was rejected because native select options should remain short and scannable.

## Decision 3: Keep Existing Hidden Field Behavior

Decision: Preserve existing type-specific draft values when switching types, but submit only fields relevant to the selected Step Type.

Rationale: This satisfies the source design rule to preserve compatible fields and avoid silently submitting incompatible hidden fields.

Evidence: Existing tests cover instruction preservation and hidden Skill field handling when switching to Tool.
