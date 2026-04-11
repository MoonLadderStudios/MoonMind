# Phase 0 Research: Jira Create Page Integration

## Decision: Add a Browser-Facing Jira Service Beside the Tool Service

**Decision**: Implement `moonmind/integrations/jira/browser.py` as a read-only presentation service that reuses the existing Jira auth/client path and policy behavior.

**Rationale**: The existing Jira tool service is action-oriented and intentionally strict for managed-agent mutation safety. The Create page needs board-browser read models, column grouping, and target-specific import text. A separate browser service keeps UI normalization concerns out of the mutation-oriented tool service while preserving the same trusted boundary.

**Alternatives considered**:

- Extend `JiraToolService` directly: rejected because browser read models would broaden the managed-agent tool contract and make mutation policy harder to reason about.
- Let the browser call Jira directly: rejected because it violates the source contract and would expose credential and Jira-domain behavior to clients.
- Build a generic external-work-item browser first: rejected as too broad for the MVP and unnecessary for the Jira-specific desired-state contract.

## Decision: Keep Create-Page Jira UI Rollout Separate From Jira Tool Enablement

**Decision**: Use the existing `jira_create_page_enabled` runtime flag and `system.jiraIntegration.enabled` boot payload to gate UI exposure independently from `ATLASSIAN_JIRA_TOOL_ENABLED`.

**Rationale**: Operators may want trusted Jira tools available to agents without exposing browser-facing Jira controls in Mission Control. Separate rollout preserves safe defaults and enables incremental delivery.

**Alternatives considered**:

- Reuse `ATLASSIAN_JIRA_TOOL_ENABLED`: rejected because it couples agent tool availability to browser UI exposure.
- Always expose Jira controls when credentials exist: rejected because credentials alone do not express operator intent for browser access.

## Decision: Use MoonMind-Owned REST Endpoints for Browser Operations

**Decision**: Add `api_service/api/routers/jira_browser.py` with endpoints for connection verification, projects, project boards, board columns, board issues, and issue detail.

**Rationale**: The Create page boot payload already publishes REST source templates. A dedicated router provides a clear authentication, policy, and error-mapping boundary and keeps the browser free of Jira credentials and raw Jira response parsing.

**Alternatives considered**:

- Dispatch through `/mcp/tools/call`: rejected because the UI browser needs typed read models and should not couple to agent tool invocation shapes.
- Add routes under task dashboard router: rejected because Jira browsing is an integration boundary, not dashboard shell rendering.

## Decision: Normalize Board Columns and Issue Grouping Server-Side

**Decision**: The browser service resolves board configuration, status-to-column mappings, and issue grouping before returning Create-page-ready data.

**Rationale**: Jira board configuration and rich text formats are provider-specific. Server-side normalization keeps the Create page simple, testable, and credential-free while ensuring consistent grouping and empty-state behavior.

**Alternatives considered**:

- Send raw Jira board configuration to the browser: rejected because it leaks Jira-domain knowledge and duplicates mapping logic in UI code.
- Group issues by status text in the browser: rejected because board column membership is not equivalent to display status text.

## Decision: Preserve Existing Create Page Submission Semantics

**Decision**: Jira import only changes existing authored text fields. It does not add Jira provenance to the submitted task payload in the initial delivery.

**Rationale**: The Create page submission path already handles objective resolution, oversized artifact fallback, dependency validation, runtime settings, and scheduling. Keeping Jira as a one-time text import reduces risk and matches the desired-state posture.

**Alternatives considered**:

- Persist Jira provenance in task payloads immediately: rejected because there is no current downstream consumer and it would expand the submission contract.
- Create a Jira-native task type: rejected by the source contract and product stance.

## Decision: Keep Browser State Local and Session Memory Optional

**Decision**: Track selected project, board, column, issue, import target, import mode, replace/append preference, loading/errors, and provenance in Create-page state. Persist only last project/board in browser session storage when enabled.

**Rationale**: Jira browsing should not change durable task state until import is explicitly confirmed. Session memory improves convenience without storing cross-session integration state or changing backend schemas.

**Alternatives considered**:

- Persist browser state server-side: rejected because it adds storage and privacy surface without MVP benefit.
- Use local storage for long-lived memory: rejected because the desired state says session-only memory.

## Decision: Validate With Boundary and UI Tests Before Broad Integration

**Decision**: Cover the feature with runtime config tests, service normalization tests, router tests, redaction/policy tests, and Create-page UI tests.

**Rationale**: The highest-risk areas are boundary behavior: credential safety, policy enforcement, board/status mapping, draft mutation semantics, preset reapply expectations, and failure isolation.

**Alternatives considered**:

- Rely mostly on manual Jira verification: rejected because provider-backed failures are hard to reproduce and the feature changes shared Create-page behavior.
- Add only isolated frontend tests: rejected because the trusted backend boundary is the security-critical part of the feature.
