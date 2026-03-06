# Research Notes: Manifest Task System Documentation

## Decision: Reuse Agent Queue for manifest ingestion
- **Rationale**: Leveraging `/api/queue` standardizes submission, lifecycle events, cancellation, and artifact storage while avoiding a bespoke ingestion scheduler.
- **Alternatives Considered**:
  - **Dedicated ingestion service**: Rejected because it would duplicate queue features and bypass existing worker tokens.
  - **Extend `/v1/documents/*` endpoints**: Rejected for lacking event/artifact parity and not aligning with dashboard categories.

## Decision: Canonical ManifestJobPayload with derived capabilities
- **Rationale**: Enforcing `requiredCapabilities` ensures only workers advertising needed connectors (manifest, qdrant, embeddings, github, etc.) can claim jobs, preventing misrouted execution.
- **Alternatives Considered**:
  - **Client-specified capabilities**: Risky because payload authors might omit required capabilities and bypass safety checks.
  - **No capability derivation**: Would require manual worker assignment and harm scaling.

## Decision: Dedicated `moonmind-manifest-worker`
- **Rationale**: Manifest ingestion has deterministic, multi-stage pipelines distinct from codex/gemini runtime selection; isolating execution simplifies telemetry and avoids LLM runtime coupling.
- **Alternatives Considered**:
  - **Reuse codex worker**: Would overload LLM-capable workers with ingestion logic and complicate capability checks.
  - **Server-side ingestion**: Would block API threads during long-running fetch/transform steps.

## Decision: Phase 1 scope (job type + worker + UI) with Phase 2 registry
- **Rationale**: Delivering queue + worker integration first enables internal teams to submit manifests immediately, while registry CRUD and Vault integration require additional backend work and can be layered later.
- **Alternatives Considered**:
  - **Ship registry + Vault together**: Increases critical path and delays ingestion availability.
  - **Skip registry entirely**: Limits future reuse and governance over manifests.

## Decision: Security model = token-free payloads + env/vault refs
- **Rationale**: Keeps queue database free of raw secrets while letting workers resolve credentials at runtime using env vars today and Vault references later.
- **Alternatives Considered**:
  - **Allow inline secrets**: Violates existing task safety constraints and auditability.
  - **Require Vault on day one**: Introduces infrastructure dependency that is not ready for all deployments.
