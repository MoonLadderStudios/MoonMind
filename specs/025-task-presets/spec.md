# Feature Specification: Task Presets Catalog

**Feature Branch**: `025-task-presets`
**Created**: 2026-02-18
**Status**: Draft
**Input**: User description: "Create docs/TaskPresetsSystem.md which expands option 2 into a full technical design and implement the server-hosted task preset template system with UI conveniences and save-as-template flows."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Apply catalog template to a new task (Priority: P1)

Staff engineers drafting a new task open the Task Queue UI, browse the shared template catalog, preview a preset, provide required inputs, and insert the expanded steps directly into the step editor without touching raw JSON.

**Why this priority**: Reusable orchestrations significantly reduce drafting time and drive consistency; without this flow the catalog is unused.

**Independent Test**: From a clean browser session, a user can browse templates, preview steps, apply inputs, choose append vs replace, and see expanded steps rendered with deterministic IDs in the editor.

**Acceptance Scenarios**:

1. **Given** a catalog template that requires inputs, **When** the user submits valid input values, **Then** the UI displays a preview and inserts concrete steps with deterministic IDs into `task.steps[]`.
2. **Given** existing draft steps, **When** the user chooses "Replace all steps", **Then** the editor removes previous steps, inserts the expanded preset, and records the applied template metadata.

---

### User Story 2 - Save curated steps as a reusable template (Priority: P1)

Power users select a subset of task steps they just executed successfully, scrub secrets, parameterize repeated phrases, and save them as a personal or team template for later reuse.

**Why this priority**: The catalog stays relevant only if teams can capture real workflows rapidly without leaving the task UI.

**Independent Test**: Select steps, invoke "Save as template", configure metadata, inputs, and scope, submit without validation errors, and see the template appear under the expected scope.

**Acceptance Scenarios**:

1. **Given** selected steps that contain sensitive tokens, **When** the user invokes "Save as template", **Then** the UI highlights detected secrets and the server rejects unredacted values.
2. **Given** non-contiguous step selections, **When** the user saves them as a template, **Then** the resulting blueprint preserves relative order and input placeholders.

---

### User Story 3 - Track template usage and enforce RBAC (Priority: P2)

Team leads administer the template catalog, ensuring that team-only presets stay private, global presets undergo review, and audit logs capture who applies each version.

**Why this priority**: Governance prevents accidental sharing of sensitive automation and supports compliance reviews.

**Independent Test**: Apply templates under different scopes with distinct users and verify RBAC enforcement plus audit records.

**Acceptance Scenarios**:

1. **Given** a personal template, **When** another user attempts to load it, **Then** the API denies access and the UI hides it from listings.
2. **Given** a global template marked inactive, **When** a user attempts to apply it, **Then** the UI surfaces a warning and expansion requires explicit confirmation while audit logs capture the event.

---

### User Story 4 - CLI/MCP users expand templates via API (Priority: P3)

Automation scripts and CLI tooling fetch the catalog and invoke the expand endpoint, receiving concrete steps to merge into JSON payloads without duplicating business logic.

**Why this priority**: Ensures parity for non-UI workflows and avoids manual drift.

**Independent Test**: Using service tokens, call `GET /api/task-step-templates` and `POST /api/task-step-templates/{slug}:expand`, receiving validated steps ready for queue submission.

**Acceptance Scenarios**:

1. **Given** a valid API token, **When** the CLI calls the list endpoint with tag filters, **Then** the response only includes templates matching search criteria and scope permissions.
2. **Given** an automation request referencing disallowed step keys, **When** the server validates the template expansion, **Then** it returns a schema error and no task is enqueued.

### Edge Cases

- What happens when two users edit the same template concurrently? The API must enforce version immutability and reject edits to released versions.
- How does the system handle missing input values or invalid enum submissions? Expansion should fail with actionable validation errors before altering the task.
- How are templates referencing deprecated skills treated? Expansion must surface capability errors and block enqueueing until dependencies are resolved.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST host a catalog of task step templates with slug, title, description, inputs schema, step blueprints, scope metadata, and version history.
- **FR-002**: System MUST expose REST endpoints to list templates (filterable by scope, tags, favorites, recency) and fetch individual versions.
- **FR-003**: System MUST provide a server-side expand endpoint returning concrete `steps[]`, deterministic IDs, derived capabilities, and audit metadata after applying user inputs.
- **FR-004**: System MUST validate expanded steps against the existing task contract, rejecting forbidden keys, empty instructions, or output beyond configured limits.
- **FR-005**: System MUST persist audit data on each applied template (slug, version, inputs) inside the task record for later review.
- **FR-006**: System MUST provide UI affordances to browse, preview, append/replace, group, and diff template steps without modifying the worker payload schema.
- **FR-007**: System MUST allow authorized users to convert selected draft steps into new templates, including secret scrubbing, input placeholder detection, scope selection, and optional sharing workflows.
- **FR-008**: System MUST enforce RBAC so that personal templates remain private, team templates require membership, and global templates require admin approval before activation.
- **FR-009**: System MUST support CLI/MCP parity so automations can discover, expand, and apply templates via the same API contracts as the UI, including error semantics.
- **FR-010**: System MUST surface telemetry and governance artifacts (usage counters, review status, soft-delete) to support lifecycle management and compliance.

### Key Entities

- **TaskStepTemplate**: Canonical catalog entry containing slug, title, description, scope, tags, required capabilities, and latest version pointer.
- **TemplateVersion**: Immutable record of a template release with version string, inputs schema, step blueprints, activation flag, reviewer metadata, and seed provenance.
- **TemplateInputDefinition**: Field-level schema describing name, label, type (text/enum/boolean/etc.), validation rules, and default values consumed during expansion.
- **TemplateApplication**: Audit object captured per task storing template slug, version, user inputs, applied timestamps, and derived step IDs.
- **TemplateScope**: Authorization context referencing owner (user/team/global) plus RBAC policies controlling visibility and edit rights.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: At least 70% of new runtime tasks created by internal users within four weeks of launch include `appliedStepTemplates` metadata referencing a catalog preset.
- **SC-002**: 90% of template expansions complete under 300 ms at P95, including validation and deterministic ID generation, when executed inside the API service.
- **SC-003**: Secret-scrubbing heuristics reduce reported incidents of leaked tokens in saved templates to zero in the first release window.
- **SC-004**: Governance telemetry shows <5% of template applications fail due to RBAC or validation errors after the first month, indicating accurate scopes and user education.
