# Data Model: Compile Step Type Payloads Into Runtime Plans and Promotable Proposals

## ExecutableStep

Represents a submitted task step that can be materialized for execution.

Fields:
- `id`: optional stable local identity.
- `title`: optional display label.
- `instructions`: optional step instructions.
- `type`: required when using the explicit Step Type contract; accepted executable values are `tool` and `skill`.
- `tool`: required payload for `type: "tool"`.
- `skill`: required payload for `type: "skill"`.
- `source`: optional `StepSource` provenance metadata.

Validation rules:
- `type: "preset"`, `activity`, and `Activity` are rejected at executable boundaries.
- Tool steps must include a Tool payload and must not include a Skill payload.
- Skill steps must not include a non-skill Tool payload.

## StepSource

Metadata describing how an executable step was produced.

Fields:
- `kind`: one of `manual`, `preset-derived`, `preset-include`, or `detached`.
- `presetId` / `presetSlug`: optional preset identity.
- `version`: optional preset version.
- `includePath`: optional source include path.
- `originalStepId`: optional original preset step identity.

Validation rules:
- Source metadata is optional.
- Source metadata must not decide runtime materialization.
- Source metadata may be used for audit, UI grouping, proposal reconstruction, review, and explicit refresh workflows.

## RuntimePlanNode

Internal execution node generated from an executable step.

Fields:
- `tool`: internal runtime tool descriptor.
- `inputs`: normalized runtime inputs, including selected Tool/Skill identifiers and optional source metadata.

State transitions:
- Tool executable step -> typed tool plan node.
- Skill executable step -> agent runtime plan node.
- Preset authoring placeholder -> no runtime node by default; must expand before executable submission.

## PromotableProposal

Stored proposal that can be promoted into a task execution.

Fields:
- `taskCreateRequest`: reviewed task creation envelope.
- `taskCreateRequest.payload.task.steps`: flat executable steps.
- `taskCreateRequest.payload.task.authoredPresets`: optional audit/reconstruction metadata.
- `status`: proposal lifecycle status.

Validation rules:
- Promotion validates the stored task payload under the canonical task contract.
- Promotion rejects non-executable stored payloads.
- Promotion may apply bounded runtime overrides without rewriting reviewed steps or provenance.
- Promotion must not silently re-expand live preset catalog entries.
