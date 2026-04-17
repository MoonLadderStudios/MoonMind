# Create Page Preset State Contract

## Preset Application

- Selecting a preset updates only selected preset UI state.
- Pressing Apply or Reapply fetches template details and expansion from MoonMind REST endpoints.
- If the draft contains only the initial empty step, expanded preset steps replace that placeholder.
- If authored steps exist, expanded preset steps append after current steps.
- A successful Apply/Reapply records applied preset metadata and clears dirty state.

## Preset Objective Inputs

- Feature Request / Initial Instructions is the first source for resolved task objective text.
- Objective-scoped attachments are task-level input attachments.
- Changing objective text or objective-scoped attachments after Apply/Reapply marks the preset state dirty.
- Dirty state changes the action label to `Reapply preset` and status text explains that reapply is explicit.
- Dirty state must not mutate already expanded steps.

## Template-Bound Steps

- Preset-expanded steps may submit their template step ID only while authored instructions and attachment identity still match the template-authored input contract.
- Manual instruction edits detach template instruction identity.
- Manual attachment changes detach template input identity.
- Jira text or image import into a template-bound step is a manual edit.

## Payload Boundary

- Task objective text is submitted through `payload.task.instructions`.
- Objective-scoped attachments are submitted through `payload.task.inputAttachments`.
- Step-scoped attachments are submitted through `payload.task.steps[n].inputAttachments`.
- Applied template metadata is submitted through `payload.task.appliedStepTemplates` only after Apply/Reapply succeeds.
