# Skill Input Schema UI Generation Design

Status: Desired-state design
Owners: MoonMind Engineering (Workflow Platform + UI)
Related: `docs/Steps/InputSchemaGuidance.md`, `docs/Steps/SkillSystem.md`, `docs/Steps/StepTypes.md`, `docs/Workflows/WorkflowPresetsSystem.md`, `docs/UI/CreatePage.md`

---

## Purpose

This document defines the system MoonMind should use to parse optional input
schemas from Skills and generate Create page UI fields for Skill-step inputs.

The design intentionally mirrors the preset path already described and partly
implemented for schema-driven preset inputs. Skills should not get a separate
hard-coded form system. A selected Skill should expose the same normalized
capability input contract as a selected Preset:

```json
{
  "id": "github-issue-implement",
  "kind": "skill",
  "label": "GitHub Issue Implement",
  "description": "Implement a GitHub issue and prepare a pull request.",
  "inputSchema": {},
  "uiSchema": {},
  "defaults": {}
}
```

When a Skill has no `inputSchema`, MoonMind must preserve agent-native behavior:
the Skill remains selectable, the user may provide natural-language instructions
and context, and the runtime agent may infer or ask for missing information.

---

## Design Goals

1. Reuse the shared schema-form renderer that presets use.
2. Parse `inputSchema` / `input_schema` from `SKILL.md` frontmatter and
   deployment-stored Skill content.
3. Normalize Skills, Presets, and Tools into one capability input contract shape.
4. Make Skill input schemas optional and non-blocking for third-party Skill
   adoption.
5. Validate Skill-step inputs locally for UX and again in the backend before
   execution.
6. Keep `uiSchema` optional, safe, and presentation-only.
7. Avoid Skill-specific Create page branches such as
   `if skill.name === "github-issue-implement"`.
8. Preserve deterministic provenance by tying the input contract to Skill content
   evidence.

---

## Non-Goals

- This design does not make `inputSchema` mandatory for Agent Skills-style
  `SKILL.md` files.
- This design does not define a custom React component per Skill.
- This design does not allow executable code, dynamic expressions, or remote
  component loading from Skill metadata.
- This design does not replace the agent runtime's ability to infer, request, or
  extract missing values from natural-language context.
- This design does not turn Skills into Tools. A Skill step remains an agentic
  executable step.

---

## Existing Preset Pattern To Reuse

The preset system already establishes the target shape:

1. A preset definition may declare `inputSchema`, `uiSchema`, and `defaults`.
2. The backend normalizes legacy preset input rows and schema annotations into
   the same catalog contract.
3. The Create page renders fields from that contract with a generic
   schema-form renderer.
4. The backend validates inputs again before apply, reapply, submit-time
   expansion, or API-created workflows.
5. Preset-specific forms are forbidden; reusable widgets are allowed.

Skill input parsing should adopt the same pattern with one important difference:
a Skill does not expand into child steps. It remains executable as a Skill step,
so validated inputs become `step.skill.inputs` rather than preset expansion
inputs.

---

## Core Contract

Every selectable Step capability should expose a normalized input contract.

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

For Skills:

```json
{
  "id": "pr-resolver",
  "kind": "skill",
  "label": "PR Resolver",
  "description": "Resolve a pull request by diagnosing state and delegating to specialized skills.",
  "inputSchema": {
    "type": "object",
    "required": ["pr"],
    "properties": {
      "repo": {
        "type": "string",
        "title": "Repository",
        "description": "GitHub repository in owner/name form.",
        "x-moonmind-context-default": "repository"
      },
      "pr": {
        "type": "string",
        "title": "Pull request",
        "description": "PR number, PR URL, or branch name."
      }
    }
  },
  "uiSchema": {},
  "defaults": {},
  "contentDigest": "sha256:...",
  "contractDigest": "sha256:..."
}
```

API responses should use camelCase (`inputSchema`, `uiSchema`) even when source
files use snake_case (`input_schema`, `ui_schema`). Ingestion may accept both.

---

