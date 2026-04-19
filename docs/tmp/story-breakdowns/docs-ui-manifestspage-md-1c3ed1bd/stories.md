# Manifests Page Story Breakdown

Source design: `docs/UI/ManifestsPage.md`
Story extraction date: 2026-04-19T19:55:45Z
Requested output mode: jira

Coverage gate result:

```text
PASS - every major design point is owned by at least one story.
```

## Design Summary

`docs/UI/ManifestsPage.md` defines a desired-state dashboard consolidation: `/tasks/manifests` becomes the only manifest destination, with a compact **Run Manifest** form above **Recent Runs**. The page must preserve registry and inline YAML submission, reuse existing submit and history APIs, keep users anchored on the same page after submit, remove legacy **Manifest Submit** navigation, stay responsive and accessible, avoid raw secret entry, and leave room for future saved manifest registry support without making it part of phase 1.

## Coverage Points

| ID | Type | Source Section | Title | Design Point |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | requirement | Summary; Goals; Canonical route and navigation | Single canonical manifest destination | Replace separate Manifests and Manifest Submit tabs with one canonical Manifests page at /tasks/manifests. |
| DESIGN-REQ-002 | requirement | Summary; Why this change; Goals | Launch and monitor in one flow | Keep manifest submission and recent run history together so users can submit and immediately verify the resulting run without tab switching. |
| DESIGN-REQ-003 | requirement | Goals; Run Manifest card; Interaction model | Compact default submit surface | Keep the Run Manifest form visible and compact by default, with advanced options collapsed. |
| DESIGN-REQ-004 | requirement | Goals; Source type toggle; Fields; Submit from registry mode; Submit from inline YAML mode | Registry and inline YAML support | Support both registry manifest execution and inline YAML submission from the unified page. |
| DESIGN-REQ-005 | state-model | Run Manifest card; Source type toggle | Mode-specific draft preservation | Switching between Registry Manifest and Inline YAML modes should preserve entered values for each mode independently during the same session. |
| DESIGN-REQ-006 | requirement | Fields; Primary action; Secondary actions | Supported fields and advanced runtime controls | Expose required source/action fields and optional advanced controls for dry run, force full sync, max docs, and priority without making advanced controls prominent by default. |
| DESIGN-REQ-007 | security | Validation rules; Non-goals; Acceptance criteria | Client-side validation and secret guardrail | Validate required manifest inputs, positive max docs, action selection, and practical YAML errors while preventing raw secret entry in UI content or helper fields. |
| DESIGN-REQ-008 | requirement | Submission behavior; Interaction model; Success path cleanup | In-place success behavior | After successful submit, stay on /tasks/manifests, show success feedback, prepend or refresh the new run in Recent Runs, and avoid default navigation to legacy tabs or queue routes. |
| DESIGN-REQ-009 | requirement | Failure handling | Submission failure preserves context | When submission fails, keep the user on the page, preserve entered data, show inline form error feedback, and avoid automatic form clearing. |
| DESIGN-REQ-010 | integration | Recent Runs section; Data source; API and data notes | Recent manifest runs data source | Load recent manifest run history from /api/executions?entry=manifest&limit=200. |
| DESIGN-REQ-011 | requirement | Table purpose; Recommended columns; Manifest-specific status detail | Manifest-aware run table answers operator questions | Show enough run metadata for users to know whether a run started, whether it is running, its current stage, result, duration, and how to open details or logs. |
| DESIGN-REQ-012 | requirement | Filters; Empty state | Lightweight run filtering and empty state | Provide lightweight status, manifest name, free-text, and optional action filters, plus an empty state that points directly back to the run form. |
| DESIGN-REQ-013 | requirement | Page structure; Recommended layout; Responsive behavior | Responsive single-column workflow | Keep a clear form-first, history-second vertical flow on desktop and mobile, stacking controls and converting run history to compact cards or rows where needed. |
| DESIGN-REQ-014 | requirement | Accessibility | Accessible form, feedback, and table actions | Make the source toggle keyboard reachable and labeled, associate validation with fields, provide textarea fallback for YAML, ensure toasts are not the only signal, and label row actions clearly. |
| DESIGN-REQ-015 | migration | Canonical route and navigation; Routing and cleanup; Acceptance criteria | Legacy route and navigation cleanup | Remove the dedicated Manifest Submit navigation item and redirect old submit routes to /tasks/manifests, optionally focusing the run form. |
| DESIGN-REQ-016 | constraint | Goals; Optional future section: Saved Manifests; API and data notes | Future saved manifest registry compatibility | Leave room for future saved manifest registry support and autocomplete from /api/manifests without requiring a full registry UI or another top-level tab in phase 1. |
| DESIGN-REQ-017 | non-goal | Non-goals; API and data notes | No backend execution redesign | Do not change manifest execution model, stages, or run detail pages; reuse the current manifest submission API/handler and recent execution history endpoint. |
| DESIGN-REQ-018 | constraint | Why inline form is preferred over a drawer/modal | Inline form preferred over drawer or modal | Prefer an inline form above the runs table to keep submit and monitoring visible in one flow; drawer is acceptable only as a secondary choice launched from the same page. |

