# Data Model: Normalize Step Type API and Executable Submission Payloads

## Step Type

- Values: `tool`, `skill`, `preset`.
- Validation:
  - Draft/edit reconstruction may represent all three values.
  - Executable submission accepts `tool` and `skill` only by default.
  - `activity`, `script`, and `command` are not valid Step Type values.

## Draft Step

- Fields:
  - `id`: stable local identity when available.
  - `title`: optional display title.
  - `instructions`: optional authoring or execution instructions.
  - `stepType`: explicit Step Type discriminator.
  - `tool`: optional Tool payload for Tool steps.
  - `skill`: optional Skill payload for Skill steps.
  - `preset`: optional Preset payload for Preset steps.
  - `source`: optional provenance metadata.
  - `inputAttachments`: optional step attachment refs.
- Validation:
  - `stepType=tool` may carry `tool`.
  - `stepType=skill` may carry `skill`.
  - `stepType=preset` may carry `preset`.
  - Legacy readers may infer `stepType` from older `tool` or `skill` fields only for reconstruction.

## Executable Step

- Fields:
  - `type`: `tool` or `skill`.
  - Matching type-specific payload.
  - Optional source/provenance metadata.
- Validation:
  - Preset steps are rejected before runtime materialization.
  - Activity labels are rejected before runtime materialization.
  - Conflicting Tool/Skill payloads are rejected.

## State Transitions

1. Draft authoring may hold Tool, Skill, or Preset steps.
2. Preset preview/apply expands Preset steps into executable Tool and Skill steps.
3. Executable submission validates only Tool and Skill steps.
4. Runtime materialization consumes executable steps only.
