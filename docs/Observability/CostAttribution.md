# Usage and Cost Attribution

Status: Draft
Owners: MoonMind Platform
Last updated: 2026-07-14

MoonMind records model usage at the provider-request and step-attempt boundary. These records are durable operational evidence, not provider invoices. Estimates may differ from billed amounts.

`UsageAttributionRecord` is the versioned canonical contract. Each record has stable workflow, run, logical-step, step-execution, attempt, and event identities. Provider request identifiers are stored only as hashes. Raw provider payloads remain in protected artifacts and never enter Temporal history or metric labels.

Sources are visibly distinct: `provider_reported`, `bridge_reported`, `moonmind_estimated`, and `unavailable`. Reported values are preserved; an estimate never overwrites them. Estimates require an immutable pricing source and version. Missing usage is represented by an unavailable reason, never a synthetic zero or text-length guess.

Side-effecting activities and services append or idempotently upsert records. Workflow code carries only compact records or artifact references. Replayed events deduplicate by event identity. A final reported event replaces an interim estimate for the same hashed request. Distinct retries and failed or canceled provider requests count whenever incurred usage is reported. Continuations and remediation attempts remain separate records and roll up to their owning step; reruns remain separate runs.

Rollups keep observed and estimated cost separate and count unavailable requests. Workflow Detail and incident reconstruction must read the same durable records. The UI displays `Unavailable` rather than `$0.00` for unknown usage and shows provider, model, attempt, retry, source, and pricing provenance.

Metrics may use only bounded labels such as environment, provider, allowlisted model family, profile class, runtime family, outcome, and source. Workflow, run, step, session, repository, user, request, and artifact identifiers are prohibited as metric labels. Authorization follows workflow ownership; retention follows execution evidence policy; provider account and billing identifiers are never exposed.

Reconciliation is idempotent, preserves correction lineage, detects duplicates and missing terminal usage, and compares durable rollups with aggregate telemetry. Historical estimates are never silently repriced; re-estimation is a separately versioned projection.
