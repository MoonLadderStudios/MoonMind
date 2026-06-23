# Story Breakdown: Provider Profiles

- Source design: `docs/Security/ProviderProfiles.md`
- Source document class: `canonical-declarative`
- Story extraction date: `2026-06-23T03:51:11Z`
- Requested output mode: `jira`

## Design Summary

Provider Profiles define MoonMind's provider-aware managed-runtime execution contract. The design separates provider-profile semantics from secret storage, OAuth terminal transport, Settings setup UX, and runtime strategy code while specifying activation state, credential source classes, materialization modes, request routing, capacity coordination, launch-time security, persistence, migration, and acceptance tests. The operational outcome is that Claude, Codex, Gemini, and alternative provider combinations are visible and configurable in Settings but only launch through explicit, verified, secure, provider-aware profiles.

## Coverage Points

- `DESIGN-REQ-001` **Provider Profiles are durable execution contracts** (requirement, 1. Summary; 19. Summary): A Provider Profile answers runtime, upstream provider, credential source, materialization, concurrency/cooldown, routing, and launchability for managed runtime launches.
- `DESIGN-REQ-002` **System boundaries stay separated** (constraint, 2. Document Boundaries): Provider Profiles own launch-target semantics while Secrets owns SecretRef behavior, OAuth Terminal owns browser-terminal transport, and Settings owns discovery/setup UX.
- `DESIGN-REQ-003` **Support many runtimes, providers, credential classes, and materialization modes** (requirement, 3. Goals; 5. Key Concepts): The model must support multiple profiles per runtime, multiple providers per runtime, OAuth/API-key/no-secret credential classes, runtime-specific materialization modes, default model intent, routing metadata, and activation state.
- `DESIGN-REQ-004` **Explicit non-goals preserve scope** (non-goal, 4. Non-Goals): Provider Profiles do not normalize every provider into a universal API, store raw credentials, replace runtime strategies, define OAuth terminal UX, redefine secret backends, solve billing, or make unconfigured providers launchable.
- `DESIGN-REQ-005` **Enabled and launch_ready are distinct** (state-model, 5.6 Enabled vs Launch Ready): enabled records operator/user intent, while launch_ready is computed from enabled, auth_state, credential validity, OAuth/SecretRef readiness, provider validation, and workspace policy.
- `DESIGN-REQ-006` **Canonical profile and file-template contracts are explicit** (state-model, 6. Provider Profile Model): ManagedAgentProviderProfile and RuntimeFileTemplate define persistent fields for runtime/provider identity, credentials, materialization, activation, routing, concurrency, audit metadata, and generated files.
- `DESIGN-REQ-007` **First-party Settings activation is safe by default** (requirement, 7. Settings-First Activation Model): Claude, Codex, and Gemini provider cards are visible before setup but disabled/not launchable until successful OAuth or API-key setup, which enables the profile by default unless policy blocks it.
- `DESIGN-REQ-008` **User-disabled and policy states are respected** (state-model, 7.3-7.7 User-initiated activation, passive validation, manual disable, API behavior): Passive validation may update diagnostics but must not re-enable user-disabled profiles; direct setup or enable actions may clear user_disabled; API writes must reject enabled=true when readiness blockers remain.
- `DESIGN-REQ-009` **OAuth-backed profiles are reusable profile rows** (integration, 8. OAuth-Backed Provider Profiles): OAuth terminal transport creates or updates a provider profile with OAuth volume metadata and connected/enabled state after verification, without storing terminal-session fields or OAuth tokens in the profile.
- `DESIGN-REQ-010` **API-key setup stores SecretRefs only** (security, 9. API-Key-Backed Provider Profiles): API keys are accepted through Settings or credential endpoints, validated, stored in the Secrets System, represented in the profile by SecretRefs/templates, and never persisted raw in profile rows, payloads, diagnostics, audit rows, or artifacts.
- `DESIGN-REQ-011` **Requests resolve profiles provider-aware** (requirement, 10. Request and Selection Model): Execution requests support exact profile refs and selectors; resolution filters by runtime, provider, tags, enabled state, launch readiness, cooldown, and available slots, then applies priority and slot tie-breaks.
- `DESIGN-REQ-012` **Default provider fallback avoids accidental disabled or arbitrary routing** (constraint, 10.4 Default provider fallback): Generic runtime requests may select compatible launch-ready alternatives by priority/capacity, but disabled setup stubs never participate and UI flows should prefer explicit provider or default-tag constraints.
- `DESIGN-REQ-013` **ProviderProfileManager coordinates capacity** (integration, 11. Provider Profile Manager Workflow): Per-runtime singleton manager workflows own active leases, slot capacity, cooldowns, queued requests, assignment decisions, sync only enabled launch-ready profiles, and expose request/release/cooldown/sync signals.
- `DESIGN-REQ-014` **Waiting and cooldown behavior are durable and observable** (observability, 11.6-11.7 Waiting semantics, Cooldown behavior): Runs wait durably or fail fast when no compatible profile is available, surface missing setup/capacity/cooldown/policy context, and report provider 429 cooldowns against the selected profile before re-requesting slots.
- `DESIGN-REQ-015` **Runtime materialization is layered and launch-boundary checked** (requirement, 12. Runtime Materialization Pipeline): Launch builds a predictable layered environment from base defaults through selected profile, rechecks readiness, clears competing env vars, resolves secrets only at launch, materializes files, applies templates and strategy shaping, then launches.
- `DESIGN-REQ-016` **Persistence uses provider-aware table and safe defaults** (state-model, 13. Persistence Model): Provider profiles persist in managed_agent_provider_profiles with activation columns, indexes, constrained enum values, no raw secrets, and disabled/not_configured/missing_credentials defaults for unconfigured setup stubs and new custom profiles outside verified setup flows.
- `DESIGN-REQ-017` **Provider-profile security rules prevent credential leakage** (security, 14. Security Requirements): Workflows carry only profile IDs/selectors, profiles store only refs/templates/volume metadata, secrets resolve only at controlled boundaries, logs/artifacts redact sensitive data, OAuth volumes are isolated, competing variables are cleared, proxy-first is preferred, and only user-initiated setup auto-enables.
- `DESIGN-REQ-018` **Runtime/provider examples define concrete first-party and MiniMax mappings** (artifact, 15. Examples): Examples specify expected stub, OAuth, API-key, Gemini, Codex, Claude, and MiniMax profile shapes, including clear_env_keys, env/file templates, defaults, tags, priority, and command behavior.
- `DESIGN-REQ-019` **Migration preserves activation semantics and defaults** (migration, 16. Migration Plan): Migration adds activation columns, changes defaults, seeds setup stubs, backfills connected profiles, disables missing/invalid credentials, preserves user disables, updates default normalization, manager sync, and Settings UI.
- `DESIGN-REQ-020` **Acceptance coverage and terminology complete the desired state** (constraint, 17. Acceptance Tests; 18. Terminology): Required tests cover stubs, create/update enable guards, OAuth/API-key success and failure, user-disable protection, manager/default exclusions, workflow default rejection, Settings cards, and Auth Profile terminology replacement.

