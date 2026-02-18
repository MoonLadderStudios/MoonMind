# Feature Specification: Manifest Task System Documentation

**Feature Branch**: `024-manifest-task-system`  
**Created**: 2026-02-18  
**Status**: Draft  
**Input**: User description: "Create docs/ManifestTaskSystem.md describing how MoonMind ingests manifest-defined pipelines via the Agent Queue and outline Phase 1 delivery."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Queue-Aligned Manifest Runs (Priority: P1)

MoonMind platform engineers need a single source of truth that explains how manifest-defined ingestion pipelines are submitted, monitored, cancelled, and audited via the Agent Queue so they can build and operate the feature consistently.

**Why this priority**: Without clear documentation, the manifest ingestion feature cannot be implemented or reviewed against existing queue constraints.

**Independent Test**: Engineers can follow the doc to register a manifest job type, derive capabilities, and reason about queue visibility without requesting additional clarification.

**Acceptance Scenarios**:

1. **Given** an engineer reading the doc, **When** they reference the queue job type section, **Then** they learn how to submit a `type="manifest"` job with capability derivation and payload rules.
2. **Given** a need to cancel a manifest run, **When** the engineer follows the worker lifecycle guidance, **Then** they can describe how cancellations propagate through events and artifacts without missing steps.

---

### User Story 2 - Worker Implementation Guide (Priority: P2)

Engineers implementing the `moonmind-manifest-worker` require an explicit stage plan, artifact contract, and configuration checklist to build the worker without reverse-engineering older task workers.

**Why this priority**: The worker is the core runtime for manifest ingestion; missing guidance risks security regressions or incompatible ingestion behavior.

**Independent Test**: Using only the document, a developer can enumerate worker env vars, stage events, required artifacts, and cancellation semantics.

**Acceptance Scenarios**:

1. **Given** a developer referencing Section 9, **When** they configure the worker, **Then** they can list all mandatory env vars and capability expectations.
2. **Given** the stage plan, **When** they emit events for each pipeline step, **Then** they align with the documented event names and payload hygiene rules.

---

### User Story 3 - Tasks Dashboard and Security Visibility (Priority: P3)

Task Dashboard maintainers and security reviewers need clarity on the new manifest category, submit form, and payload redaction rules so UI and guardrails land alongside the backend changes.

**Why this priority**: Manifest runs must surface as a first-class category with safe payload handling to maintain operator trust and compliance.

**Independent Test**: The UI and security sections let a reviewer describe the new category, submit flow, and no-secrets enforcement without referencing other docs.

**Acceptance Scenarios**:

1. **Given** Section 10, **When** a designer reads it, **Then** they can outline the new Tasks Dashboard category, submit form inputs, and detail view expectations.
2. **Given** Section 11, **When** a security reviewer inspects the doc, **Then** they can enumerate allowed secret reference patterns and logging redaction policies.

---

### Edge Cases

- What happens when a manifest references a capability (e.g., Confluence) that no worker advertises? Document that server-side capability derivation prevents the job from being claimed and signals validation errors.
- How does the system handle manifests missing `vectorStore` metadata or embedding dimension mismatches? The execution engine section must describe validation failures before upsert.
- What if cancellation is requested during a long-running transform? The worker stage plan should specify safe interruption points and artifact outcomes.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The document MUST define the new Agent Queue job type `type="manifest"`, including capability gating rules and the canonical payload schema (ManifestJobPayload).
- **FR-002**: The document MUST describe all supported manifest source kinds (`inline`, `path`, `registry`, `repo`) and state that Phase 1 supports inline + path while recommending registry for later phases.
- **FR-003**: The document MUST capture manifest actions (`plan`, `run`, `evaluate`) with explicit behaviors for Phase 1 (plan/run) and call out evaluation as future scope.
- **FR-004**: The document MUST outline the v0 manifest execution engine structure (models, validator, interpolate, adapters, transforms, embeddings, vector store expectations) and tie it back to existing components.
- **FR-005**: The document MUST define the manifest worker (service name, env vars, capabilities, stage events, artifacts, cancellation flow) so implementation teams can build it.
- **FR-006**: The document MUST detail Tasks Dashboard updates (new category, submission flow, detail view) to ensure UI teams can expose manifest runs distinctly.
- **FR-007**: The document MUST state the security model: token-free payloads, secret resolution paths (env + Vault), and logging/artifact redaction rules.
- **FR-008**: The document MUST provide a phased delivery plan highlighting Phase 1 (job type + worker + UI), Phase 2 (registry + secret refs), and Phase 3 (adapters/evals) to guide incremental rollout.

### Key Entities *(include if feature involves data)*

- **Manifest (v0)**: YAML contract describing sources, transforms, embeddings, and vector store targets for ingestion; references readers like GitHub or Google Drive.
- **Manifest Run**: A single queue job execution with `type="manifest"` that carries derived capabilities, emits events, and uploads artifacts for auditing.
- **Manifest Worker**: Dedicated daemon advertising `manifest` capability, responsible for claim → validate → fetch/transform/embed/upsert and artifact handling.
- **Manifest Registry**: Postgres-backed store (existing `manifest` table) that tracks YAML, version metadata, and links queue job IDs to manifest definitions.
- **Tasks Dashboard Category**: UI grouping for manifest jobs so operators can submit and monitor ingestion runs separately from codex/gemini/claude tasks.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The repository hosts `docs/ManifestTaskSystem.md` containing all sections enumerated in the requirements and referencing related docs (WorkerVectorEmbedding, etc.).
- **SC-002**: Platform engineers can reference the document to describe Phase 1 deliverables (job type, worker, UI) without additional meetings, as verified by internal review sign-off.
- **SC-003**: Task Dashboard designers can build a manifest submit form prototype using only the documented requirements, confirmed via checklist completion.
- **SC-004**: Security reviewers can trace how secrets remain outside queue payloads and how redaction is enforced, evidenced by the presence of explicit rules in Section 11.
