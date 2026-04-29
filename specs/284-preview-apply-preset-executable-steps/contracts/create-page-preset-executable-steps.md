# Contract: Create Page Preset Preview and Apply

## Step Editor Contract

The Create page step editor exposes Step Type `Preset` alongside executable step types.

When Step Type `Preset` is selected:

- the author can select an available preset from the step editor,
- the author can configure preset inputs when the selected preset requires them,
- preview is disabled until a preset is selected,
- apply is disabled until a current preview exists.

## Preview Contract

Preview invokes the existing preset expansion source for the selected preset and input values.

Successful preview renders:

- generated step titles,
- generated Step Types,
- source/origin text when available,
- expansion warnings when present.

Failed preview renders a visible error and leaves the draft unchanged.

## Apply Contract

Apply replaces the selected temporary Preset step with the current preview's generated executable steps.

Applied generated steps:

- are normal editable Tool or Skill steps,
- preserve executable bindings,
- preserve source metadata when expansion provided it,
- can be submitted only after their own Tool or Skill validation passes.

## Submission Contract

The task submission payload must not contain unresolved Preset steps by default.

If unresolved Preset steps remain, submission is blocked before `/api/executions` is called.

Future linked-preset execution mode is outside this contract unless it is introduced as an explicit, visibly different mode with separate semantics.