## Skill Authoring Shape

A Skill may declare an input schema in frontmatter:

```yaml
---
name: github-issue-implement
description: Implement a GitHub issue by assessing repository state, completing missing work, and preparing a pull request.
metadata:
  required-capabilities:
    - git
    - gh
inputSchema:
  type: object
  required:
    - github_issue
  properties:
    github_issue:
      type: object
      title: GitHub issue
      description: Issue that should seed implementation context.
      x-moonmind-semantic-type: issue-reference
      x-moonmind-provider: github
      required:
        - repository
        - number
      properties:
        repository:
          type: string
          title: Repository
        number:
          type: integer
          title: Issue number
        title:
          type: string
        body:
          type: string
        url:
          type: string
          format: uri
uiSchema:
  github_issue:
    widget: github.issue-picker
    dataSource: github.issues
    searchPlaceholder: Search GitHub issues
    allowManualIssueEntry: true
defaults: {}
---
```

The same Skill remains valid if the frontmatter omits `inputSchema`.

---

## Parsing Sources

MoonMind should parse Skill input contracts from every Skill source that can
produce a selectable Skill:

| Source | Parse strategy |
| --- | --- |
| Built-in Skills | Parse bundled `SKILL.md` at startup or catalog load. |
| Deployment-stored Skills | Parse submitted markdown during `AgentSkillsService.update_skill_content`. Store extracted metadata with the content artifact. |
| Repo checked-in Skills | Parse `.agents/skills/<name>/SKILL.md` during repo Skill discovery when policy allows repo Skills. |
| Local-only Skills | Parse `.agents/skills/local/<name>/SKILL.md` only when policy allows local Skills. |
| Future bundled Skill format | Read the manifest field that corresponds to `inputSchema`, then normalize into the same contract. |

Parsing should be content-addressed. Given the same Skill content and the same
platform parser version, MoonMind should produce the same normalized contract and
digest.

---

## Parser Pipeline

Introduce a shared parser/normalizer module for capability input contracts, for
example:

```text
moonmind/capabilities/input_contracts.py
```

The module should not be preset-specific. It should support:

```text
parseCapabilityInputContract(raw_metadata, owner) -> CapabilityInputContractParts
normalizeCapabilityInputContract(parts, owner, policy) -> CapabilityInputContract
validateCapabilityInputs(contract, values, context) -> ValidationResult
```

A Skill-specific adapter should feed frontmatter into the shared parser:

```text
Skill markdown
  -> safe frontmatter parser
  -> SkillInputMetadata
  -> shared CapabilityInputContract normalizer
  -> Skill catalog response
  -> shared schema-form renderer
```

Detailed steps:

1. Read the `SKILL.md` markdown as UTF-8 text.
2. If the file starts with YAML frontmatter, parse it with a safe YAML loader.
3. Extract:
   - `name`
   - `description`
   - `metadata.required-skills`
   - `metadata.required-capabilities`
   - `inputSchema` or `input_schema`
   - `uiSchema` or `ui_schema`
   - `defaults`
4. Normalize the input contract:
   - require the root schema to be a JSON object schema when present
   - preserve field order from `properties`
   - preserve standard JSON Schema validation keywords
   - preserve safe `x-moonmind-*` semantic hints
   - normalize API casing to camelCase
5. Compute `contractDigest` from the normalized `inputSchema`, `uiSchema`,
   `defaults`, parser version, and Skill `contentDigest`.
6. Attach diagnostics for unsupported or ignored fields.
7. Expose the contract to the Skill catalog and Create page.

---

## Frontmatter Error Policy

Skill schema parsing must not make third-party Skill import brittle.