## Ordered Story Candidates

### STORY-001: Unify manifest route and navigation

Short name: `manifest-route-nav`

Source reference: `docs/UI/ManifestsPage.md` (Summary, Why this change, Goals, Canonical route and navigation, Routing and cleanup, Acceptance criteria)

Why: As a dashboard user, I want Manifests to be the only top-level destination for manifest work so I can launch and monitor manifest runs without choosing between overlapping tabs.

Scope:
- Make /tasks/manifests the canonical manifest page route.
- Keep only the Manifests nav/tab entry in the main dashboard navigation.
- Redirect legacy manifest submit route(s) to /tasks/manifests, optionally with focus state for the run form.
- Preserve room in the page model for future saved manifest registry support without adding a second top-level destination.

Out of scope:
- A full saved manifest registry/catalog browser.
- Run detail page redesign.
- Manifest execution model or stage changes.

Independent test:
Render dashboard navigation and route handling in isolation, then verify only Manifests appears for manifest work and legacy submit URLs redirect to /tasks/manifests without creating any new spec or backend execution behavior.

Acceptance criteria:
- Given the dashboard navigation loads, when manifest destinations are listed, then only Manifests appears as a top-level manifest nav item.
- Given a user opens /tasks/manifests, then the unified Manifests page is rendered.
- Given a user opens an old Manifest Submit route, then they are redirected to /tasks/manifests and the route can focus or scroll to the run form when supported.
- Given phase 1 ships, then no full saved manifest registry UI or additional manifest tab is required.
- Given existing run details are opened from manifest history, then this story does not redesign those detail pages or alter manifest execution stages.

Requirements:
- The dashboard must expose /tasks/manifests as the canonical manifest destination.
- The dedicated Manifest Submit navigation entry must be removed or converted into a redirect path.
- Legacy submit routes must not remain as separate user-facing destinations.
- The route/page structure must allow future registry support without requiring another top-level tab.

Dependencies: None.

Risks or open questions:
- None.

Owned coverage: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017.

### STORY-002: Run registry or inline manifests from the Manifests page

Short name: `manifest-run-form`

Source reference: `docs/UI/ManifestsPage.md` (Page structure, Run Manifest card, Source type toggle, Fields, Validation rules, Responsive behavior, Accessibility, Why inline form is preferred over a drawer/modal)

Why: As a dashboard user, I want a compact Run Manifest form on the Manifests page that supports registry names and inline YAML so I can start either kind of manifest run from the same context.

Scope:
- Embed the existing manifest submit behavior as a Run Manifest card on /tasks/manifests.
- Support Registry Manifest and Inline YAML modes with independently preserved draft values.
- Expose required manifest source and action inputs plus collapsed advanced runtime controls.
- Validate required fields, positive max docs, action selection, YAML errors where practical, and raw-secret guardrails.
- Keep the form responsive and accessible with keyboard-reachable mode controls and textarea fallback for YAML.

Out of scope:
- Changing backend-supported action values.
- Adding a full saved manifest catalog browser.
- Submitting raw secret values through new helper fields.

Independent test:
Mount the Manifests page with the run history service mocked, exercise Registry Manifest and Inline YAML modes, and verify draft preservation, validation errors, advanced option defaults, responsive markup hooks, and accessible labels before any submit call succeeds.