## Ordered Story Candidates

### STORY-001: Define provider-aware profile contract and persistence

- Short name: `profile-contract`
- Source reference: `docs/Security/ProviderProfiles.md` (1. Summary, 2. Document Boundaries, 3. Goals, 4. Non-Goals, 5. Key Concepts, 6. Provider Profile Model, 13. Persistence Model, 19. Summary)
- Why: This establishes the durable object every later setup, selection, and launch path relies on.
- Independent test: Create and validate provider profile records for multiple runtime/provider/credential combinations, then assert persisted fields, enum constraints, safe defaults, source-boundary behavior, and no raw secret storage in profile-owned fields.
- Dependencies: None
- Coverage: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-016

Acceptance criteria:

- ManagedAgentProviderProfile captures runtime_id, provider_id, credential_source, runtime_materialization_mode, activation state, routing metadata, model intent, concurrency/cooldown policy, SecretRef/OAuth volume bindings, templates, and audit metadata.
- New unconfigured first-party setup stubs and custom profiles outside a verified setup flow default to enabled=false, auth_state=not_configured, disabled_reason=missing_credentials, and credential_source=none where applicable.
- The persistence layer enforces valid auth_state and disabled_reason values and indexes runtime/provider/readiness lookup paths.
- Provider Profile code treats SecretsSystem, OAuthTerminal, Settings, and runtime strategies as separate owners and does not embed their private transport or storage semantics.
- Non-goals are enforced by tests or explicit validation: no universal provider protocol, no raw credential storage, no launchability for unconfigured first-party profiles, and no removal of runtime strategy shaping.

Scope:

