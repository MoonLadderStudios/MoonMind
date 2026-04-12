# Research: Jira Browser API

## Decision: Reuse the trusted Jira auth/client boundary

**Rationale**: Existing Jira integration docs and code already define the safe server-side boundary for resolving SecretRefs, constructing Jira auth headers, applying timeouts/retries, and redacting sensitive material. The browser API must not create a new credential path or expose Jira credentials to the client.

**Alternatives considered**:

- Browser-to-Jira calls: rejected because it would require browser-held Jira credentials and violates the Create-page contract.
- New standalone Jira client stack: rejected because it would duplicate retry, timeout, auth, and redaction logic already present in MoonMind.

## Decision: Add a browser-facing read service beside the tool service

**Rationale**: The existing Jira tool layer is action-oriented and intentionally strict for agent mutation safety. The Create page needs a read-optimized presentation model for projects, boards, columns, grouped issues, and issue detail. A dedicated browser service keeps UI read normalization separate while still reusing the same trusted auth/client primitives.

**Alternatives considered**:

- Extend the tool registry with browser actions: rejected because browser presentation reads are not agent tools and should not be governed by agent action naming.
- Embed normalization in the router: rejected because service-level tests need to cover column mapping, issue grouping, and rich-text normalization independently from HTTP routing.

## Decision: Keep browser exposure separate from trusted Jira tool enablement

**Rationale**: Operators may enable trusted backend Jira tools without exposing Create-page browser UI. The browser API should be available only when the Create-page Jira rollout is enabled, while still relying on the same trusted Jira configuration and project policy.

**Alternatives considered**:

- Tie browser endpoints directly to Jira tool enablement: rejected because it would couple agent tooling availability to UI exposure.
- Always expose browser endpoints when Jira is configured: rejected because the Create-page UI rollout must be explicit and reversible.

## Decision: Resolve board columns from Jira board configuration server-side

**Rationale**: The browser must render columns in Jira board order and must not infer column membership from raw status names. Server-side normalization can translate Jira status mappings into stable MoonMind column records and keep empty columns renderable.

**Alternatives considered**:

- Let the frontend group by status name: rejected because Jira board column mappings are board-specific and status-name inference can be wrong.
- Return raw Jira board configuration to the browser: rejected because the browser contract should remain Create-page-ready and avoid Jira-domain parsing.

## Decision: Return an explicit unmapped issue bucket

**Rationale**: Jira issues may have statuses that are not present in a board's column configuration. An explicit unmapped group preserves data without guessing and gives the UI a safe empty/error-state path.

**Alternatives considered**:

- Drop unmapped issues: rejected because users could miss relevant stories.
- Assign unmapped issues to the first column: rejected because it hides configuration drift and misrepresents Jira workflow state.

## Decision: Normalize issue detail into plain text and recommended imports

**Rationale**: The Create-page browser should preview and import text without parsing Jira rich-text formats. Server-provided `descriptionText`, `acceptanceCriteriaText`, and target-specific recommended imports keep frontend behavior consistent.

**Alternatives considered**:

- Return raw rich text and parse it in the frontend: rejected because it duplicates Jira-specific parsing in the browser.
- Return only description text: rejected because acceptance criteria and target-specific recommendations are part of the desired Create-page contract.

## Decision: Keep submission and import semantics out of this phase

**Rationale**: This phase supplies the trusted browser read model only. Importing Jira text into preset or step fields, local provenance chips, and preset reapply semantics belong to later Create-page UI phases and should not change task submission behavior in this backend phase.

**Alternatives considered**:

- Persist Jira provenance into task payloads now: rejected because there is no downstream consumer in this phase and it would expand the task submission contract.
- Implement frontend imports together with backend reads: rejected because the server-side browser API is independently testable and should land behind the runtime rollout first.
