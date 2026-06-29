# Input Schema Guidance

Status: Desired-state guidance
Owners: MoonMind Engineering (Workflow Platform + UI)
Related: `docs/Steps/StepTypes.md`, `docs/Steps/SkillSystem.md`, `docs/Workflows/WorkflowPresetsSystem.md`, `docs/UI/CreatePage.md`

---

## Purpose

This document defines how MoonMind should treat optional input schemas for Skills,
Presets, Tools, and future selectable step options.

MoonMind should make structured input collection better when a selected Skill,
Preset, or Tool provides an input schema, but schema authoring must not become a
barrier to third-party Skill adoption. A plain Agent Skills-style `SKILL.md` with
instructions and no structured input schema remains valid. In that case, the
runtime agent may infer, request, or extract the information it needs from the
natural-language task, Skill body, Workflow instructions, and available context,
as agents normally do.

This document intentionally avoids using "capability input" as the primary term.
MoonMind already uses capability-related language for step-added capabilities and
`requiredCapabilities`; this guidance is about optional structured input schemas
for selected Skills, Presets, Tools, and similar step options.

---

## Core Policy

1. `inputSchema` / `input_schema` is optional for Skills and third-party Skill
   integrations.
2. When present, `inputSchema` is the canonical structured contract for user
   input values, validation, defaults, API submission, and draft persistence.
3. When absent, MoonMind must fall back to instruction-driven execution rather
   than rejecting the selected Skill solely because it lacks a schema.
4. Skill and Preset authors should describe input data and semantics, not
   MoonMind UI components.
5. MoonMind should derive the default UI from `inputSchema` using platform-owned
   schema-to-form logic and a local widget registry.
6. `uiSchema` / `ui_schema`, when accepted, is optional presentation metadata and
   must be treated as a non-authoritative override layer rather than the normal
   authoring requirement.

This policy preserves easy Skill import while enabling a richer Start Workflow /
Create page experience for Skills and Presets that choose to expose structured
inputs.

---

## Step Option Catalog Shape

A selected step option may expose a normalized input contract:

```json
{
  "id": "jira-implement",
  "kind": "preset",
  "label": "Jira Implement",
  "description": "Move a Jira issue through implementation.",
  "inputSchema": {},
  "uiSchema": {},
  "defaults": {}
}
```

The fields are optional unless the selected step option has a stronger internal
requirement. For example, many Tools have hard typed contracts because they
execute deterministic operations. Third-party Skills may omit `inputSchema` and
still be selectable.

When `inputSchema` is omitted, the catalog response should either omit the field
or expose an empty object. The UI must not interpret an empty schema as a broken
Skill. It should provide an instruction-oriented fallback such as a general
instructions field, context attachments, or direct launch with the selected
Skill.

---

## Recommended Skill Frontmatter

A Skill that wants first-class MoonMind input fields may include `inputSchema` in
frontmatter:

```yaml
---
name: pr-resolver
description: Resolve a pull request by diagnosing state and delegating to specialized skills.
metadata:
  required-skills: "fix-comments fix-ci fix-merge-conflicts"
  required-capabilities:
    - git
    - gh
inputSchema:
  type: object
  required:
    - pr
  properties:
    repo:
      type: string
      title: Repository
      description: GitHub repository in owner/name form. Defaults from workflow context when available.
      x-moonmind-context-default: repository
    pr:
      type: string
      title: Pull request
      description: PR number, PR URL, or branch name.
    mergeMethod:
      type: string
      title: Merge method
      enum:
        - merge
        - squash
        - rebase
      default: squash
---
```

This metadata improves form generation and validation, but the same Skill remains
valid if it only contains the standard Agent Skills frontmatter plus a prose
`## Inputs` section or no explicit input section at all.

---

## Preset Guidance

Presets that require deterministic expansion should prefer explicit
`inputSchema`, because expansion often needs field-addressable validation and
stable bindings into generated steps.

However, `inputSchema` should still be understood as an enhancement path rather
than a universal file-format requirement. A preset with no user-configurable
inputs may omit it or expose an empty object schema:

```yaml
inputSchema:
  type: object
  properties: {}
```

A preset that expects values for expansion should provide enough schema metadata
for MoonMind to validate and persist those values before apply or submit-time
expansion.

---

