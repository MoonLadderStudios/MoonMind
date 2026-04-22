# Research: Route Claude Auth Actions

## FR-001 / DESIGN-REQ-001 - Provider Row Placement

Decision: preserve existing Provider Profiles table placement.
Evidence: `frontend/src/components/settings/ProviderProfilesManager.tsx` renders provider rows and action buttons in the Settings Provider Profiles table; tests already cover row/card labels in `frontend/src/components/settings/ProviderProfilesManager.test.tsx`.
Rationale: The requested entrypoint already lives in the correct UI surface, so the story should extend row actions rather than add a page or route.
Alternatives considered: A standalone Claude page was rejected because the Jira brief and source design explicitly forbid it.
Test implications: Unit UI assertions should prove Claude actions render in the row and no standalone navigation is introduced.

## FR-002 / FR-005 / FR-006 / DESIGN-REQ-007 - Claude Labels And Lifecycle Actions

Decision: add a Claude action classifier that emits `Connect Claude` for disconnected rows and supported lifecycle labels for connected rows.
Evidence: Current action rendering only derives `canStartOAuth` from `isCodexOAuthCapable(profile)` and renders a generic `Auth` button.
Rationale: The story is label and routing focused; lifecycle labels can be derived from trusted row metadata without implementing token persistence in this slice.
Alternatives considered: Reusing the Codex OAuth button was rejected because the source design requires Claude-specific labels and action semantics.
Test implications: Unit UI tests must cover disconnected Claude, connected Claude with supported actions, unsupported actions omitted, and absence of Codex labels.

## FR-003 / DESIGN-REQ-003 - Metadata-Based Capability

Decision: replace the Codex-only auth helper with a classifier that considers runtime, provider, credential strategy, and explicit command/readiness metadata.
Evidence: `isCodexOAuthCapable(profile)` currently returns true only when `profile.runtime_id === 'codex_cli'`; `ProviderProfile` already carries `provider_id`, `credential_source`, `runtime_materialization_mode`, `command_behavior`, and `secret_refs`.
Rationale: The requirement forbids deciding capability solely from the Codex runtime check. A classifier can keep Codex OAuth support while enabling Claude only when the row identifies the Claude Anthropic provider and trusted metadata supports it.
Alternatives considered: Adding backend schema fields first was rejected for this slice because the UI can consume existing row metadata and fail closed when metadata is absent.
Test implications: Tests should include a Claude row with explicit supported actions and a non-Claude or unsupported row with no Claude action.

## FR-004 - Codex OAuth Regression

Decision: preserve the existing Codex OAuth request path.
Evidence: Tests named `starts a Codex OAuth session from the profile Auth action`, `supports OAuth finalize without offering reconnect after success`, and `supports OAuth retry actions for failed Settings sessions` cover the Codex path.
Rationale: Claude action classification must not change the existing Codex OAuth session endpoints, payload, terminal launch, or labels.
Alternatives considered: Renaming Codex `Auth` was rejected as unrelated scope.
Test implications: Existing Codex OAuth tests should continue passing; update only if aria labels need to distinguish action kind.

## FR-008 - Fail-Closed Unsupported Metadata

Decision: unsupported Claude or missing metadata should show no Claude lifecycle actions.
Evidence: No current Claude auth logic exists.
Rationale: Hiding actions is safer than presenting a misleading enrollment flow when the row does not expose trusted capability/readiness metadata.
Alternatives considered: Showing `Connect Claude` for every `claude_cli` runtime was rejected because the spec requires metadata, provider, or credential strategy rather than runtime-only checks.
Test implications: Add a Claude-looking row without supported metadata and assert `Connect Claude` is absent.

## FR-009 - Readiness And Validation State

Decision: expose a concise Claude auth status line from trusted metadata when available.
Evidence: Current status cell shows enabled/disabled and OAuth session status only.
Rationale: Operators need to distinguish connected, disconnected, failed, and degraded Claude row states without leaving Settings.
Alternatives considered: Adding a new backend readiness endpoint was rejected for this story to keep scope row-local.
Test implications: Add a UI assertion for a metadata-backed Claude readiness label when provided.

## SC-* / Traceability

Decision: preserve MM-445 and source mappings through all artifacts and final verification.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-445-moonspec-orchestration-input.md` and `spec.md` include MM-445, DESIGN-REQ-001, DESIGN-REQ-003, and DESIGN-REQ-007.
Rationale: Final verification and PR metadata need stable Jira/source references.
Alternatives considered: None.
Test implications: Final `/moonspec-verify` should confirm traceability.