Acceptance criteria:
- Given /tasks/manifests loads, then a Run Manifest card is visible above Recent Runs and advanced options are collapsed by default.
- Given Registry Manifest mode is active, then manifest name and action are required and inline YAML is not required.
- Given Inline YAML mode is active, then non-empty YAML and action are required and registry manifest name is not required.
- Given a user switches between source modes, then values entered in each mode are preserved independently for the current session.
- Given max docs is provided, then non-positive or non-integer values are rejected before submit.
- Given submitted content or helper fields contain raw secret-style entries, then the UI rejects them or requires env/Vault references instead.
- Given the page is used with keyboard or assistive technology, then the source toggle, validation messages, YAML fallback, and primary action have accessible labels and field associations.
- Given the viewport narrows, then form controls stack in a clear form-first flow without hiding the primary Run Manifest action behind advanced options.

Requirements:
- The Run Manifest card must be available directly on /tasks/manifests.
- Registry Manifest mode must accept a required manifest name and existing supported action values.
- Inline YAML mode must accept required YAML content and existing supported action values.
- Advanced options must include supported dry run, force full sync, max docs, and priority controls where those controls already exist or are backend-supported.
- The primary action label must be Run Manifest and must disable while submission is in progress.
- The implementation must not introduce raw secret entry into the UI.
- Autocomplete from /api/manifests may be added only as progressive enhancement with free-text fallback.

Dependencies: STORY-001.

Risks or open questions:
- None.

Owned coverage: DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-016, DESIGN-REQ-018.

### STORY-003: Keep manifest submission feedback in place

Short name: `manifest-submit-feedback`

Source reference: `docs/UI/ManifestsPage.md` (Submission behavior, Interaction model, Failure handling, Success path cleanup, Accessibility, Acceptance criteria)

Why: As a dashboard user, I want manifest submit success or failure to update the current Manifests page in place so I can see what happened without losing form context or being sent to another route.

Scope:
- Reuse the current manifest submission API/handler from the Manifests page.
- Keep successful submissions on /tasks/manifests with a success toast and a non-toast page-level signal such as the new row, highlight, or details link.
- Insert or refresh the submitted run at the top of Recent Runs and optionally highlight or scroll to it.
- Preserve entered data and show inline errors when submission fails.
- Remove legacy success redirects to separate tabs or queue pages for the default submit path.

Out of scope:
- Backend redesign of manifest submission.
- Guaranteed deep-linking or row highlighting if the returned submit response does not identify the run.
- Run detail page changes beyond offering an existing details link.

Independent test:
Mock successful and failed submit responses from the existing manifest submit handler, then assert the route stays /tasks/manifests, loading state toggles correctly, success updates Recent Runs, failure preserves inputs, and no legacy redirect occurs.

Acceptance criteria:
- Given a valid registry manifest submission succeeds, then the user remains on /tasks/manifests and receives success feedback.
- Given a valid inline YAML submission succeeds, then the user remains on /tasks/manifests and receives the same in-place success behavior.
- Given the submit response includes a run identifier or execution record, then the new run appears at the top of Recent Runs or the list refreshes to show it.
- Given submission is in progress, then the Run Manifest button is disabled and communicates loading state.
- Given submission fails, then the form data remains intact and an inline error is shown, with toast feedback allowed only as an additional signal.
- Given legacy code previously redirected after submit, then the default success path no longer sends users to a separate tab or queue route.

Requirements:
- The page must use the existing manifest submission API/handler.
- Successful submission must update the current page in place.
- Failure handling must preserve form state and keep the user on /tasks/manifests.
- Success and error feedback must not rely on toast messages as the only user-visible signal.

Dependencies: STORY-002.

Risks or open questions:
- None.

Owned coverage: DESIGN-REQ-002, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-014, DESIGN-REQ-017.

### STORY-004: Show recent manifest runs below the run form

Short name: `manifest-recent-runs`

Source reference: `docs/UI/ManifestsPage.md` (Recent Runs section, Data source, Table purpose, Recommended columns, Manifest-specific status detail, Filters, Empty state, Responsive behavior, Accessibility)

Why: As a dashboard user, I want recent manifest runs visible below the Run Manifest card so I can immediately check start state, current stage, result, timing, and details for manifest executions.

Scope:
- Load recent manifest execution history from /api/executions?entry=manifest&limit=200.
- Render a Recent Runs table or responsive compact rows/cards below the Run Manifest form.
- Show manifest-aware run metadata including run ID/details link, manifest label, action, status, stage, started time, duration, triggered by where available, and actions supported by existing data.
- Surface staged workflow status such as Running - fetch when stage data is available.
- Provide lightweight status, manifest name, free-text, and optional action filtering where supported.
- Show an empty state that directs the user back to the form.

