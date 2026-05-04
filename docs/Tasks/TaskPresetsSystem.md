# Task Presets System

**Implementation tracking:** Rollout and backlog notes live in MoonSpec artifacts (`specs/<feature>/`), gitignored handoffs (for example `artifacts/`), or other local-only files—not as migration checklists in canonical `docs/`.

Status: Active
Owners: MoonMind Engineering (Task Platform + UI)
Last Updated: 2026-03-13

## 1. Purpose

Define the MoonMind **Task Presets** system: a server-hosted catalog of reusable orchestration presets that users browse, parameterize, and apply to tasks. Presets compile into `PlanDefinition` artifacts (see `docs/Tasks/SkillAndPlanContracts.md`) for execution by the Temporal Plan Executor.

The system serves three roles:

1. **Discovery** — a searchable catalog of ready-made orchestrations with scopes, favorites, and recency tracking.
2. **Authoring** — parameterized blueprints (Jinja2 inputs) that produce deterministic, validated plans without manual JSON editing.
3. **Governance** — versioned, RBAC-scoped entries with audit trails, secret scrubbing, and release lifecycle management.

### 1.1 Relationship to Plans

A **Preset** is what a user *chooses*. A **Plan** is what *executes*.

Presets are the authoring and discovery surface; Plans are the runtime execution contract. The expansion service compiles a preset into a `PlanDefinition` DAG, which is then stored as an immutable artifact and submitted to the `MoonMind.Run` Temporal workflow.

```
Preset (catalog entry)
 ├── inputs_schema (parameterization)
 ├── step blueprints (Jinja2 templates)
 └── metadata (scope, tags, capabilities)
 │
 │ expand(inputs) — server-side
 v
PlanDefinition (immutable artifact)
 ├── nodes[] (concrete Step invocations)
 ├── edges[] (dependency DAG)
 ├── policy (failure_mode, max_concurrency)
 └── metadata.registry_snapshot (pinned skill versions)
 │
 │ submit to Temporal
 v
Plan Executor (MoonMind.Run workflow)
 └── schedules Activities, tracks progress, enforces policy
```

### 1.2 Terminology

| Term | Definition |
|------|-----------|
| **Preset** | A versioned, parameterized blueprint in the catalog. Users browse and select presets. |
| **Plan** | A validated DAG of tool/skill invocations. The runtime execution artifact. See `SkillAndPlanContracts.md` §6. |
| **Step** | A single node in a Plan that invokes one tool (skill subtype). See `Step` dataclass. |
| **Tool / Skill** | An executable capability with input/output schemas, policies, and activity bindings. See `ToolDefinition`. |
| **Expansion** | The server-side compilation of a preset + user inputs into a `PlanDefinition`. |
| **Preset Include** | A compositional preset-version entry that references another preset by slug and pinned version. |
| **Expansion Tree** | The recursive include graph resolved during expansion, including aliases and include paths. |
| **Flattened Plan** | The ordered concrete step list produced after all includes are resolved. This is the execution-facing shape. |
| **Preset Provenance** | Compact metadata attached to flattened steps identifying the root preset, source preset, pinned version, alias, and include path. |
| **Detachment** | Save-as-preset behavior where customized, partial, or provenance-mismatched steps are serialized as concrete steps instead of preserving include semantics. |

---

## 2. Goals and Non-Goals

### Goals

- Provide a single authoritative preset catalog with versioning, ownership, and scopes (global / personal).
- Offer deterministic server-side expansion that produces validated `PlanDefinition` artifacts with pinned registry snapshots.
- Deliver UI conveniences (preview, append/replace, collapse-as-group, favorites) without changing the Plan execution contract.
- Support CLI/MCP flows via REST endpoints identical to the UI.
- Maintain full audit trails linking every task execution back to the preset, version, and inputs that produced it.

### Non-Goals

- Allowing parameter substitutions during Temporal runtime (remains an anti-pattern; all parameterization happens at expansion time).
- Replacing the Plan Executor or modifying Temporal Workflow execution behavior.
- Supporting conditional logic within presets (conditions belong in Plans via future `edges[].condition` support; see `SkillAndPlanContracts.md` §Q2).