- Provider-profile domain model fields and enum constraints
- Provider/credential/materialization/default-model/concurrency/routing metadata
- Persistence shape and safe defaults
- Clear boundaries with Secrets, OAuth Terminal, Settings, and runtime strategies

Out of scope:

- Secret backend implementation
- Browser-terminal OAuth transport
- Replacing runtime-specific launch strategy code

### STORY-002: Expose safe Settings-first provider activation

- Short name: `settings-activation`
- Source reference: `docs/Security/ProviderProfiles.md` (7. Settings-First Activation Model, 17. Acceptance Tests)
- Why: The Settings experience is the operator-facing path that turns setup stubs into launchable provider profiles without unsafe auto-enable behavior.
- Independent test: Exercise Settings/API profile setup and patch flows for fresh stubs, successful setup, failed setup, manual disable, passive validation, policy block, and explicit re-enable, asserting launchability and diagnostics after each transition.
- Dependencies: STORY-001
- Coverage: DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-020

Acceptance criteria:

- Claude Code/Anthropic, Codex CLI/OpenAI, and Gemini CLI/Google are discoverable before credentials exist and show setup-required actions.
- Fresh setup stubs are disabled, not default, auth_state=not_configured, and disabled_reason=missing_credentials.
- Successful user-initiated OAuth, reconnect, add-key, or rotate-key setup sets auth_state=connected, disabled_reason=null, enabled=true, and validation timestamps/methods as appropriate unless policy blocks launch.
- PATCH or create requests that attempt enabled=true fail with a clear validation error while OAuth/API-key readiness blockers remain.
- Passive validation, migrations, admin repair jobs, readiness refreshes, and health probes do not clear disabled_reason=user_disabled or silently re-enable profiles.
- Disconnect, validation failure, manual disable, and policy block produce the documented auth_state/disabled_reason/enabled combinations and user-facing diagnostics.

Scope:

- Settings-visible first-party setup cards
- Setup-required, connected, disabled, validation-failed, disconnected, and policy-blocked states
- Enable validation rules for direct setup actions versus passive validation
- Readiness diagnostics returned to UI/API callers

Out of scope:

- OAuth terminal transport internals
- Secret backend storage internals
- ProviderProfileManager slot assignment

### STORY-003: Register OAuth-backed provider profiles after verification

- Short name: `oauth-profiles`
- Source reference: `docs/Security/ProviderProfiles.md` (8. OAuth-Backed Provider Profiles, 15.2 Claude Code + Anthropic OAuth after setup, 15.4 Gemini CLI + Google OAuth after setup, 15.5 Codex CLI + OpenAI OAuth after setup, 17. Acceptance Tests)
- Why: OAuth is a major first-party setup path and must bridge OAuthTerminal output into the durable launch contract without coupling profiles to browser terminal transport.
- Independent test: Run the OAuth finalization service with verified, failed, and disconnect outcomes for Claude/Codex/Gemini profiles and assert profile fields, enabled state, absence of terminal/tokens, and Settings readiness metadata.
- Dependencies: STORY-001, STORY-002
- Coverage: DESIGN-REQ-009, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-020

Acceptance criteria:

- OAuth finalization writes credential_source=oauth_volume, runtime_materialization_mode=oauth_home, volume_ref, volume_mount_path, account_label, connected auth_state, null disabled_reason, last_auth_method=oauth_volume, and enabled=true after verification succeeds.
- Failed OAuth verification leaves the profile visible but disabled with auth_state=validation_failed and disabled_reason=auth_invalid.
- OAuth disconnect sets auth_state=disconnected, disabled_reason=disconnected, enabled=false, and clears OAuth volume fields.
- Provider Profile rows never contain PTY bridge IDs, WebSocket URLs, terminal session IDs, browser session status, access tokens, or refresh tokens.
- First-party OAuth profiles include the documented clear_env_keys, home_path_overrides, tags, default readiness labels, and default/priority behavior where applicable.

Scope:

- OAuth verification to Provider Profile create/update
- OAuth success, failure, and disconnect transitions
- Volume reference and mount metadata storage
- First-party OAuth profile materialization defaults and command readiness metadata

Out of scope:

- PTY/WebSocket browser terminal implementation
- OAuth provider token storage format inside runtime-owned auth volumes

### STORY-004: Validate API-key setup into SecretRef-backed profiles