| Condition | Desired behavior |
| --- | --- |
| No frontmatter | Skill is valid; expose empty schema and a fallback instructions UI. |
| Frontmatter exists but no `inputSchema` | Skill is valid; expose empty schema. |
| `inputSchema` is not an object | Skill remains selectable; omit generated fields and surface a non-blocking diagnostic unless strict policy is enabled. |
| `inputSchema` root type is not `object` | Same as invalid schema; root object is required for field generation. |
| Unsupported JSON Schema keyword | Preserve when safe if the validator can ignore it; otherwise add a diagnostic and degrade to a safe field. |
| Unknown `x-moonmind-*` hint | Ignore or surface a diagnostic; do not block. |
| Unknown non-namespaced custom hint | Ignore and warn in authoring/admin surfaces. |
| Secret-like default value | Reject the default, emit a diagnostic, and require user entry. In strict managed-skill save flows, reject the save. |
| Malformed YAML | Managed save should fail clearly. Third-party discovery may skip structured metadata and keep the Skill available when policy allows. |

Deployment policy may enable strict managed-skill validation for centrally
published Skills, but lenient discovery should remain available for repo and
third-party Skills.

---

## Schema Subset For Generated Skill Fields

MoonMind should support the same practical JSON Schema subset used for preset
inputs:

| Schema signal | Default field |
| --- | --- |
| `type: string` | Text input |
| `type: string`, long description, `format: markdown`, or `x-moonmind-multiline: true` | Textarea / markdown editor |
| `type: integer` / `number` | Numeric input |
| `type: boolean` | Checkbox |
| `enum` | Select |
| `type: array` with enum items | Multi-select |
| `format: uri` | URL input |
| `format: email` | Email input |
| `format: date` | Date input |
| `format: date-time` | Date-time input |
| `x-moonmind-semantic-type: issue-reference` + provider | Provider issue picker when available |
| `x-moonmind-semantic-type: repository` | Repository picker when available |
| `x-moonmind-semantic-type: branch` | Branch picker when repository context is available |
| `x-moonmind-semantic-type: file-reference` | File/artifact picker |
| Unknown object | JSON/object editor fallback |
| Unsupported combination | Safe fallback plus actionable diagnostic |

`oneOf` and `anyOf` may be supported incrementally. The safe initial behavior is:

1. render a discriminator/select when every variant has a clear title and object
   shape;
2. otherwise use the JSON editor fallback and backend validation.

---

## UI Schema Handling

`uiSchema` is optional presentation metadata. It must not affect backend
validation or execution semantics.

Allowed uses:

- selecting a registered widget when schema signals are ambiguous
- placeholder and helper copy
- grouping advanced fields
- ordering fields
- enabling safe manual fallback behavior for integration pickers

Example:

```yaml
uiSchema:
  github_issue:
    widget: github.issue-picker
    dataSource: github.issues
    searchPlaceholder: Search GitHub issues
    allowManualIssueEntry: true
  constraints:
    widget: textarea
    advanced: true
```

Disallowed uses:

- executable expressions
- scripts
- arbitrary React component names
- remote component URLs
- credentials or secrets
- validation rules that are absent from `inputSchema`

The frontend widget registry is the only place that maps widget identifiers to
components.

---

## Widget Registry

Skill fields should use the same widget registry as preset fields.

Initial widgets:

| Widget | Purpose |
| --- | --- |
| `text` | Single-line string input |
| `textarea` | Multi-line text input |
| `markdown` | Markdown editor |
| `number` | Integer/number input |
| `checkbox` | Boolean input |
| `select` | Enum or simple `oneOf` selector |
| `multi-select` | Array of enum values |
| `json` | Advanced object/array fallback |
| `jira.issue-picker` | Jira issue search/manual issue entry |
| `github.issue-picker` | GitHub issue search/manual issue entry |
| `github.repository-picker` | GitHub repository selection |
| `github.branch-picker` | Branch selection after repository context is known |
| `provider.profile-picker` | Provider/runtime profile selection |
| `model-picker` | Model selection constrained by runtime policy |
| `file-reference-picker` | Uploaded file or artifact reference selection |

Unknown widgets must degrade safely. If no safe downgrade exists, the renderer
should show an unsupported-widget error near the field while preserving entered
values.

---

## Create Page Flow

When a user selects `Skill` as the Step Type:

1. The Create page lists Skills from the Skill catalog.
2. Selecting a Skill fetches or already includes the normalized input contract.
3. The step editor passes `inputSchema`, `uiSchema`, `defaults`, current draft
   values, workflow context, and deployment policy into the shared schema-form
   renderer.
4. The renderer generates visible fields for required inputs and default-visible
   optional inputs.
5. Advanced optional fields may be collapsed but remain discoverable.
6. Local validation runs on change/blur and before Start Workflow.
7. The draft stores values under `step.skill.inputs`.
8. The backend validates the values against the same Skill input contract before
   creating or starting the workflow.
9. The runtime receives validated values as part of the Skill-step execution
   request.

Desired draft shape:

```json
{
  "id": "implement-github-issue",
  "title": "Implement GitHub issue",
  "type": "skill",
  "skill": {
    "name": "github-issue-implement",
    "inputContractDigest": "sha256:...",
    "inputs": {
      "github_issue": {
        "repository": "MoonLadderStudios/MoonMind",
        "number": 123,
        "title": "Render Skill input fields from schema"
      },
      "constraints": "Keep the implementation compatible with preset inputs."
    }
  }
}
```

Legacy payloads may still contain `args` or `selectedSkillArgs`. New authoring
surfaces should write `inputs`. Readers should map legacy values into `inputs`
when loading older drafts or API payloads.

---

## Fallback UI When No Skill Schema Exists

A Skill without `inputSchema` should not look broken.

The Create page should show:

1. Skill title and description.
2. A general instructions field.
3. Context controls such as repository, branch, Jira/GitHub issue, files, or
   artifacts when provided by the workflow-level context.
4. Optional advanced runtime settings allowed by policy.
5. A non-blocking note that the Skill does not publish structured input fields.

Fallback values should be passed to the agent as normal step instructions and
context. The backend should not require structured `skill.inputs` unless the
selected Skill contract declares required fields.

---

## Defaulting Rules

Effective field values should be resolved in this order:

1. explicit user-entered draft value
2. server-provided draft value from a prior save
3. context default requested by a safe hint, such as
   `x-moonmind-context-default: repository`
4. `defaults[fieldName]`
5. JSON Schema `default`
6. empty field value

Defaults must not contain secrets. Integration widgets may enrich values after
selection, but the durable value must remain valid against the schema without
requiring enrichment fields.

Example:

```yaml
inputSchema:
  type: object
  properties:
    repository:
      type: string
      title: Repository
      x-moonmind-context-default: repository
    branch:
      type: string
      title: Branch
      x-moonmind-context-default: branch
defaults:
  branch: main
```

If workflow context already contains `branch`, context wins over the static
default. If the user edits the branch, the user value wins.

---

## Backend Validation

Skill input validation should be backend-owned and reusable.

Desired API/service boundary:

```text
validateSkillStepInputs(skill_name, content_digest, inputs, workflow_context)
  -> {
       values: normalized_inputs,
       errors: field_addressable_errors,
       warnings: diagnostics,
       contractDigest: "sha256:..."
     }
```

Validation layers:

1. Resolve the selected Skill and content evidence.
2. Load the normalized input contract tied to that content evidence.
3. Apply safe defaults.
4. Validate shape, required fields, types, enums, formats, and simple constraints.
5. Run integration-specific reference validation when a field uses a registered
   integration widget or semantic type.
6. Return field-addressable errors.
7. Preserve user-entered values after validation failure.

Example error:

```json
{
  "path": "steps[0].skill.inputs.github_issue.number",
  "message": "GitHub issue number is required.",
  "code": "required",
  "recoverable": true
}
```

`uiSchema` must never be required for validation.

---

## Runtime Handoff

The runtime should receive validated Skill inputs in a compact, explicit form.

Example agent execution payload fragment:

```json
{
  "skill": {
    "name": "github-issue-implement",
    "contentRef": "artifact:...",
    "contentDigest": "sha256:...",
    "inputContractDigest": "sha256:...",
    "inputs": {
      "github_issue": {
        "repository": "MoonLadderStudios/MoonMind",
        "number": 123
      }
    }
  }
}
```

