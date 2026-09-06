# Skill Input Schema UI Generation Design

**Document Class:** Canonical declarative  
**Status:** Desired-state design  
**Owners:** MoonMind Engineering (Workflow Platform + UI)  
**Last Updated:** 2026-09-06

Related: [Input Schema Guidance](InputSchemaGuidance.md), [Skill System](SkillSystem.md), [Step Types](StepTypes.md), [Workflow Presets System](../Workflows/WorkflowPresetsSystem.md), [Create Page](../UI/CreatePage.md), [Workflow Publishing](../Workflows/WorkflowPublishing.md).

## Purpose and Goals

MoonMind parses optional Skill input schemas and renders task-specific inputs through the same schema-form system used by Presets and Tools. There is no separate Skill form architecture or Skill-name switch in Create.

The normalized contract preserves schema, safe UI hints, defaults, content evidence, and authoritative context bindings. Repository, branch, and publication passthrough fields bind to the single workflow context rather than becoming independently editable controls.

A Skill without `inputSchema` remains selectable and can use instructions and attachments under admitted workflow authority. Optional structured metadata improves collection and validation, not permission to publish, change credentials, or choose another repository.

The design is declarative target state. Runtime implementation and qualification are separate from this document.

## Core Contract

```ts
type CapabilityKind = "tool" | "skill" | "preset";

type CapabilityInputContract = {
  id: string;
  kind: CapabilityKind;
  label: string;
  description?: string;
  inputSchema: JsonSchemaObject;
  uiSchema: Record<string, unknown>;
  defaults: Record<string, unknown>;
  contractDigest?: string;
  contentDigest?: string;
  source?: CapabilitySourceSummary;
  diagnostics?: CapabilityInputDiagnostic[];
};
```

`inputSchema` includes validated semantic binding declarations. Publication roles/default requirements are normalized with the existing capability definition and compiler metadata, not exposed as another input-form policy.

Representative normalized Skill contract:

```json
{
  "id": "pr-resolver",
  "kind": "skill",
  "label": "PR Resolver",
  "description": "Resolve a pull request through the selected portable Skill.",
  "inputSchema": {
    "type": "object",
    "required": ["pr"],
    "properties": {
      "repo": {
        "type": "string",
        "title": "Repository",
        "x-moonmind-context-binding": "repository.name"
      },
      "pr": {
        "type": "string",
        "title": "Pull request",
        "description": "PR number, PR URL, or a supported alternative head-branch locator."
      }
    }
  },
  "uiSchema": {},
  "defaults": {},
  "contentDigest": "sha256:...",
  "contractDigest": "sha256:..."
}
```

The UI renders the PR target, not a second editable repository. The runtime can still receive the projected portable `repo` argument. API casing is camelCase even when source metadata uses `input_schema` / `ui_schema`.

## Optional Skill Authoring

Skills may include schema frontmatter:

```yaml
---
name: github-issue-implement
description: Assess repository state, implement missing work, and prepare a pull request.
metadata:
  required-capabilities: [git, gh]
inputSchema:
  type: object
  required: [github_issue]
  properties:
    github_issue:
      type: object
      title: GitHub issue
      x-moonmind-semantic-type: issue-reference
      x-moonmind-provider: github
      required: [repository, number]
      properties:
        repository: {type: string}
        number: {type: integer}
        title: {type: string}
        body: {type: string}
        url: {type: string, format: uri}
uiSchema:
  github_issue:
    widget: github.issue-picker
    allowManualIssueEntry: true
---
```

An issue's repository is part of its target identity, not automatically a duplicated workspace binding. The target is checked against the admitted role, and a mismatch cannot silently switch repository authority. Field-name heuristics must not erase meaningful target data.

A Skill remains valid without this metadata. A trusted catalog adapter may bind an unchanged portable Skill's existing arguments without modifying its checked-in files or requiring MoonMind-specific frontmatter outside MoonMind.

## Sources and Parsing

Every selectable Skill source uses the same content-addressed normalization:

| Source | Behavior |
| --- | --- |
| Built-in | Parse bundled definition at startup/catalog load. |
| Deployment-stored | Parse submitted markdown during managed content update; persist metadata with the content artifact. |
| Repository | Parse allowed checked-in Skill sources through normal source policy. |
| Local-only | Parse allowed local sources without treating them as trusted deployment metadata. |
| Future bundle format | Adapt its input metadata to the same contract. |

The shared capability input normalizer, existing Skill-resolution/content service, and preset catalog retain their respective ownership. This design extends those owners rather than creating a parallel form/parser service.

The pipeline safely parses UTF-8 YAML frontmatter; extracts identity, description, required Skills/capabilities, schema, UI hints, defaults, and binding declarations; validates the root object/schema subset; preserves property order; normalizes API casing; computes the contract digest; and attaches bounded diagnostics.

The digest covers normalized schema, allowed UI hints, defaults, binding semantics/version, parser version, and source content evidence. Historical digests are not recomputed with new rules. A resolved workflow uses its pinned contract, not a later catalog parse.

## Error Policy

Lenient discovery must not make third-party adoption brittle, but it cannot erase authority constraints.

| Condition | Behavior |
| --- | --- |
| No schema/frontmatter | Preserve instruction-driven use with an empty schema. |
| Invalid optional schema shape | Show a diagnostic and safe instruction fallback when policy allows; managed strict publication may reject the definition. |
| Unsupported harmless keyword/widget | Safe fallback or actionable diagnostic without losing task input. |
| Unknown harmless presentation hint | Ignore or warn under policy. |
| Malformed/unsupported context binding or authority-bearing semantic requirement | Block the affected execution/definition before mutation. Never convert it into an editable override or ignore it. |
| Secret-like default | Reject/redact the default; strict managed save rejects unsafe content. |
| Malformed YAML | Managed save fails clearly; third-party discovery can retain safe instruction-only use when policy permits and no authority contract is bypassed. |

Absence of a schema is different from an incompatible repository/publication contract. The latter is explained as an execution-policy limitation, not as a demand to add MoonMind schema metadata.

## Schema Subset and Widgets

Supported authored-field signals include strings, multiline/markdown, numbers/integers, booleans, enums, arrays of enum items, URI/email/date/date-time formats, issue/repository/branch/file-reference semantics, and safe object/JSON fallbacks.

For required `oneOf`/`anyOf`, render a usable discriminator when variants have clear object shapes, or a safe structured editor with backend validation. Fields participating in required alternatives remain discoverable in guided mode.

The shared local widget registry contains text, textarea, markdown, number, checkbox, select, multi-select, JSON, Jira/GitHub issue, repository/branch, profile/model, and file-reference components. No remote components, arbitrary React identifiers, scripts, executable expressions, or unapproved schema fetches are allowed.

`uiSchema` controls safe placeholders, ordering, grouping, optional advanced disclosure, and registered presentation choices. It cannot change validation, defaults that confer authority, context-binding ownership, or publication policy.

## Authoritative Context Binding

[Input Schema Guidance](InputSchemaGuidance.md) owns the binding-key semantics. The renderer/validator recognizes `x-moonmind-context-binding`, including `repository.name`, `repository.branch`, and `publication.policy` where the consumer supports the required projection.

Bound values are computed from admitted workflow/target context. They are not editable in guided, Advanced, or raw-JSON forms, and they are not serialized as authored duplicates. A compact read-only source explanation is permitted. Compiler-owned runtime projections remain valid execution arguments.

Binding is not defaulting. The old `x-moonmind-context-default` behavior applies only to genuine defaultable task inputs or historical decoding. It must not preserve a stale Skill repository when the visible workflow repository changes.

A required bound input is resolved before schema validation. If missing, its error identifies the editable workflow control or target selector and the affected Skill. An unknown key, incompatible projected type, conflicting caller value, or forged compiler provenance fails before execution.

