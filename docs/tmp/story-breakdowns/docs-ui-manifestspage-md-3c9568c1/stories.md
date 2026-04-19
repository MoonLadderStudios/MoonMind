# Manifests Page Story Breakdown

- Source design: `docs/UI/ManifestsPage.md`
- Original source document reference: `docs/UI/ManifestsPage.md`
- Story extraction date: 2026-04-19T04:59:50Z
- Requested output mode: `jira`
- Coverage gate: PASS - every major design point is owned by at least one story.

## Design Summary

ManifestsPage.md defines a single canonical dashboard destination at /tasks/manifests that combines manifest submission and recent manifest run monitoring. The design removes the separate Manifest Submit tab, keeps registry and inline YAML execution on the same page as run history, preserves compact advanced controls and in-place submit feedback, and constrains phase 1 to routing/composition changes without backend execution redesign, raw secret entry, run-detail redesign, or a full saved-manifest registry UI.

## Coverage Points

- **DESIGN-REQ-001 - Single manifest destination** (requirement, Summary, Goals, Canonical route and navigation, Acceptance criteria): The dashboard should expose Manifests as the only top-level destination for manifest operations at /tasks/manifests.
- **DESIGN-REQ-002 - Remove or redirect Manifest Submit navigation** (migration, Canonical route and navigation, Routing and cleanup): The separate Manifest Submit tab/nav item is removed, and legacy submit routes redirect to /tasks/manifests, optionally focusing the form.
- **DESIGN-REQ-003 - Unified vertical page structure** (requirement, Page structure, Recommended layout): The page has a header, a Run Manifest card, and a Recent Runs table in one vertical flow, with space for a future saved-manifests section.
- **DESIGN-REQ-004 - Header content and actions** (ui-surface, Header): The page title is Manifests with concise help text, optional refresh/latest-run actions, and no separate New Manifest Run button when the form is visible.
- **DESIGN-REQ-005 - Inline Run Manifest form replaces submit tab** (requirement, Run Manifest card, Why inline form is preferred over a drawer/modal): The old Manifest Submit surface is mounted inline above run history so launching and monitoring stay connected.
- **DESIGN-REQ-006 - Registry and inline YAML source modes** (requirement, Goals, Source type toggle, Fields, Submit from registry mode, Submit from inline YAML mode): Users can submit either a registry manifest or inline YAML, and switching modes preserves draft values independently for each mode.
- **DESIGN-REQ-007 - Form fields and advanced options** (state-model, Fields, Primary action, Secondary actions, Default load, Responsive behavior): The form supports manifest name or YAML, action, collapsed advanced controls for dry run, force full sync, max docs, and priority, with a Run Manifest primary button disabled during submission.
- **DESIGN-REQ-008 - Validation and secret guardrails** (security, Validation rules, Non-goals, Acceptance criteria): Required fields and max docs are validated, YAML errors are shown inline when practical, action is required, and raw secret entry is not introduced into submitted content or helper fields.
- **DESIGN-REQ-009 - In-place submission success behavior** (requirement, Submission behavior, Interaction model, Success path cleanup, Acceptance criteria): Successful submits stay on /tasks/manifests, show success feedback, prepend the new run in Recent Runs, and optionally highlight/scroll or offer details without legacy route navigation.
- **DESIGN-REQ-010 - Submission failure handling** (resilience, Failure handling): Failed submits keep the user on the page, preserve entered data, show inline form errors, and may also show a toast.
- **DESIGN-REQ-011 - Recent manifest runs data source** (integration, Recent Runs section, Data source, API and data notes): Recent Runs uses /api/executions?entry=manifest&limit=200 for phase 1.
- **DESIGN-REQ-012 - Manifest-aware run table answers key questions** (ui-surface, Table purpose, Recommended columns, Manifest-specific status detail): The run table shows run identity, manifest/action, status, current stage when available, started/duration details, optional trigger identity, and actions such as view details or cancel.
- **DESIGN-REQ-013 - Lightweight run filtering and empty state** (requirement, Filters, Empty state): The page offers lightweight status, manifest name, free-text, and optional action filtering, plus an empty state that points users back to the form.
- **DESIGN-REQ-014 - Responsive layout behavior** (ui-surface, Recommended layout, Responsive behavior): Desktop keeps the form above the table with the primary action visible; tablet/mobile stack controls and use compact rows/cards while preserving form-first, history-second order.
- **DESIGN-REQ-015 - Accessibility requirements** (accessibility, Accessibility): The source toggle is keyboard reachable and screen-reader labeled; validation is associated and announced; the YAML editor has a textarea fallback; toasts are not the only signal; row actions have clear labels.
- **DESIGN-REQ-016 - Reuse existing APIs and execution model** (constraint, API and data notes, No required backend redesign, Non-goals): Phase 1 reuses existing manifest submission and history behavior and does not change the manifest execution model, stages, or run detail pages.
- **DESIGN-REQ-017 - Future saved-manifest registry compatibility** (non-goal, Goals, Optional future section: Saved Manifests, API and data notes, Acceptance criteria): A full saved-manifest registry UI is not required in phase 1, but the layout should leave room for future /api/manifests-backed autocomplete, details, runs, or a feature-gated catalog section without another top-level tab.
- **DESIGN-REQ-018 - Recommended phased implementation path** (migration, Recommended implementation sequence, Final recommendation): Phase 1 builds the unified page, moves the existing submit form, keeps recent runs below, removes extra nav, and prepends new runs; later phases may enhance autocomplete, YAML hints, row highlighting, deep links, and saved manifests.

