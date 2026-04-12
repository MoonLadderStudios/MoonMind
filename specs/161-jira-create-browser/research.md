# Research: Jira Create Browser

## Decision: Gate browser visibility through runtime config

**Decision**: The Create page will render Jira browser entry points only when both `system.jiraIntegration.enabled` is true and `sources.jira` endpoint templates are present in the boot payload.

**Rationale**: The spec requires browser exposure to be separate from trusted backend Jira tooling. Runtime config is already the Create page's central discovery mechanism for Temporal sources, templates, provider profiles, and attachments.

**Alternatives considered**:

- Always render Jira when trusted Jira tooling is enabled. Rejected because the source contract explicitly separates trusted backend tooling from Create page browser rollout.
- Hardcode browser endpoint paths in the client. Rejected because runtime config is the canonical operator-controlled rollout surface.

## Decision: Use MoonMind-owned Jira endpoint templates only

**Decision**: Browser data hooks will interpolate and fetch only MoonMind-owned endpoint templates supplied by `sources.jira`.

**Rationale**: Browser clients must not call Jira directly or carry Jira credentials. Keeping all external integration details behind MoonMind preserves policy enforcement, SecretRef-aware auth, and redaction boundaries.

**Alternatives considered**:

- Call Jira REST APIs from the browser. Rejected for credential and policy-boundary reasons.
- Let the browser infer Jira board columns from raw issue status text. Rejected because the source contract assigns status-to-column normalization to MoonMind.

## Decision: Keep browser state local to the Create page for Phase 4

**Decision**: Track selected project, selected board, active column, issues by column, selected issue, open/closed status, target, replace/append preference, loading, and error states in the Create page entrypoint.

**Rationale**: Phase 4 does not persist Jira provenance or submit Jira-specific payload fields. Local state avoids expanding task submission contracts before there is a downstream consumer.

**Alternatives considered**:

- Persist Jira browser state in task payloads. Rejected as out of scope for Phase 4.
- Introduce a global Jira browser store. Rejected because the Create page is the only consumer in this phase.

## Decision: Use one shared browser surface

**Decision**: Implement one modal or drawer titled `Browse Jira story`, opened from preset or step instruction contexts.

**Rationale**: The desired-state doc describes Jira as a secondary instruction-source surface, not a separate page section. One shared surface prevents duplicated browser state and makes the active target explicit.

**Alternatives considered**:

- Embed a browser under each field. Rejected because it creates parallel surfaces and makes it harder to preserve one active target.
- Create a standalone Jira workbench page. Rejected because the Create page remains the task composition surface.

## Decision: Do not import text in Phase 4

**Decision**: Selecting an issue loads preview only. Import execution, import modes, reapply messaging, provenance chips, and session memory remain deferred.

**Rationale**: The requested phase is the browser shell. Preserving task objective, step instructions, preset expansion, and submission payloads reduces risk while the browser flow is introduced.

**Alternatives considered**:

- Implement step or preset import immediately. Rejected because later phases explicitly cover import semantics and preset reapply behavior.