---

## 3. System Overview

```
 +---------------------+
 | Preset Catalog DB |
 +----------+----------+
 ^
 | CRUD + version seed
+-------------+ REST | +----------------+
| Task UI / +-------->+ Preset Catalog API <-+ MCP / CLI / CI |
| Automations | | +----------------+
+------+------+ v
 | +-----+------------------+
 | expand | Preset Expansion Svc |
 +--------->+ (validate + hydrate |
 | + compile to Plan) |
 +-----+------------------+
 |
 | PlanDefinition artifact
 v
 +-----+------------------+
 | Plan Submission |
 | (store artifact + |
 | start workflow) |
 +-----+------------------+
 v
 +-----+------------------+
 | Plan Executor |
 | (MoonMind.Run workflow) |
 +------------------------+
```

Key properties:

- Presets are stored centrally and exposed via FastAPI routers under `/api/task-step-templates` (to be renamed `/api/presets` in a future migration).
- The expansion service applies inputs via Jinja2 rendering, validates against skill schemas, generates deterministic step IDs, resolves the current registry snapshot, and compiles the result into a `PlanDefinition`.
- The `PlanDefinition` is written as an immutable artifact. The Plan Executor in `MoonMind.Run` reads the artifact reference and executes the DAG.
- Audit metadata (`appliedPreset`) is attached to the task record for governance and traceability.

---

## 4. Preset Model

### 4.1 Catalog Entry (database)

A preset is a `TaskStepTemplate` row with versioned releases:

| Field | Type | Description |
|-------|------|-------------|
| `slug` | `String(128)` | Unique identifier within scope. URL-safe, lowercase. |
| `scope_type` | `Enum(GLOBAL, PERSONAL)` | Visibility scope. |
| `scope_ref` | `String(64)` | Owner reference (user_id for PERSONAL). Null for GLOBAL. |
| `title` | `String(255)` | Human-readable display name. |
| `description` | `Text` | Long-form description shown in catalog. |
| `tags` | `JSON[List[str]]` | Searchable tags for filtering. |
| `required_capabilities` | `JSON[List[str]]` | Worker capabilities needed to execute. |
| `latest_version_id` | `UUID FK` | Points to the current active release. |
| `is_active` | `Boolean` | Soft-delete flag. |
| `created_by` | `UUID` | Creator user ID. |

Unique constraint: `(slug, scope_type, scope_ref)`.

### 4.2 Version (immutable release)

Each version is a `TaskStepTemplateVersion` row:

| Field | Type | Description |
|-------|------|-------------|
| `version` | `String(32)` | Semantic version label (e.g. `1.0.0`). |
| `inputs_schema` | `JSON[List[Dict]]` | Input definitions for parameterization. |
| `steps` | `JSON[List[Dict]]` | Step blueprints (Jinja2 templates). |
| `annotations` | `JSON[Dict]` | Metadata (e.g. `sourceSkill`, `profile`). |
| `required_capabilities` | `JSON[List[str]]` | Version-specific capability overrides. |
| `max_step_count` | `Integer` | Safety limit on expanded steps (default 25). |
| `release_status` | `Enum(DRAFT, ACTIVE, INACTIVE)` | Lifecycle state. |
| `seed_source` | `String(255)` | Origin YAML file path for seeded presets. |

Unique constraint: `(template_id, version)`.

### 4.3 Input definitions

Each entry in `inputs_schema` declares a parameterizable field:

```yaml
- name: feature_request
 label: Feature Request
 type: markdown # text | textarea | markdown | enum | boolean | user | team | repo_path
 required: true
 default: null
 options: [] # populated for enum type
```

### 4.4 Step blueprints

Each entry in `steps` is a Jinja2 template that expands into a Plan node. Entries
without an explicit `kind` are treated as `kind: step` for compatibility:

```yaml
- title: Invoke moonspec-specify
 kind: step
 instructions: |-
 Run moonspec-specify with the canonical feature request:
 {{ inputs.feature_request }}

 MoonSpec Orchestrate always runs as a runtime implementation workflow.
 skill:
 id: moonspec-specify
 args: {}
 requiredCapabilities: [codex, git]
 annotations:
 phase: specification
```

**Allowed keys**: `kind`, `instructions`, `title`, `slug`, `skill`, `annotations`.

**Forbidden keys** (prevent runtime override via presets): `runtime`, `targetRuntime`, `target_runtime`, `model`, `effort`, `repository`, `repo`, `git`, `publish`, `container`.

### 4.5 Preset includes

Preset versions MAY include other preset versions as compile-time composition
entries:

```yaml
- kind: include
 slug: shared-quality-checks
 version: 1.0.0
 alias: quality
 scope: global
 inputMapping:
 feature_request: "{{ inputs.feature_request }}"
```

Rules:

- `slug`, pinned `version`, and `alias` are required.
- `inputMapping` supplies the child preset inputs after the parent entry is rendered.
- Repeated child includes in one parent version MUST use distinct aliases.
- Child step overrides are not supported in v1; a child preset expands from its own pinned version and mapped inputs only.
- `scope` defaults to the parent preset scope when omitted. Personal presets MAY include global presets, but GLOBAL presets MUST NOT include PERSONAL presets.
- Missing, unreadable, inactive, cyclic, or input-incompatible includes are rejected before executable steps are returned.

Composition is control-plane behavior only. Includes are fully resolved before a
`PlanDefinition` artifact is stored or submitted; the executor does not evaluate
nested preset semantics.

### 4.6 YAML seed format

Global presets can be seeded from YAML files in `api_service/data/task_step_templates/`:

```yaml
slug: moonspec-orchestrate
title: Workflow Orchestrate
description: Run the full workflow pipeline...
scope: global
version: 1.0.0
tags: [moonspec, orchestration]
requiredCapabilities: [git]
annotations:
 sourceSkill: moonspec-orchestrate
inputs:
 - name: feature_request
 label: Feature Request
 type: markdown
 required: true
steps:
 - title: Step 1
 instructions: "{{ inputs.feature_request }}"
 skill:
 id: auto
 args: {}
```

---

## 5. Expansion Pipeline (Preset → PlanDefinition)

### 5.1 Overview

Expansion is a server-side, deterministic compilation that transforms a preset + user inputs into a `PlanDefinition` ready for Temporal submission. The process is stateless and idempotent — the same preset version + inputs always produce the same Plan.

### 5.2 Expansion steps

```
1. Resolve preset version
 └── Look up (slug, scope, version) in catalog DB
 └── Verify release_status is ACTIVE (or DRAFT for preview)

2. Validate and resolve inputs
 └── Check required fields present
 └── Validate types and enum constraints
 └── Apply defaults for optional inputs

3. Build Jinja2 variable context
 └── { inputs: {...}, context: {...}, now: ISO-timestamp, iso_today: YYYY-MM-DD }

4. Render step blueprints and includes
 └── Apply SandboxedEnvironment to each step's instructions/title
 └── Reject any unresolved {{ ... }} placeholders
 └── Reject any forbidden keys in rendered output
 └── For `kind: include`, render `inputMapping` and resolve the child preset
 version by slug, scope, and pinned version

5. Resolve composition
 └── Recursively resolve include entries into an expansion tree
 └── Reject cycles with a path such as parent@1.0.0 → child:shared@1.0.0
 └── Reject GLOBAL → PERSONAL includes
 └── Reject missing, unreadable, inactive, or child-input-incompatible includes
 └── Enforce `max_step_count` after flattening

6. Generate deterministic step IDs
 └── Format: tpl:{slug}:{version}:{index:02d}:{input_hash}
 └── input_hash = sha256(canonical JSON of inputs)[:8]
 └── `index` is the flattened step index from the root preset expansion

7. Attach provenance
 └── Each flattened step receives `presetProvenance`
 └── Provenance includes root slug/version, source slug/version/scope,
 source step index, include alias, and include path

8. Resolve registry snapshot ← NEW
 └── Load current skill registry
 └── Compute snapshot digest
 └── Store snapshot as artifact, capture ArtifactRef

9. Map steps to Plan nodes ← NEW
 └── For each rendered step:
 │ ├── Resolve skill.id → ToolDefinition(name, version) from registry
 │ ├── Validate step inputs against ToolDefinition.input_schema
 │ └── Create Step(id, skill_name, skill_version, inputs)
 └── Infer edges from sequential ordering (linear chain)
 └── Future: support explicit dependency annotations in blueprints

10. Assemble PlanDefinition ← NEW
 └── plan_version: "1.0"
 └── metadata: { title, created_at, registry_snapshot }
 └── policy: { failure_mode: from preset annotations or default FAIL_FAST,
 max_concurrency: from preset annotations or default 1 }
 └── nodes: [Step, ...]
 └── edges: [PlanEdge, ...] (linear chain by default)

11. Store Plan artifact ← NEW
 └── Write PlanDefinition JSON as immutable artifact
 └── Return ArtifactRef for workflow submission

12. Record audit metadata
 └── Write appliedPreset { slug, version, inputs, planArtifactRef, appliedAt }
 └── Update recents table (top 5 per user)
```