## UI Derivation Policy

MoonMind owns the default form-generation behavior. The normal path is:

1. Load a selected Skill, Preset, or Tool's `inputSchema`, if present.
2. Derive an internal UI contract from schema types, formats, annotations,
   property names, deployment policy, integration availability, and workflow
   context.
3. Render fields through the platform widget registry.
4. Validate locally for immediate feedback where safe.
5. Validate again in the backend before execution, expansion, or submission.

Examples of default derivation:

| Schema signal | Default MoonMind interpretation |
| --- | --- |
| `type: string` | Text input |
| `type: string`, long description, or markdown semantic format | Textarea or markdown editor |
| `type: boolean` | Checkbox |
| `enum` | Select |
| `type: array` with enum items | Multi-select |
| `format: uri` | URL input |
| `format: date` / `date-time` | Date or date-time input |
| Object with Jira issue semantics | Jira issue picker when Jira is available; safe manual fallback otherwise |
| Object with GitHub repository / PR semantics | GitHub picker when GitHub context is available; safe manual fallback otherwise |
| Unknown object | Structured JSON/object editor fallback |

A Skill or Preset file should not need to name `jira.issue-picker`,
`github.branch-picker`, or another concrete MoonMind component for the UI to be
usable.

---

## Semantic Hints

Skill and Preset authors may include stable semantic hints in `inputSchema` when
the plain JSON Schema shape is not enough:

```yaml
inputSchema:
  type: object
  properties:
    target_issue:
      type: object
      title: Target issue
      x-moonmind-semantic-type: issue-reference
      x-moonmind-provider: jira
      required:
        - key
      properties:
        key:
          type: string
```

Semantic hints describe what the value means. MoonMind decides how to render that
meaning in the current deployment. This is preferred over binding a Skill or
Preset to a concrete widget name.

Rules:

1. Custom fields must be namespaced with `x-moonmind-*`.
2. Semantic hints must be safe declarative metadata, not executable code.
3. Hints must not contain secrets, tokens, raw credentials, or URLs with embedded
   credentials.
4. Unknown hints should be ignored or surfaced as non-blocking diagnostics unless
   a deployment policy says otherwise.

---

## Optional `uiSchema`

MoonMind may support `uiSchema` / `ui_schema` for compatibility with existing
schema-form ecosystems and for narrow presentation overrides. It is not the
preferred source of truth for Skill or Preset authors.

Valid uses:

- marking a field as advanced or collapsed by default
- selecting between platform-supported generic presentations when the schema is
  ambiguous
- adding safe display hints that do not affect validation or execution semantics

Avoid:

- coupling a Skill or Preset to a concrete React component
- defining layout that belongs to the Create page
- encoding deployment-specific integration assumptions
- putting validation requirements only in `uiSchema`
- including executable code, expressions, scripts, or secrets

Effective UI metadata should be layered in this order:

1. MoonMind schema-derived defaults
2. deployment or admin policy overrides
3. schema-authored semantic hints
4. narrowly-scoped `uiSchema`, when allowed

Backend validation must always use `inputSchema` and execution policy, not
`uiSchema`.

---

## Fallback Behavior When No Schema Exists

When a selected Skill or third-party integration has no `inputSchema`, MoonMind
should preserve the current agent-native behavior:

1. Show the Skill description, instructions, and any prose input guidance.
2. Let the user provide natural-language instructions and context attachments.
3. Pass the Skill body and Workflow context to the agent runtime.
4. Let the agent infer, ask for, or extract missing values from the instructions
   and available context.
5. Surface runtime blockers clearly if required information is unavailable.

The absence of `inputSchema` should reduce structured UI affordances, not prevent
use of the Skill.

---

## Acceptance Criteria

- A third-party `SKILL.md` without `inputSchema` can be imported and selected.
- A Skill or Preset with `inputSchema` gets schema-generated input fields in the
  MoonMind UI.
- MoonMind derives default UI behavior from `inputSchema` without requiring
  Skill- or Preset-authored `uiSchema`.
- Skill- or Preset-authored `uiSchema`, when present, is optional, safe, and
  non-authoritative.
- Backend validation and preset expansion never depend on frontend-only UI hints.
- Adding an `inputSchema` improves the authoring experience but is not required
  for basic agent-driven execution.