## Ordered Story Candidates

### STORY-001: Unify manifest route and dashboard navigation

- Short name: `manifest-route-navigation`
- Source reference: `docs/UI/ManifestsPage.md` (Summary; Goals; Canonical route and navigation; Page structure; Header; Routing and cleanup; Optional future section: Saved Manifests; Recommended implementation sequence)
- Why: The current split between Manifests and Manifest Submit creates avoidable tab switching and overstates the amount of manifest surface area.
- Description: As a dashboard user, I want Manifests to be the only top-level manifest destination so launching and monitoring manifest work starts from one predictable page.
- Independent test: Open the dashboard navigation and legacy submit URL in a frontend/router test; assert only Manifests appears as the manifest destination, /tasks/manifests renders the unified shell, and legacy submit route(s) redirect to the canonical route without creating a specs directory or spec.md artifact.
- Dependencies: None
- Needs clarification: None
- Scope:
  - Make /tasks/manifests the canonical page for manifest operations.
  - Remove the separate Manifest Submit top-level nav item.
  - Redirect legacy manifest submit route(s) to /tasks/manifests, optionally focusing or scrolling to the run form.
  - Render the Manifests header with concise help text and optional refresh/latest-run actions where existing data supports them.
  - Establish the page shell with Run Manifest above Recent Runs and room for a future saved-manifests section.
- Out of scope:
  - Building a full saved-manifest registry browser.
  - Changing manifest execution stages or run detail pages.
  - Implementing the complete submit form internals beyond mounting its page slot.
- Acceptance criteria:
  - The main dashboard navigation contains one manifest destination labeled Manifests.
  - /tasks/manifests is the canonical route for manifest operations.
  - Legacy Manifest Submit route(s) redirect to /tasks/manifests and may carry a focus or anchor hint for the run form.
  - The page header title is Manifests with concise help text equivalent to running and monitoring manifest executions in one place.
  - No separate New Manifest Run header button is introduced when the inline form is visible on the page.
  - The page shell orders Run Manifest before Recent Runs and leaves an extension point for a future saved-manifests section without adding a new top-level tab.
- Requirements:
  - Canonicalize manifest routing around /tasks/manifests.
  - Remove or redirect the old Manifest Submit navigation surface.
  - Render a unified page shell that keeps submission and history together.
  - Preserve future registry space without implementing the registry browser in phase 1.
- Source design coverage:
  - DESIGN-REQ-001: owns the single destination requirement.
  - DESIGN-REQ-002: owns nav removal and legacy route redirects.
  - DESIGN-REQ-003: owns the initial vertical page structure.
  - DESIGN-REQ-004: owns header behavior and actions.
  - DESIGN-REQ-017: keeps future saved-manifest support as page capacity instead of another tab.
  - DESIGN-REQ-018: covers phase 1 route/nav cleanup sequencing.
- Assumptions:
  - Legacy submit routes can redirect through the existing dashboard routing layer.
- Jira handoff: Unify manifest route and dashboard navigation. Build this as a one-story Moon Spec slice with failing router/navigation tests before production code, preserve the original design input, and verify the owned design requirements explicitly.

### STORY-002: Run registry or inline YAML manifests in place