### 5.3 Dependency inference

In v1, preset steps compile to a **linear chain** — each step depends on the previous:

```
n1 → n2 → n3 → n4 → ...
```

This matches the existing sequential step model. Future versions will support:

- **Explicit edges** via `dependsOn` annotations in step blueprints.
- **Parallel groups** via `group` annotations that share a common predecessor and successor.
- **Fan-out / fan-in** patterns for steps that can run concurrently.

### 5.4 Registry snapshot pinning

Every expanded Plan pins a `registry_snapshot`:

```json
{
 "digest": "reg:sha256:abc123...",
 "artifact_ref": "art:sha256:def456..."
}
```

This ensures the Plan can be re-executed against the exact same skill definitions, regardless of future registry changes. The snapshot is computed at expansion time from the current deployed registry.

### 5.5 Policy defaults

Presets can declare execution policy via `annotations`:

```yaml
annotations:
 planPolicy:
 failure_mode: CONTINUE # default: FAIL_FAST
 max_concurrency: 4 # default: 1
```

If omitted, the expansion service applies defaults (`FAIL_FAST`, concurrency 1).

### 5.6 Skill resolution

The `skill.id` field in step blueprints maps to registered `ToolDefinition` entries:

| Blueprint `skill.id` | Resolution |
|----------------------|------------|
| `auto` | Inferred from step context (e.g. instructions analysis). Falls back to a default general-purpose skill. |
| `moonspec-specify` | Exact match to `ToolDefinition.name` in registry. Uses latest version in snapshot. |
| `repo.apply_patch@2.1.0` | Pinned to specific version. |

Resolution failures (skill not found, version mismatch) produce expansion errors, not runtime failures.

### 5.7 Composition output

Expansion returns both:

- `steps[]`: the flattened execution-facing step list.
- `composition`: the expansion tree used for preview and audit.

Flattened steps include `presetProvenance` so downstream audit, preview, and
save-as-preset flows can understand the source of each concrete step without
re-resolving the include graph.

---

## 6. API Contract

### 6.1 Endpoints

Base path: `/api/task-step-templates`

| Method | Path | Description |
|--------|------|-------------|
| `GET /` | List presets | Filterable by scope, tags, favorites, recency. |
| `POST /` | Create preset | New catalog entry with initial version. |
| `POST /save-from-task` | Save from steps | Convert executed steps into a personal preset. |
| `POST /{slug}:expand` | Expand preset | Compile to `PlanDefinition` with given inputs. Returns Plan artifact ref. |
| `GET /{slug}` | Get latest version | Fetch preset details with latest active release. |
| `GET /{slug}/versions/{version}` | Get specific version | Fetch a pinned version. |
| `PUT /{slug}/versions/{version}` | Review version | Approve/reject for release (admin only for GLOBAL). |
| `POST /{slug}:favorite` | Add favorite | Mark for quick access. |
| `DELETE /{slug}:favorite` | Remove favorite | Unmark. |
| `DELETE /{slug}` | Soft delete | Deactivate preset (preserves history). |

