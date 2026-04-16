# MM-343 MoonSpec Orchestration Input

## Source

- Jira issue: MM-343
- Board scope: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: MoonSpec STORY-002: Resolve Claude policy envelopes and handshakes
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

Jira issue: MM-343 from MM board
Summary: MoonSpec STORY-002: Resolve Claude policy envelopes and handshakes
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-343 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-343: MoonSpec STORY-002: Resolve Claude policy envelopes and handshakes

User Story
As an enterprise administrator, I need Claude managed settings, permission modes, rules, hooks, sandbox, MCP, memory, provider, and handshake state compiled into a versioned PolicyEnvelope so session behavior is enforceable and auditable.

Source Document
docs/ManagedAgents/ClaudeCodeManagedSessions.md

Source Sections
- 2.3 Treat policy as compiled runtime state
- 11. Policy model
- 21.2 Managed settings risk model
- 22.5 Known semantic mismatch

Coverage IDs
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-010
- DESIGN-REQ-024
- DESIGN-REQ-028
- DESIGN-REQ-030

Story Metadata
- Story ID: STORY-002
- Short name: claude-policy-envelope
- Dependency mode: none
- Story dependencies from breakdown: STORY-001

Acceptance Criteria
- Server-managed settings win when non-empty; endpoint-managed settings apply only when server-managed settings are empty or unsupported.
- Managed settings cannot be overridden by local settings, CLI arguments, or user settings.
- Policy fetch states include cache_hit, fetched, fetch_failed, and fail_closed as applicable.
- Risky managed hooks and managed environment variables can require an explicit security-dialog state in interactive sessions.
- BootstrapPreferences for Claude are labeled and handled as bootstrap templates, never represented as native user-overridable managed defaults.
- Every compiled envelope records provider_mode and policy trust level for governance reporting.

Requirements
- Compile all relevant policy sources into one effective PolicyEnvelope.
- Version policy envelopes over time and emit policy events.
- Preserve Claude settings precedence exactly.
- Expose limited support for Codex-style managed defaults honestly.
- Support fail-closed startup behavior when configured policy refresh fails.

Independent Test
Feed server-managed, endpoint-managed, empty, fetch-failed, fail-closed, interactive-dialog, and non-interactive policy scenarios through the policy boundary and assert the compiled PolicyEnvelope, events, and handshake states match precedence rules.

Out of Scope
- Actual tool execution decisions.
- Context compaction.
- Hook runtime invocation.

Needs Clarification
- [NEEDS CLARIFICATION] How much policy fetch state should be exposed to end users versus administrators?