- Short name: `manifest-inline-submit`
- Source reference: `docs/UI/ManifestsPage.md` (Run Manifest card; Source type toggle; Fields; Validation rules; Submission behavior; Interaction model; Failure handling; API and data notes; Why inline form is preferred over a drawer/modal)
- Why: The submit UI is the functional replacement for the old Manifest Submit tab and must keep user input, validation, and success or failure feedback anchored on the canonical page.
- Description: As a dashboard user, I want to run either a registry manifest or inline YAML from the Manifests page so I can start work without leaving the run history context.
- Independent test: Use frontend form tests and submit-handler tests to exercise registry and inline YAML modes, mode switching, required-field errors, advanced option validation, raw-secret rejection, loading state, success staying on /tasks/manifests, and failed submit preserving entered values.
- Dependencies: STORY-001
- Needs clarification: None
- Scope:
  - Mount the manifest submission behavior in a compact Run Manifest card on /tasks/manifests.
  - Support Registry Manifest and Inline YAML modes with independent draft preservation when switching modes.
  - Expose manifest name, inline YAML, action, and collapsed advanced controls for dry run, force full sync, max docs, and priority using existing backend-supported values.
  - Validate required source fields, selected action, positive max docs, and YAML errors before submit whenever practical.
  - Reject raw secret entry patterns in submitted content or helper fields and allow only env/Vault-style references.
  - Keep users on /tasks/manifests after submit, disable the button while submitting, show success feedback, and preserve data plus inline errors on failure.
- Out of scope:
  - Designing a new manifest execution API.
  - Adding a full saved-manifest catalog UI.
  - Changing manifest run detail pages.
  - Adding advanced payload preview or copy controls unless there is existing dashboard precedent.
- Acceptance criteria:
  - The Run Manifest card is visible by default on /tasks/manifests.
  - Registry Manifest mode requires a manifest name and uses autocomplete from /api/manifests when available with free-text fallback otherwise.
  - Inline YAML mode requires non-empty YAML and shows YAML validation errors inline before submit whenever practical.
  - Switching between Registry Manifest and Inline YAML preserves draft values for each mode independently during the same session.
  - Action is required and is limited to existing backend-supported action values.
  - Advanced options are collapsed by default and include dry run, force full sync, max docs, and priority where those controls are supported.
  - max docs must be a positive integer when provided.
  - Raw secret entry is not accepted directly in submitted content or helper fields.
  - Run Manifest is the primary button label and the button is disabled while submission is in progress.
  - Successful submit stays on /tasks/manifests and does not navigate to a legacy queue or submit route.
  - Failed submit preserves entered data and shows an inline form error with toast feedback when appropriate.
- Requirements:
  - Provide compact inline manifest submission on the canonical Manifests page.
  - Preserve registry and inline YAML submission support.
  - Validate source, action, YAML, and advanced fields before submission where practical.
  - Protect against raw secret entry in the UI.
  - Keep success and failure feedback in-page.
- Source design coverage:
  - DESIGN-REQ-005: owns the inline Run Manifest form replacement.
  - DESIGN-REQ-006: owns registry and inline YAML source modes plus draft preservation.
  - DESIGN-REQ-007: owns fields, advanced options, and loading state.
  - DESIGN-REQ-008: owns validation and raw secret guardrails.
  - DESIGN-REQ-009: owns in-page success path from the form side.
  - DESIGN-REQ-010: owns submit failure behavior.
  - DESIGN-REQ-016: reuses the current manifest submission handler instead of redesigning backend execution.
  - DESIGN-REQ-018: covers phase 1 form migration and later enhancement boundaries.
- Assumptions:
  - The existing manifest submission handler already exposes the supported action and priority values or the current UI already knows them.
- Jira handoff: Run registry or inline YAML manifests in place. Build this as a one-story Moon Spec slice with failing component and submit-handler tests before production code, preserve the original design input, and verify the owned design requirements explicitly.

### STORY-003: Show recent manifest runs beside submission