Out of scope:
- A heavy filter builder.
- New backend history APIs required for phase 1.
- Changing existing manifest workflow stages or run detail pages.

Independent test:
Mock /api/executions?entry=manifest&limit=200 with empty, running, succeeded, failed, and staged manifest runs, then verify table/card rendering, filters, details links, stage labels, empty state, and responsive accessibility behavior.

Acceptance criteria:
- Given /tasks/manifests loads, then Recent Runs requests /api/executions?entry=manifest&limit=200.
- Given manifest runs exist, then the history surface shows run ID/details link, manifest label, action, status, current stage when available, started time, duration when available, and supported row actions.
- Given a run is active and stage data is available, then the status display includes manifest-specific stage detail such as Running - fetch.
- Given status, manifest name, or free-text filters are used, then the visible run list updates without a heavy filter-builder flow.
- Given no manifest runs exist, then the empty state says no manifest runs exist and points users to run a registry manifest or submit inline YAML above.
- Given the viewport is narrow, then recent runs remain readable as compact cards or stacked rows with clear action labels.
- Given row actions are icon-based in implementation, then they include accessible names.

Requirements:
- Recent Runs must appear below the Run Manifest card on the same page.
- The history request must use the existing manifest execution endpoint for phase 1.
- The history view must help answer whether the run started, whether it is still running, what stage it is in, whether it succeeded or failed, and how to open details/logs.
- Filters must remain lightweight and bounded to status, manifest name, search, and optional action support.
- The implementation must not require a backend redesign or new manifest-centric history API for phase 1.

Dependencies: STORY-001.

Risks or open questions:
- None.

Owned coverage: DESIGN-REQ-002, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-017.


## Coverage Matrix

| Design Requirement | Owning Stories |
| --- | --- |
| DESIGN-REQ-001 | STORY-001 |
| DESIGN-REQ-002 | STORY-001, STORY-003, STORY-004 |
| DESIGN-REQ-003 | STORY-002 |
| DESIGN-REQ-004 | STORY-002 |
| DESIGN-REQ-005 | STORY-002 |
| DESIGN-REQ-006 | STORY-002 |
| DESIGN-REQ-007 | STORY-002 |
| DESIGN-REQ-008 | STORY-003 |
| DESIGN-REQ-009 | STORY-003 |
| DESIGN-REQ-010 | STORY-004 |
| DESIGN-REQ-011 | STORY-004 |
| DESIGN-REQ-012 | STORY-004 |
| DESIGN-REQ-013 | STORY-002, STORY-004 |
| DESIGN-REQ-014 | STORY-002, STORY-003, STORY-004 |
| DESIGN-REQ-015 | STORY-001 |
| DESIGN-REQ-016 | STORY-001, STORY-002 |
| DESIGN-REQ-017 | STORY-001, STORY-003, STORY-004 |
| DESIGN-REQ-018 | STORY-002 |

## Dependencies

- `STORY-001` has no dependencies and should run first because it establishes the canonical route and navigation destination.
- `STORY-002` depends on `STORY-001` because the embedded run form belongs on the unified page.
- `STORY-003` depends on `STORY-002` because submit feedback behavior requires the page-level run form.
- `STORY-004` depends on `STORY-001` because recent runs can be validated on the canonical page independently of submit behavior.

## Out Of Scope Items And Rationale

- Full saved manifest registry/catalog UI: explicitly deferred by the source design; phase 1 only needs compatibility and optional autocomplete if `/api/manifests` exists.
- Run detail page redesign: the design only requires links/actions to existing details and logs.
- Manifest execution model or stage changes: the UI merge must reuse existing execution behavior and stages.
- Heavy filter builder: the design calls for lightweight filtering only.
- Raw secret entry: explicitly prohibited by the design and must remain outside the UI.
- New backend redesign: phase 1 is primarily route composition, existing submit behavior, and existing history data.

## Recommended First Story

Start `/speckit.specify` with `STORY-001` (`manifest-route-nav`). It creates the canonical page destination and removes the legacy tab split, which gives the later form and run-history stories a stable route to build against.

## Clarifications

No stories contain unresolved `[NEEDS CLARIFICATION]` markers.

## Coverage Gate

```text
PASS - every major design point is owned by at least one story.
```