### 6.2 Expand request

```json
POST /api/task-step-templates/{slug}:expand

{
 "version": "1.0.0",
 "inputs": {
 "feature_request": "Add caching to the API layer"
 },
 "context": {},
 "options": {
 "enforceStepLimit": true,
 "preview": false
 }
}
```

### 6.3 Expand response (updated for Plan output)

```json
{
 "plan": {
 "plan_version": "1.0",
 "metadata": {
 "title": "moonspec-orchestrate v1.0.0",
 "created_at": "2026-03-13T12:00:00Z",
 "registry_snapshot": {
 "digest": "reg:sha256:abc123...",
 "artifact_ref": "art:sha256:def456..."
 }
 },
 "policy": {
 "failure_mode": "FAIL_FAST",
 "max_concurrency": 1
 },
 "nodes": [
 {
 "id": "tpl:moonspec-orchestrate:1.0.0:01:a1b2c3d4",
 "tool": { "type": "skill", "name": "moonspec-specify", "version": "1.2.0" },
 "inputs": { "feature_request": "Add caching to the API layer" }
 }
 ],
 "edges": []
 },
 "planArtifactRef": "art:sha256:789abc...",
 "appliedPreset": {
 "slug": "moonspec-orchestrate",
 "version": "1.0.0",
 "inputs": { "feature_request": "Add caching..." },
 "nodeIds": ["tpl:moonspec-orchestrate:1.0.0:01:a1b2c3d4"],
 "appliedAt": "2026-03-13T12:00:00Z"
 },
 "capabilities": ["git", "codex"],
 "warnings": []
}
```

When `options.preview` is true, the Plan is returned but not stored as an artifact — the caller can inspect it before committing.

### 6.4 Save-from-task request

```json
POST /api/task-step-templates/save-from-task

{
 "scope": "personal",
 "scopeRef": "user-uuid",
 "title": "My custom pipeline",
 "description": "Steps I use for feature work",
 "steps": [ ... ],
 "suggestedInputs": [ ... ],
 "tags": ["custom", "feature-work"]
}
```

The save service sanitizes steps (strips forbidden keys), scans for secrets (GitHub tokens, AWS keys, PEM blocks, generic patterns), and creates a DRAFT version.

---

## 7. RBAC and Scoping

### 7.1 Scope rules

| Scope | Visibility | Create | Activate | Delete |
|-------|-----------|--------|----------|--------|
| `PERSONAL` | Owner only | Any user | Auto (DRAFT default) | Owner |
| `GLOBAL` | All users | Admin | Admin (requires review) | Admin |

### 7.2 Access enforcement

- The API resolves the caller's scope permissions before any read or write operation.
- Expansion of INACTIVE presets emits a warning but is allowed with explicit confirmation (audit logged).
- Personal presets are invisible to other users in listings and direct fetch.

---

## 8. UX Affordances

### 8.1 Catalog browsing

- Filter by scope, tags, required capabilities.
- Sort by recency, popularity, or alphabetical.
- Favorites pinned to top of listings.

### 8.2 Preview and apply

- **Preview**: expand with `options.preview: true` to see the resulting Plan nodes without submitting.
- **Append**: merge expanded nodes into existing draft plan (extends the DAG).
- **Replace**: discard existing draft nodes and replace with expanded preset.
- **Collapse-as-group**: UI renders preset-derived nodes as a collapsible group with the preset title.

### 8.3 Save-as-preset

- Select steps from an executed task.
- Scrub detected secrets (highlighted in UI).
- Parameterize repeated values as input placeholders.
- Choose scope (personal; global requires admin promotion).
- Preserve an include only when the selected steps exactly match an intact
 provenance subtree from one include expansion and the source preset/version is
 still readable.