- Short name: `manifest-recent-runs`
- Source reference: `docs/UI/ManifestsPage.md` (Recent Runs section; Data source; Table purpose; Recommended columns; Manifest-specific status detail; Filters; Empty state; Submission behavior; API and data notes)
- Why: The Recent Runs section is the other half of the unified workflow and replaces the old history-only Manifests tab content.
- Description: As a dashboard user, I want recent manifest executions visible on the same page as the submit form so I can immediately confirm whether a submitted run started, where it is, and how to inspect it.
- Independent test: Mock /api/executions?entry=manifest&limit=200 in frontend tests and assert recent runs load under the form, staged status text appears when supplied, filters narrow the list, the empty state points back to the form, and a successful submit prepends or highlights the newly created run.
- Dependencies: STORY-001
- Needs clarification: None
- Scope:
  - Load recent manifest runs from /api/executions?entry=manifest&limit=200 under the Run Manifest card.
  - Show manifest-aware run details such as run ID, manifest, action, status, current stage, started time, duration, optional trigger identity, and actions.
  - Surface staged manifest status details when available, including validate, plan, fetch, transform, embed, upsert, and finalize.
  - Provide lightweight status, manifest name, free-text, and optional action filtering if supported by the data model.
  - Render an empty state that directs users to run a registry manifest or submit inline YAML above.
  - After successful submit, insert the new run at the top and optionally highlight, scroll, or offer View run details without leaving /tasks/manifests.
- Out of scope:
  - Redesigning execution detail pages.
  - Building a heavy filter builder.
  - Creating new backend history endpoints as a phase 1 requirement.
  - Changing manifest workflow stages.
- Acceptance criteria:
  - Recent Runs loads immediately below the Run Manifest card using /api/executions?entry=manifest&limit=200.
  - Users can answer whether the run started, whether it is still running, which stage it is in, whether it succeeded or failed, and how to open details or logs.
  - Run ID links to the run detail page.
  - Manifest, action, status, stage, started time, duration, optional triggered-by identity, and actions are shown when available.
  - Running or failed manifest runs show current stage inline when available, such as Running · fetch or Failed · transform.
  - Lightweight filtering supports status, manifest name, free-text search, and action only if action filtering is already supported in the data model.
  - The empty state reads equivalently to No manifest runs yet and points users back to registry or inline YAML submission above.
  - Successful submit updates the current Recent Runs list in place by prepending the new run and may highlight, scroll, or offer View run details.
- Requirements:
  - Display recent manifest execution history on the canonical page.
  - Use the existing execution history endpoint for manifest runs.
  - Expose manifest-specific stage context and detail links.
  - Keep filtering lightweight and phase-appropriate.
  - Connect submit success to immediate run visibility.
- Source design coverage:
  - DESIGN-REQ-009: owns in-page success path from the run list side.
  - DESIGN-REQ-011: owns the recent-runs API data source.
  - DESIGN-REQ-012: owns table purpose, columns, actions, and staged status detail.
  - DESIGN-REQ-013: owns lightweight filtering and empty state.
  - DESIGN-REQ-016: keeps phase 1 on existing history/execution behavior.
  - DESIGN-REQ-018: covers phase 1 recent-runs composition and later row-highlighting improvements.
- Assumptions:
  - The execution history response already contains enough manifest metadata to show the recommended columns when available.
- Jira handoff: Show recent manifest runs beside submission. Build this as a one-story Moon Spec slice with failing history-table and submit-refresh tests before production code, preserve the original design input, and verify the owned design requirements explicitly.

### STORY-004: Make the unified Manifests page responsive and accessible

- Short name: `manifest-page-a11y-responsive`
- Source reference: `docs/UI/ManifestsPage.md` (Responsive behavior; Accessibility; Non-goals; Optional future section: Saved Manifests; API and data notes; Recommended implementation sequence)
- Why: The design includes explicit responsive, accessibility, non-goal, and future-registry constraints that need an owning story so the implementation does not treat them as optional polish.
- Description: As a dashboard user on any device or assistive technology, I want the unified Manifests page to remain usable, understandable, and ready for future registry enhancements without reintroducing navigation sprawl.
- Independent test: Run responsive component or browser tests across desktop and mobile widths plus accessibility-focused tests for keyboard source toggles, associated validation errors, textarea fallback, non-toast success/error state, and named row actions; assert no extra top-level manifest destination is added for future registry support.
- Dependencies: STORY-001, STORY-002, STORY-003
- Needs clarification: None
- Scope:
  - Implement responsive behavior for desktop, tablet, and mobile while preserving form-first and history-second order.
  - Stack form controls vertically on smaller screens and render recent runs as compact rows or cards when the table would not fit.
  - Keep the submit action visible or pinned within the card section when the form becomes long.
  - Ensure source toggles, validation, YAML editor fallback, toasts, and row actions meet the accessibility requirements.
  - Confirm the implementation does not introduce raw secret fields, a full registry browser, new run detail designs, or backend execution/stage changes.
  - Leave future /api/manifests registry autocomplete/details/runs support as progressive enhancement inside the same Manifests destination.
