# Jules Proposal Delivery and Management

**Implementation tracking:** Rollout and backlog notes live in MoonSpec artifacts (`specs/<feature>/`), gitignored handoffs (for example `artifacts/`), or other local-only files—not as migration checklists in canonical `docs/`.

Status: Proposed
Owner: MoonMind Engineering
Audience: Platform, Temporal, and API teams

## 1) Overview

MoonMind workers routinely generate follow-up and run-quality proposals (as described in `docs/TaskProposalQueue.md`). Currently, these proposals sit in the MoonMind API queue awaiting human review or manual promotion. 

This document outlines a design for systematically sending these proposals to the Jules environment for automated or human-in-the-loop consumption, fully managed by Temporal workflows. By moving proposal delivery to Temporal, MoonMind can ensure durable tracking, retryable API interactions, and asynchronous synchronization with Jules' native execution or triage systems.

## 2) Problem Statement

When Jules is acting as the primary agent environment or execution engine, it lacks visibility into internally generated MoonMind proposals until they are explicitly promoted to tasks.

We need a reliable, durable mechanism to push or expose these proposals to Jules so that:
- Jules agents can triage and action run-quality proposals.
- Jules systems can seamlessly incorporate follow-up proposals into their execution context.
- Delivery failures (e.g., rate limits, network issues) are durably retried without operator intervention.
- MoonMind retains a definitive, auditable record of whether Jules successfully received a proposal.

## 3) Proposed Architecture

### 3.1 Temporal Workflow: `MoonMind.ProposalDelivery`

We propose a dedicated Temporal workflow, `MoonMind.ProposalDelivery` (or integrating this logic into the lifecycle of an existing workflow), responsible for synchronizing a newly created or updated proposal with Jules.

1. **Trigger:** The MoonMind API or an upstream workflow starts a `ProposalDelivery` execution when a new proposal targeting Jules is created, or when an existing proposal meets specific routing criteria.
2. **Activity: `integration.jules.send_proposal`:** A new Temporal activity maps the MoonMind proposal schema into a Jules-compatible payload. If Jules does not have a native "proposal" entity, this maps to a Jules task with specific metadata (e.g., `type: proposal`).
3. **Activity: `integration.jules.sync_proposal_status` (Optional):** If Jules actions the proposal, a polling or callback activity waits for the resolution status and maps it back to MoonMind's proposal status (e.g., `promoted`, `rejected`).
4. **Resolution:** The workflow completes once Jules acknowledges receipt (and optionally, resolution) of the proposal.

### 3.2 Integration with Jules Adapter

The existing Jules adapter (`moonmind/workflows/adapters/jules_client.py`) will be extended to support proposal delivery.

#### Payload Mapping Strategy
- MoonMind `proposal.title` -> Jules `title`
- MoonMind `proposal.signal_metadata` and origin context -> Jules `description` or structured `metadata`
- MoonMind `proposal.target_repository` -> Jules execution context

### 3.3 Status Synchronization and Callbacks

Once delivered, the lifecycle of the proposal in MoonMind and Jules must remain synchronized.
- If Jules rejects or dismisses the proposal, MoonMind marks it as `rejected`.
- If Jules accepts/promotes the proposal into an active run, MoonMind marks it as `promoted`.

Since Jules is moving toward an ExternalEvent/Callback model (per `JulesTemporalExternalEventContract.md`), MoonMind will prefer receiving Temporal signals (`ExternalEvent`) from Jules to update the proposal's state. It will fall back to Temporal-managed polling (`integration.jules.sync_proposal_status`) if a verified callback path is not yet available.

## 4) Temporal Activity Contract Expansion

The activity catalog (`moonmind/workflows/temporal/activity_catalog.py`) will be expanded with the following signatures:

- `integration.jules.send_proposal`:
 - **Input:** `proposal_id`, `correlation_id`, `proposal_payload`
 - **Output:** `external_operation_id` (Jules ID), `provider_status`
- `integration.jules.sync_proposal_status`:
 - **Input:** `external_operation_id`
 - **Output:** `normalized_status` (e.g., `pending`, `promoted`, `rejected`), `provider_status`

These activities will reuse the core Jules integration patterns, including status normalization and artifact discipline.

## 5) Failure Handling and Retries

Temporal provides the necessary durability to handle intermittent delivery failures safely:
- **Rate Limiting:** Jules API rate limits (`429`) and transient network issues will be handled via Temporal's activity retry policies with exponential backoff and jitter.
- **Provider Outages:** If Jules is unreachable (`5xx`), the workflow will suspend and retry without losing the proposal payload.
- **Terminal Errors:** If Jules rejects the payload format (`4xx`), the activity will fail without retry, the workflow will surface a failure summary artifact, and the proposal in MoonMind will be flagged with a delivery error state for operator intervention.

## 6) Security and Correlation

- **Secrets:** Jules API keys remain strictly in the worker context. They are never exposed in workflow history, memo fields, or artifacts.
- **Correlation:** MoonMind will attach a stable `correlation_id` in the Jules payload metadata to link the Jules entity back to the MoonMind proposal, ensuring idempotency across retries and preventing duplicate submissions.

## 7) Delivery surface

**Target:** Jules-facing proposal payloads flow through extended adapter schemas, dedicated Temporal activities (`send_proposal`, `sync_proposal_status` or equivalents), a `MoonMind.ProposalDelivery` (or integrated) workflow triggered from the API, and dashboard visibility of delivery status. Build-out status is in .
