# Research Log — Task Proposal Targeting Policy

## Decision 1: Where to store global proposal target defaults
- **Rationale**: Centralize configuration inside `moonmind/config/settings.py` so both the API service and Codex worker share the same defaults without bespoke env parsing. Adding `proposal_targets_default` (`project|moonmind|both`) and `moonmind_ci_repository` fields to `TaskProposalSettings` lets us pull the values anywhere via `settings.task_proposals`.
- **Alternatives Considered**:
  - *SpecWorkflowSettings only*: rejected because non-worker surfaces (API router, dashboard) would lack direct access.
  - *New ad-hoc env parsing in worker/router*: rejected to avoid drift and duplicated validation.

## Decision 2: ProposalPolicy representation inside canonical task payloads
- **Rationale**: Extend `CanonicalTaskPayload` and `CreateJobRequest` with a dedicated `proposalPolicy` object containing `targets`, `maxItems`, and `minSeverityForMoonMind`. This keeps overrides alongside other task-scoped directives, survives normalization, and is persisted inside `task_context.json` for worker-side enforcement.
- **Alternatives Considered**:
  - *Embedding policy under `task.metadata`*: rejected because existing tooling does not preserve arbitrary metadata sections.
  - *Passing overrides via environment variables*: rejected because policies must vary per task run and be auditable within the job payload itself.

## Decision 3: Severity + priority mapping logic
- **Rationale**: Implement mapping inside `TaskProposalService` so MoonMind-targeted proposals always have server-derived `reviewPriority`. Signals will be parsed from the proposal payload (tags + origin metadata) and mapped to HIGH/NORMAL/LOW buckets per DOC-REQ-007. Doing this server-side ensures even manually submitted CI proposals follow the policy.
- **Alternatives Considered**:
  - *Worker-derives priority only*: rejected because API clients or integrations could bypass the worker, and server enforcement is simpler to audit.

## Decision 4: Metadata validation & enrichment responsibilities
- **Rationale**: Split responsibilities—worker enriches origin metadata (trigger repo/job/step, signal measurements) because it has the live run context; API service validates the fields before persisting. This satisfies DOC-REQ-008 while keeping the API stateless.
- **Alternatives Considered**:
  - *API service derives trigger metadata*: rejected; the service does not have visibility into the originating run/job details and would need new persistence.

## Decision 5: Dashboard filter strategy
- **Rationale**: Reuse existing client-side filtering pipeline in `dashboard.js` and add repository/category/tag selectors with cached option lists derived from the loaded dataset. Avoids new backend endpoints and keeps latency low.
- **Alternatives Considered**:
  - *Server-side filter query builder*: rejected for now because API already supports repository/category filters; we only need to expose UI controls.