- Out of scope:
  - Implementing phase 2 autocomplete and YAML hint improvements unless they already exist as small progressive enhancements.
  - Implementing phase 3 Saved Manifests catalog/browser.
  - Creating another top-level manifest-related dashboard route.
- Acceptance criteria:
  - Desktop keeps the run form above recent runs and keeps the primary action visible without requiring advanced options expansion.
  - Tablet and mobile stack form controls vertically and render run history as compact rows or cards when needed.
  - The visual order remains form first, history second on all supported viewport sizes.
  - The source type toggle is keyboard reachable and screen-reader labeled.
  - Validation errors are announced and associated with their fields.
  - The YAML editor provides an accessible textarea fallback.
  - Toasts are not the only signal of submit success or failure.
  - Run table/card actions have clear accessible labels and are not icon-only controls without names.
  - No raw secret entry UI, full manifest registry UI, run detail redesign, execution model change, or stage change is introduced as part of phase 1.
  - Future saved-manifest registry support remains compatible with the same /tasks/manifests page rather than requiring another top-level tab.
- Requirements:
  - Make the unified page responsive across desktop, tablet, and mobile.
  - Satisfy explicit accessibility requirements for toggles, forms, editor fallback, toasts, and row actions.
  - Enforce phase 1 non-goals and backend-boundary constraints.
  - Preserve same-page future registry compatibility.
- Source design coverage:
  - DESIGN-REQ-003: reinforces page structure across responsive layouts.
  - DESIGN-REQ-008: reinforces no raw secret entry as an implementation guardrail.
  - DESIGN-REQ-014: owns responsive behavior.
  - DESIGN-REQ-015: owns accessibility requirements.
  - DESIGN-REQ-016: owns no backend redesign, run-detail redesign, or stage changes.
  - DESIGN-REQ-017: owns future saved-manifest compatibility without phase 1 registry UI.
  - DESIGN-REQ-018: keeps phase 2 and phase 3 work separated from the phase 1 merge.
- Assumptions:
  - The existing frontend test stack can cover responsive rendering and accessibility attributes for the Manifests page.
- Jira handoff: Make the unified Manifests page responsive and accessible. Build this as a one-story Moon Spec slice with failing responsive and accessibility tests before production code, preserve the original design input, and verify the owned design requirements explicitly.

## Coverage Matrix

- **DESIGN-REQ-001** -> STORY-001
- **DESIGN-REQ-002** -> STORY-001
- **DESIGN-REQ-003** -> STORY-001, STORY-004
- **DESIGN-REQ-004** -> STORY-001
- **DESIGN-REQ-005** -> STORY-002
- **DESIGN-REQ-006** -> STORY-002
- **DESIGN-REQ-007** -> STORY-002
- **DESIGN-REQ-008** -> STORY-002, STORY-004
- **DESIGN-REQ-009** -> STORY-002, STORY-003
- **DESIGN-REQ-010** -> STORY-002
- **DESIGN-REQ-011** -> STORY-003
- **DESIGN-REQ-012** -> STORY-003
- **DESIGN-REQ-013** -> STORY-003
- **DESIGN-REQ-014** -> STORY-004
- **DESIGN-REQ-015** -> STORY-004
- **DESIGN-REQ-016** -> STORY-002, STORY-003, STORY-004
- **DESIGN-REQ-017** -> STORY-001, STORY-004
- **DESIGN-REQ-018** -> STORY-001, STORY-002, STORY-003, STORY-004

## Dependencies

- **STORY-001** depends on: None
- **STORY-002** depends on: STORY-001
- **STORY-003** depends on: STORY-001
- **STORY-004** depends on: STORY-001, STORY-002, STORY-003

## Out Of Scope

- A full saved-manifest registry UI is deferred because phase 1 only needs same-page launch and monitoring while leaving room for future `/api/manifests` support.
- Run detail pages are not redesigned because the declarative design keeps the change focused on routing and composition.
- The manifest execution model and stages are not changed; the page reuses existing submit and history behavior.
- Raw secret entry is not introduced; submitted content should use env/Vault references rather than direct secrets.
- Heavy filtering and advanced registry/catalog browsing are deferred to later phases.

## Coverage Gate

PASS - every major design point is owned by at least one story.