- Short name: `apikey-profiles`
- Source reference: `docs/Security/ProviderProfiles.md` (9. API-Key-Backed Provider Profiles, 14. Security Requirements, 15.3 Claude Code + Anthropic API key after setup, 17. Acceptance Tests)
- Why: API-key setup is the second first-party activation path and has the highest direct credential-handling risk.
- Independent test: Submit valid and invalid API-key setup requests, including rotate-key and readiness-blocked cases, then assert SecretRef-only profile updates, connected/enabled state on success, disabled diagnostics on failure, and secret redaction in captured payload/log/artifact outputs.
- Dependencies: STORY-001, STORY-002
- Coverage: DESIGN-REQ-010, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-020

Acceptance criteria:

- The API-key setup endpoint checks write permission, validates the supplied key provider-specifically, stores the key through the Secrets System, and writes only SecretRefs plus templates into the profile.
- Successful setup sets credential_source=secret_ref, runtime_materialization_mode=api_key_env or provider-required mode, auth_state=connected, disabled_reason=null, first/last validation metadata, last_auth_method=secret_ref, and enabled=true.
- Failed validation sets auth_state=validation_failed, disabled_reason=auth_invalid, enabled=false, and does not persist the candidate key in workflow payloads, profile rows, diagnostics, audit rows, or artifacts.
- First-party mappings bind Anthropic, OpenAI, and Google keys to documented secret roles, env_template entries, and clear_env_keys.
- Runtime-specific deviations for key names, config files, or home directory behavior remain strategy-owned and explicit.

Scope:

- API-key credentials endpoint behavior
- Provider-specific validation before enablement
- SecretRef persistence and env template binding
- Failure handling and raw-key hygiene
- First-party API-key mappings for Claude, Codex, and Gemini

Out of scope:

- Secrets backend encryption and rotation internals beyond consuming returned SecretRefs
- General-purpose provider billing or pricing attribution

### STORY-005: Resolve execution requests with provider-aware selection

- Short name: `profile-routing`
- Source reference: `docs/Security/ProviderProfiles.md` (10. Request and Selection Model, 17. Acceptance Tests)
- Why: Once a runtime can target multiple providers, launch correctness depends on deterministic provider-aware filtering and fallback rules.
- Independent test: Resolve requests across multiple profiles for the same runtime with disabled stubs, cooldowns, full slots, provider selectors, tag selectors, exact refs, and priority ties, asserting the selected profile or validation failure.
- Dependencies: STORY-001, STORY-002
- Coverage: DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-020

Acceptance criteria:

- AgentExecutionRequest supports execution_profile_ref and profile_selector fields for provider_id, tags_any, tags_all, and runtime_materialization_mode.
- Exact profile references resolve only to the requested profile and fail when it is disabled, not launch-ready, policy-blocked, or incompatible with the runtime request.
- Selector resolution follows the documented order: runtime, provider, tags, enabled, launch_ready, cooldown, available slots, highest priority, then most free slots.
- Disabled setup stubs never participate in default provider fallback or default profile normalization.
- Mission Control default flows either specify provider, use a default tag convention, or rely on explicit priority ordering to avoid unintended cross-provider routing.
- workflow.default_provider_profile_ref rejects disabled or not-ready profiles.

Scope:

- AgentExecutionRequest profile fields and selectors
- Exact profile resolution
- Provider/tag/materialization filtering
- Enabled, launch_ready, cooldown, and slot exclusion
- Priority and available-slot tie-breaks
- Default-provider fallback guardrails

Out of scope:

- Slot lease lifecycle implementation inside ProviderProfileManager
- Settings setup-card rendering

### STORY-006: Coordinate profile leases, capacity, cooldowns, and waiting

- Short name: `profile-manager`
- Source reference: `docs/Security/ProviderProfiles.md` (11. Provider Profile Manager Workflow, 17. Acceptance Tests)
- Why: Capacity and cooldown are profile-level orchestration concerns that must survive workflow crashes and remain visible to operators.
- Independent test: Drive a manager workflow with enabled launch-ready profiles, disabled stubs, slot exhaustion, cooldown reports, releases, exact-profile requests, selector-based requests, and 429 classifications, then assert assignments, waiting state, cooldown windows, and UI/projection summaries.
- Dependencies: STORY-005
- Coverage: DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-020

Acceptance criteria:

- One ProviderProfileManager singleton exists per runtime family and can Continue-As-New independently.
- Manager sync accepts only profiles that are enabled and launch_ready, preventing disabled setup stubs from becoming runnable.
- Manager state owns active leases, slot capacity, cooldown windows, queued requests, and assignment decisions.
- When no compatible launch-ready profile is available, runs durably enter awaiting_slot or fail fast according to request policy with summaries that identify runtime, provider/exact profile intent, and missing condition.
- On provider 429 or equivalent quota exhaustion, AgentRun reports cooldown for the selected profile, releases the slot, and re-requests through the same selector unless exact-profile intent prevents alternatives.
- Runtime strategies classify provider rate limits into the documented integration_error/provider_error_code or retry recommendation shape needed by orchestration.

Scope:

- Per-runtime ProviderProfileManager singleton workflows
- Lease, capacity, cooldown, queue, and assignment state
- request_slot, release_slot, report_cooldown, sync_profiles, slot_assigned, and shutdown signals
- Durable awaiting_slot behavior and operator-visible summaries
- Provider 429 classification integration

Out of scope:

- Provider-specific API-key validation
- Runtime subprocess materialization

### STORY-007: Materialize runtime environments securely from selected profiles

- Short name: `runtime-materialization`
- Source reference: `docs/Security/ProviderProfiles.md` (12. Runtime Materialization Pipeline, 14. Security Requirements, 15. Examples)
- Why: The selected profile only becomes useful when launch code translates it into environment variables, generated config, home overrides, and runtime command behavior without credential leakage or process-context loss.
- Independent test: Launch materialization in a controlled test harness for OAuth, API-key, env_bundle, config_bundle, and composite profiles, asserting base env preservation, cleared competing variables, ephemeral secret resolution, generated file content/permissions, strategy shaping, and redaction of sensitive values.
- Dependencies: STORY-001, STORY-005
- Coverage: DESIGN-REQ-015, DESIGN-REQ-017, DESIGN-REQ-018

Acceptance criteria:

- The launcher follows the documented order from base environment through runtime-global defaults, selected profile, readiness recheck, clear_env_keys, secret resolution, file templates, env template, home overrides, strategy shaping, command build, and subprocess launch.
- Profile materialization layers onto the base environment and never replaces it wholesale, preserving PATH, HOME, and runtime process context unless explicitly overridden.
- SecretRefs resolve only at controlled launch/proxy boundaries for the minimum duration needed and raw resolved values are redacted from logs, metadata, diagnostics, and durable artifacts.
- Generated files respect format, merge_strategy, and permissions; files containing credentials are sensitive runtime files and are not durable artifacts by default.
- Conflicting provider variables are removed or blanked before launch according to clear_env_keys to prevent accidental fallback.
- Runtime strategies consume command_behavior, default_model, and model_overrides without Provider Profiles replacing strategy-owned command construction.
- Proxy-first execution is preferred when MoonMind owns the outbound provider call path, with direct-credential materialization reserved for runtimes that require it.

Scope:

- Launch-boundary readiness recheck
- Layered environment construction
- clear_env_keys handling
- SecretRef resolution into ephemeral launch-only values
- file_templates materialization with permissions and merge strategies
- env_template and home_path_overrides application
- runtime strategy command_behavior integration
- Redaction, artifact hygiene, volume isolation, proxy-first preference

Out of scope:

- Profile selection algorithm before launch
- Secrets backend storage implementation
- Making generated secret-bearing config files durable artifacts by default

### STORY-008: Migrate Auth Profile terminology and activation semantics

- Short name: `profile-migration`
- Source reference: `docs/Security/ProviderProfiles.md` (16. Migration Plan, 17. Acceptance Tests, 18. Terminology)
- Why: The design replaces a narrower auth concept; the rollout must prevent partial migrations that leave unsafe defaults or stale names behind.
- Independent test: Run migration/backfill tests against representative old records and source scans for terminology, then assert new columns/states, preserved user disables, disabled invalid profiles, launch-ready-only defaults, manager sync exclusion, Settings cards, and absence of old Auth Profile names in active code/docs.
- Dependencies: STORY-001, STORY-002, STORY-005, STORY-006
- Coverage: DESIGN-REQ-019, DESIGN-REQ-020
- [NEEDS CLARIFICATION] Which active Temporal histories, if any, must survive the Auth Profile to Provider Profile contract rename cutover?

Acceptance criteria:

- Migration adds or maps auth_state, disabled_reason, first_authenticated_at, last_validated_at, and last_auth_method without making unverified profiles launchable.
- First-party setup stubs are seeded or backfilled for claude_anthropic_default, codex_openai_default, and gemini_google_default in disabled setup-required state.
- Existing launch-ready OAuth and SecretRef profiles are backfilled to connected with appropriate last_auth_method and enabled=true only when currently enabled and not policy-blocked.
- Profiles with missing or invalid credentials are disabled with not_configured or validation_failed state and missing_credentials or auth_invalid reasons.
- Explicit user/admin disables are preserved as disabled_reason=user_disabled and are not overwritten by passive validation.
- Default normalization chooses only launch-ready profiles and clears the runtime default when none exists.
- Auth Profile names are replaced with Provider Profile names, including ManagedAgentProviderProfile, MoonMind.ProviderProfileManager, and managed_agent_provider_profiles, without compatibility aliases for internal contracts.
- The acceptance-test list in the source design is represented in unit, integration, or workflow-boundary tests appropriate to each behavior.

Scope:

- Activation-column migration or equivalent model update
- Setup-stub seeding/backfill
- Connected OAuth and SecretRef profile backfill
- Missing/invalid credential disablement
- User/admin disable preservation
- Default normalization update
- ProviderProfileManager sync update
- Settings UI update
- Auth Profile terminology replacement across docs/code/tests

Out of scope:

- Creating a compatibility alias for old internal contract names
- Changing unrelated secret backend schemas

## Coverage Matrix

- `DESIGN-REQ-001` Provider Profiles are durable execution contracts: STORY-001
- `DESIGN-REQ-002` System boundaries stay separated: STORY-001
- `DESIGN-REQ-003` Support many runtimes, providers, credential classes, and materialization modes: STORY-001
- `DESIGN-REQ-004` Explicit non-goals preserve scope: STORY-001
- `DESIGN-REQ-005` Enabled and launch_ready are distinct: STORY-001
- `DESIGN-REQ-006` Canonical profile and file-template contracts are explicit: STORY-001
- `DESIGN-REQ-007` First-party Settings activation is safe by default: STORY-002
- `DESIGN-REQ-008` User-disabled and policy states are respected: STORY-002
- `DESIGN-REQ-009` OAuth-backed profiles are reusable profile rows: STORY-003
- `DESIGN-REQ-010` API-key setup stores SecretRefs only: STORY-004
- `DESIGN-REQ-011` Requests resolve profiles provider-aware: STORY-005
- `DESIGN-REQ-012` Default provider fallback avoids accidental disabled or arbitrary routing: STORY-005
- `DESIGN-REQ-013` ProviderProfileManager coordinates capacity: STORY-006
- `DESIGN-REQ-014` Waiting and cooldown behavior are durable and observable: STORY-006
- `DESIGN-REQ-015` Runtime materialization is layered and launch-boundary checked: STORY-007
- `DESIGN-REQ-016` Persistence uses provider-aware table and safe defaults: STORY-001
- `DESIGN-REQ-017` Provider-profile security rules prevent credential leakage: STORY-003, STORY-004, STORY-007
- `DESIGN-REQ-018` Runtime/provider examples define concrete first-party and MiniMax mappings: STORY-003, STORY-004, STORY-007
- `DESIGN-REQ-019` Migration preserves activation semantics and defaults: STORY-008
- `DESIGN-REQ-020` Acceptance coverage and terminology complete the desired state: STORY-002, STORY-003, STORY-004, STORY-005, STORY-006, STORY-008

## Dependencies

- `STORY-001` depends on no prior story.
- `STORY-002` depends on STORY-001.
- `STORY-003` depends on STORY-001, STORY-002.
- `STORY-004` depends on STORY-001, STORY-002.
- `STORY-005` depends on STORY-001, STORY-002.
- `STORY-006` depends on STORY-005.
- `STORY-007` depends on STORY-001, STORY-005.
- `STORY-008` depends on STORY-001, STORY-002, STORY-005, STORY-006.

## Out-of-Scope Items

- Secret backend storage, encryption, rotation, and SecretRef schema internals: Owned by docs/Security/SecretsSystem.md; Provider Profiles only reference SecretRefs.
- Browser-terminal OAuth transport and PTY/WebSocket architecture: Owned by docs/ManagedAgents/OAuthTerminal.md; Provider Profiles only persist verified launch-target metadata.
- Universal provider API normalization or runtime strategy replacement: The design explicitly preserves runtime-specific strategies and avoids forcing providers into one logical API.
- Pricing or billing attribution: Provider Profiles carry selection and launch policy; billing is not solved by this design.

## Coverage Gate

PASS - every major design point is owned by at least one story.