The Skill body remains the source of behavioral instructions. The input contract
is not a second prompt language; it is the structured configuration layer that
helped collect and validate values before launch.

Runtime adapters should not re-parse `SKILL.md` to discover inputs. They should
consume the already-resolved Skill snapshot and validated step inputs.

---

## Catalog And API Design

Skill catalog responses should include the normalized contract, or a reference
that the Create page can fetch on demand.

List response item:

```json
{
  "id": "github-issue-implement",
  "kind": "skill",
  "label": "GitHub Issue Implement",
  "description": "Implement a GitHub issue and prepare a pull request.",
  "source": {
    "kind": "deployment",
    "contentDigest": "sha256:..."
  },
  "inputSchema": {
    "type": "object",
    "properties": {}
  },
  "uiSchema": {},
  "defaults": {},
  "contractDigest": "sha256:...",
  "diagnostics": []
}
```

For large schemas, the list endpoint may return:

```json
{
  "id": "github-issue-implement",
  "kind": "skill",
  "label": "GitHub Issue Implement",
  "hasInputSchema": true,
  "inputContractRef": "/api/skills/github-issue-implement/input-contract?digest=sha256:..."
}
```

The detailed endpoint should return the full contract.

---

## Persistence Strategy

### Deployment-stored Skills

When `AgentSkillsService.update_skill_content` receives Skill markdown:

1. parse frontmatter once;
2. extract required Skills and required capabilities as today;
3. extract the optional input contract;
4. store normalized input contract metadata on the Skill content artifact;
5. store diagnostics and contract digest with the artifact metadata.

Suggested artifact metadata keys:

```json
{
  "skill_slug": "github-issue-implement",
  "format": "markdown",
  "required_skills": [],
  "required_capabilities": ["git", "gh"],
  "input_schema": {},
  "ui_schema": {},
  "defaults": {},
  "input_contract_digest": "sha256:...",
  "input_schema_diagnostics": []
}
```

Schema metadata may remain content-addressed with the artifact unless denormalized
columns on `agent_skill_definitions` are later required for query performance.

### Built-in, Repo, And Local Skills

For file-backed Skills:

1. parse on catalog discovery;
2. cache by path, mtime, size, and content digest when practical;
3. expose diagnostics in admin/developer surfaces;
4. avoid mutating checked-in Skill files.

---

## Shared Code Placement

Generic capability-contract behavior belongs in shared modules rather than
preset-specific service code. Presets and Skills should consume the same
normalization, validation, widget-hint, and diagnostics contracts.

Suggested modules:

```text
moonmind/capabilities/input_contracts.py
moonmind/capabilities/schema_validation.py
moonmind/capabilities/widget_hints.py
moonmind/services/skill_input_contracts.py
```

Responsibilities:

| Module | Responsibility |
| --- | --- |
| `input_contracts.py` | Normalize `inputSchema`, `uiSchema`, `defaults`, diagnostics, and contract digest. |
| `schema_validation.py` | Validate submitted values against the supported JSON Schema subset. |
| `widget_hints.py` | Interpret safe `x-moonmind-*` semantic hints into renderer hints. |
| `skill_input_contracts.py` | Parse Skill frontmatter and adapt Skill source metadata into the shared contract. |
| Preset catalog service | Call the shared normalizer instead of owning a preset-only version. |
| Skill catalog service | Call the Skill adapter and expose the shared contract. |
| Create page renderer | Consume only the shared contract shape. |

---

## Security And Trust Boundaries

Skill input schemas are untrusted metadata.

Rules:

1. Use a safe YAML parser only.
2. Enforce maximum frontmatter, schema, and defaults sizes.
3. Reject or redact secret-like defaults.
4. Never execute schema-provided code.
5. Never fetch remote schemas or `$ref` URLs by default.
6. Only allow local, registered widgets.
7. Treat `uiSchema` as hints, not authority.
8. Ignore unknown hints unless strict policy requires rejection.
9. Sanitize markdown descriptions before rendering.
10. Keep backend validation authoritative.

