# Quickstart: Create Page Composed Preset Drafts

## Validate Source Traceability

```bash
rg -n "MM-384|AppliedPresetBinding|StepDraft.source|DESIGN-REQ-016" specs/197-create-page-composed-drafts
```

Expected result: MM-384 appears in the canonical input and MoonSpec artifacts; `AppliedPresetBinding`, `StepDraft.source`, and the required design coverage appear in the spec artifacts.

## Validate Create Page Contract

```bash
rg -n "AppliedPresetBinding|StepDraft.source|preset-bound|grouped composition|flat reconstruction|Reapply preset|save-as-preset|flatten" docs/UI/CreatePage.md
```

Expected result: the Create page contract contains the composed preset draft terms required by MM-384.

## Validate Legacy Terminology Removal

```bash
! rg -n "template-bound|appliedTemplates|AppliedTemplateState" docs/UI/CreatePage.md
```

Expected result: no matches remain in the Create page contract.

## Final Verification

Run `/moonspec-verify` equivalent by auditing `spec.md`, `plan.md`, `tasks.md`, `docs/UI/CreatePage.md`, the Jira input artifact, and the grep evidence above.