- Serialize detached, partial, reordered, or customized selections as concrete
 `kind: step` entries so saved presets never silently retain stale nested
 semantics.

---

## 9. Observability and Governance

### 9.1 Audit trail

Every preset expansion records:

- `appliedPreset.slug` — which preset was used.
- `appliedPreset.version` — which version was expanded.
- `appliedPreset.inputs` — what inputs the user provided.
- `appliedPreset.planArtifactRef` — reference to the produced Plan artifact.
- `appliedPreset.appliedAt` — when expansion occurred.

This metadata is stored on the task record and is queryable for compliance review.

### 9.2 Telemetry

StatsD counters emitted under `moonmind.task_templates.*`:

- `expand.count` — total expansions (tagged by slug, scope).
- `expand.error` — expansion failures (tagged by error type).
- `save.count` — presets saved from task steps.
- `favorite.count` — favorite toggles.

### 9.3 Lifecycle management

- Versions are immutable once created. Edits produce new versions.
- `INACTIVE` versions remain in the catalog for audit but emit warnings on expansion.
- Soft-deleted presets (`is_active: false`) are excluded from listings but preserved in DB.

---

## 10. Plan-based expansion (target)

**Steady-state:** preset expansion produces a **`PlanDefinition`** (and `planArtifactRef`) consumed by `MoonMind.Run`; legacy `steps[]` / `appliedStepTemplates` paths may exist only during transition. **Target API** surface is `/api/presets` with `Preset` / `PresetVersion` models; expanders and compilers align on `appliedPreset` + artifact refs. Interim dual-output and rename steps are tracked in MoonSpec feature artifacts or local planning notes when needed.

---

## 11. Open Design Decisions

### Q1: How should presets express non-linear dependencies?

**Proposed**: Add optional `dependsOn` field to step blueprints:

```yaml
steps:
 - slug: run-tests
 title: Run tests
 instructions: "..."
 skill: { id: repo.run_tests }

 - slug: run-lint
 title: Run linter
 instructions: "..."
 skill: { id: repo.lint }

 - slug: merge-results
 title: Merge results
 instructions: "..."
 skill: { id: plan.merge }
 dependsOn: [run-tests, run-lint] # ← parallel predecessors
```

The expansion service would translate `dependsOn` into `PlanEdge` entries. Steps without `dependsOn` default to depending on the previous step (linear chain). This is a Phase 2+ feature.

### Q2: Should presets support `planPolicy` overrides per-expansion?

**Proposed**: Allow callers to override policy in the expand request:

```json
{
 "inputs": { ... },
 "policyOverrides": {
 "failure_mode": "CONTINUE",
 "max_concurrency": 4
 }
}
```

Overrides are bounded by server-enforced limits (e.g. max_concurrency capped at 16).

### Q3: How do presets reference skills that require specific versions?

**Current**: `skill.id` resolves to the latest version in the registry snapshot.

**Proposed**: Support explicit version pinning in blueprints via `skill.id: "repo.apply_patch@2.1.0"`. Unpinned skills resolve to latest-in-snapshot. This preserves the registry snapshot's reproducibility guarantee while allowing presets to be more or less specific.

---

## 12. Related Documents

- `docs/Tasks/SkillAndPlanContracts.md` — Plan schema, execution semantics, validation rules.
- `docs/Tasks/SkillAndPlanEvolution.md` — Design rationale and terminology decisions.
- `specs/028-task-presets/spec.md` — Original feature specification with user stories and acceptance criteria.
- `moonmind/workflows/skills/tool_plan_contracts.py` — `PlanDefinition`, `Step`, `PlanEdge` dataclasses.
- `moonmind/workflows/skills/plan_validation.py` — DAG validation logic.
- `moonmind/workflows/skills/plan_executor.py` — Deterministic plan execution in Temporal.
- `api_service/services/task_templates/catalog.py` — Expansion service implementation.
- `api_service/services/task_templates/save.py` — Save-from-task service.
- `api_service/api/routers/task_step_templates.py` — REST API router.
- `api_service/db/models.py` — Database models (`TaskStepTemplate*`).