Remote `$ref` support is not part of the default desired state. A future design
may allow internal, pinned schema refs after adding artifact integrity checks and
allowlist policy.

---

## Diagnostics

A capability input diagnostic should be structured enough for both UI and logs.

```ts
type CapabilityInputDiagnostic = {
  code:
    | "invalid_schema"
    | "unsupported_keyword"
    | "unsupported_widget"
    | "secret_like_default"
    | "ignored_hint"
    | "fallback_renderer";
  severity: "info" | "warning" | "error";
  path?: string;
  message: string;
  recoverable: boolean;
};
```

The Create page should show only actionable, user-safe messages. Admin/developer
surfaces may show full diagnostics.

---

## Observability

Add counters and traces for:

- Skill input schema parse success/failure
- Skill input schema omitted
- generated field count by widget
- fallback renderer usage
- unsupported widget references
- backend validation failures by code
- schema contract digest mismatch on draft submit
- strict-policy save rejection

These metrics should be grouped by Skill source kind and anonymized where
necessary. Do not log raw user input values.

---

## Versioning And Draft Staleness

A draft Skill step may be saved with `inputContractDigest`.

On draft reload or submit:

1. Re-fetch the selected Skill's current contract.
2. Compare the saved digest with the current digest.
3. If unchanged, validate normally.
4. If changed, preserve entered values, re-run validation, and show a notice that
   the Skill input contract changed.
5. If the Skill content is pinned by a resolved workflow snapshot, validate
   against the pinned contract instead of the latest catalog version.

This mirrors the content-evidence model used by Skill resolution while giving
the Create page a practical stale-draft UX.

---

## Target-State Implementation Constraints

The canonical design does not prescribe build sequencing. Any implementation of
Skill input schema UI generation should satisfy these target-state constraints:

- Generic input contract normalization belongs in a shared capability layer that
  both Presets and Skills consume.
- Preset catalog responses must remain API-compatible while moving to the shared
  contract behavior.
- Skill frontmatter parsing should act as a source adapter that emits the shared
  `inputSchema`, `uiSchema`, and `defaults` contract.
- Deployment-stored Skills should persist extracted contract metadata with the
  content artifact and content evidence.
- Skill catalog details should expose the same normalized contract shape as
  preset catalog details, including empty schemas for Skills without structured
  inputs.
- Skill steps should render through the same Create page schema-form renderer as
  preset steps and persist collected values under `step.skill.inputs`.
- Backend Skill input validation should be shared, authoritative, and
  field-addressable under `steps[n].skill.inputs`.
- Legacy Skill-step payload readers should accept older argument fields and
  normalize them to `inputs`; updated authoring surfaces should emit `inputs`.
- Conformance coverage should prove equivalent field generation for equivalent
  Skill and Preset schemas and should prove schema-less `SKILL.md` files remain
  selectable.

---

## Acceptance Criteria

- A Skill with no `inputSchema` remains importable, selectable, and executable
  through instruction-driven fallback behavior.
- A Skill with frontmatter `inputSchema` produces generated fields on the Create
  page using the same schema-form renderer as presets.
- The Skill catalog exposes `inputSchema`, `uiSchema`, and `defaults` in the same
  normalized contract shape as presets.
- Skill-generated fields support at least text, textarea/markdown, number,
  boolean, select, multi-select, URI/date/date-time, object JSON fallback, and
  registered issue/repository/branch widgets.
- Backend validation uses `inputSchema`, not `uiSchema`.
- Validation errors are field-addressable under `steps[n].skill.inputs`.
- Skill-specific Create page branches are not required for new structured Skill
  inputs.
- Secret-like defaults are rejected or redacted before reaching the UI.
- Unknown widgets or unsupported schema shapes degrade safely without losing user
  input.
- Drafts preserve entered values when a Skill input contract changes and
  revalidate against the current or pinned contract.
- Preset input rendering continues to use the same normalized contract and
  remains API-compatible.