Existing PR targets resolve their own head and base. A PR locator is task input; a resolved head used to prepare the workspace is target evidence. PR-batch children can therefore have different heads without independent authored branch overrides or different batch policies.

A publication binding projects the frozen scope intent to the supported consumer contract. It never copies local coordinator `none`, sends unresolved `default` to a worker, drops merge/finish settings, or performs a helper-local default lookup.

## Create Page Flow

1. Select a Skill and load its normalized contract.
2. Match its definition evidence to the draft's selected or pinned definition.
3. Pass schema, task values, context, and policy to the shared renderer.
4. Show required unbound fields and required alternatives in guided mode. Optional unbound fields appear in Advanced mode.
5. Show bound-context explanations without another input control.
6. Validate locally for feedback and submit only authored task values plus the single workflow context.
7. Re-resolve/validate everything at backend admission.
8. Deliver compiler-owned normalized values to the runtime.

Example authored Skill step:

```json
{
  "id": "implement-github-issue",
  "title": "Implement GitHub issue",
  "type": "skill",
  "skill": {
    "name": "github-issue-implement",
    "inputContractDigest": "sha256:...",
    "inputs": {
      "github_issue": {"repository": "MoonLadderStudios/MoonMind", "number": 123},
      "constraints": "Preserve existing behavior."
    }
  }
}
```

The issue object remains target identity. Generic workflow repository/branch/publication copies do not appear as separate authored Skill inputs. Historical `args`/`selectedSkillArgs` are decoded through the versioned reader; new authoring writes `inputs` and cannot use an old alias to bypass bindings.

## Schema-less UI

Show title, description, instructions, attachments, and the existing workflow context. The runtime receives that context and may ask for missing task information. Do not add Skill-local repository, branch, publication, or profile selectors merely because no schema exists.

Optional authorized runtime specialization remains under its existing owner. Selecting a schema-less Skill does not authorize a fallback runtime, publisher, or credential route.

## Defaulting Rules

For **unbound task inputs**, use explicit user input, retained authored draft values, supported semantic task defaults/context defaults, declared `defaults[field]`, schema default, then empty value. Explicit values are validated rather than silently overwritten. A field's origin is preserved so a displayed default does not accidentally become an explicit override on round trip.

For **bound inputs**, resolve authoritative context first and reject caller-owned duplicates. The unbound precedence chain does not apply.

Do not use examples such as `defaults.branch: main` for workflow branch selection. An omitted repository branch is resolved through the canonical repository contract, and an explicit base is retained. Changing the workflow context rebinds all consumers.

Publication defaulting belongs to the workflow compiler. New authoring Auto is `default` or omission. Runtime `auto` is the distinct Skill-owned evidence protocol. Adding a Skill with such a requirement must update the shared compatibility preview, not silently rewrite an explicit None.

## Backend Validation

The existing Skill-step validation boundary consumes identity, content evidence, authored values, and workflow context:

```text
validateSkillStepInputs(skill_name, content_digest, inputs, workflow_context)
  -> normalized values, field-addressable errors, diagnostics, contract digest
```

It resolves the Skill/contract, validates binding declarations, resolves target/context projections, defaults unbound task inputs, validates all required alternatives/types/formats, checks integration references and publication compatibility, and emits normalized runtime inputs with provenance. UI schema is not required.

Errors retain the authored path where one exists:

```json
{
  "path": "steps[0].skill.inputs.github_issue.number",
  "message": "GitHub issue number is required.",
  "code": "required",
  "recoverable": true
}
```

Binding errors also identify the workflow-level source path rather than requiring edits to a hidden field. User values survive errors; stale derived values do not regain authority by remaining in a draft.

## Runtime Handoff

The runtime receives a compact resolved Skill identity/content reference, input-contract digest, validated task values, and compiler-bound projections. The Skill body remains the behavioral authority, not a second prompt language generated by the schema system.

