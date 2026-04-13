# Research: Jira Preset Reapply Signaling

## Decision: Track reapply-needed state from the last applied preset instructions value

**Rationale**: The feature needs to distinguish a current preset instruction draft from the value that was used when a preset was last expanded. Comparing those values lets the page show reapply-needed messaging only when already-expanded steps are potentially stale.

**Alternatives considered**:

- Mark every preset instruction edit as needing reapply. Rejected because this would create false warnings before any preset has been applied.
- Mark only manual typing as needing reapply. Rejected because Jira import changes the same user-facing preset field and has the same effect on preset-derived steps.

## Decision: Preserve expanded steps until explicit reapply

**Rationale**: Operators may have reviewed, edited, reordered, or supplemented the current step list after applying a preset. Hidden regeneration would risk losing that authored plan and would make Jira import feel destructive.

**Alternatives considered**:

- Automatically regenerate preset-derived steps on Jira import. Rejected because the spec explicitly forbids hidden rewrites.
- Disable Jira import after a preset has been applied. Rejected because operators still need to update preset objective text and decide when to reapply.

## Decision: Surface the existing preset action as an explicit reapply action while dirty

**Rationale**: A status message alone explains the condition, but an explicit reapply action makes the safe next step clear without introducing a second preset application mechanism.

**Alternatives considered**:

- Add a separate Reapply button next to Apply. Rejected because it would duplicate the same preset expansion action and complicate button availability.
- Keep the button label unchanged. Rejected because the spec requires users to understand when reapply is needed.

## Decision: Detect template-bound steps using existing instruction identity fields

**Rationale**: Create page step drafts already carry template identity and original template instructions. A step is still template-bound for instruction identity only while its current instructions match the template instructions and its live id matches the template step id.

**Alternatives considered**:

- Add a new boolean state for template-bound steps. Rejected because it duplicates derivable state and can drift from the existing detachment logic.
- Warn for every preset-derived step regardless of manual edits. Rejected because already-customized steps are no longer template-bound by instruction identity.

## Decision: Make the template-bound step warning informational, not blocking

**Rationale**: Jira import into a template-bound step is a valid manual customization. The operator needs to know the consequence, but should not need a second confirmation beyond choosing Replace or Append.

**Alternatives considered**:

- Require a confirmation dialog before import. Rejected because the spec says to warn while still allowing import and because extra confirmation adds friction without improving data safety.
- Block imports into template-bound steps. Rejected because Jira import must support any step instructions target.

## Decision: Validate through focused Create page tests

**Rationale**: The risk is client-side state transition correctness: reapply messaging, step preservation, button labeling, template-bound warning visibility, and template identity detachment. The existing Create page tests already mock presets, Jira browser data, import actions, and task submission.

**Alternatives considered**:

- Add backend tests. Rejected because this phase does not change backend runtime config, routers, storage, or task submission.
- Rely on manual smoke testing only. Rejected because regressions in preset and Jira state transitions are easy to automate and high impact.