Adapters do not reparse the latest `SKILL.md` to discover inputs or choose publishing. They execute the immutable resolved Skill snapshot and admitted contract. A portable argument may be populated by the adapter, but it cannot retarget the repository or enlarge the frozen scope policy.

Resolved values are evidence, not a new user-editable source. Retry and continuation reuse their pinned origin and target derivation. Restored files or old generated instructions cannot restore old credentials or broaden publication.

## Catalog and Persistence

Catalog list/detail responses expose the normalized contract or an immutable digest-bound reference. Small list entries may provide `hasInputSchema` and `inputContractRef`; detail returns the full contract. Casing normalization is lossless.

Deployment-stored Skill content persists extracted required Skills/capabilities, schema, UI hints, defaults, diagnostics, and contract digest with the content artifact. File-backed discovery caches by content identity and safe source metadata without mutating checked-in Skill files. Denormalization for lookup performance does not become another source of truth.

Existing shared capability normalization, schema validation, widget interpretation, Skill source adapters, preset/Skill catalogs, and the Create renderer own their respective responsibilities. A context-binding change extends those owners rather than duplicating preset-only and Skill-only implementations.

## Security, Diagnostics, and Observability

Use safe YAML, bounded schema/frontmatter/default sizes, no remote `$ref` fetch by default, approved widgets only, sanitized descriptions, and secret-free metadata. Required semantic bindings are validated as untrusted data, not treated as credentials or authority.

Diagnostics include invalid schema, unsupported keyword/widget, secret-like default, ignored harmless hint, fallback renderer, binding source unavailable, binding conflict, unsupported binding projection, and stale contract/context. Messages are bounded and safe for users; detailed source diagnostics stay in authorized admin/developer views.

Counters/traces cover parse success/failure/omission, generated field counts, fallback usage, backend errors, digest mismatch, binding conflicts, and stale-context rejection. Do not log raw task values or secret-bearing context.

## Draft Staleness and Historical Compatibility

A saved draft includes selected definition/input-contract evidence and authored values. On reload/submit, unchanged evidence validates normally. Changed definitions preserve task values and produce a visible revalidation/review state. A pinned workflow uses its pinned definition rather than the latest catalog.

Binding/context changes invalidate derived previews and values even when the Skill definition itself is unchanged. Apply/Reapply, unexpanded Submit, API/MCP, schedules, edit/rerun, and remediation use the same behavior.

Historical redundant copies can collapse only when proven equivalent. Conflicting copies, unknown origins, old two-branch intent, coordinator-local None, and old Skill-owned Auto require the publishing/reconstruction contract, not simple last-value-wins normalization. Historical bytes/digests and supported replay remain unchanged; new write paths do not retain permanent override aliases.

## Conformance

The actual parser/catalog/form/compiler/runtime boundaries must prove:

- Instruction-only third-party Skills remain importable, selectable, and usable.
- Equivalent Skill/Preset contracts render equivalent task inputs without name-based page logic.
- Required alternatives, text/numeric/boolean/enum/array/formatted/object fields, and registered integration widgets remain usable.
- Bound repository/branch/publication fields do not create duplicate editors in guided, Advanced, or raw authoring.
- Required bound values validate from current context; missing source errors focus the correct visible control.
- Workflow repository/base/PR/Run/policy changes invalidate stale async responses, expansions, and bound inputs.
- Duplicate caller values, forged resolved provenance, unknown authority-bearing hints, and incompatible projection types fail before mutation.
- Existing-PR target identity and genuine comparison/source/destination roles are not collapsed by naming heuristics.
- A batch coordinator's local None cannot replace inherited child policy, and unresolved authoring Auto never reaches a portable helper.
- Historical reconstruction preserves original meaning or requires explicit review without rehashing old evidence.
- Runtime execution uses the pinned Skill and bound context, not freshly parsed metadata or ambient defaults.
- Secret-like defaults, unsafe schemas, and remote component/ref loading remain blocked.

A UI-only test or correct parser return value is not proof that backend admission, fan-out, or the runtime honors the same bindings.
