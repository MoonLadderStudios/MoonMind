# Manifests Page

## Summary

Replace the separate **Manifests** and **Manifest Submit** tabs with one canonical page at **`/tasks/manifests`**.

The new page keeps the two parts of the same workflow together:

1. **Run a manifest**
2. **See recent manifest runs**

This removes unnecessary tab switching, keeps submit feedback in context, and makes **Manifests** the single destination for launching and monitoring manifest work.

---

## Why this change

Today the manifest flow is split across two tabs:

- **Manifests** = run history
- **Manifest Submit** = create a new run

That split creates avoidable friction:

- users have to switch tabs to perform one workflow
- successful submission can feel disconnected from the resulting run
- the navigation is heavier than the feature set warrants
- the dashboard exposes execution history and submit UI, but not a full manifest catalog, so two tabs overstate the amount of surface area

A unified page is simpler: submit from the top, verify the new run immediately below.

---

## Goals

- Make **Manifests** the only top-level page/tab for manifest operations
- Allow users to launch a manifest run without leaving the run history view
- Keep the submit UI compact by default
- Update the page in place after submit instead of bouncing the user elsewhere
- Preserve support for both:
  - **registry manifest** execution
  - **inline YAML** submission
- Keep the design compatible with a future saved manifest registry section

## Non-goals

- Do not require a full manifest registry UI in phase 1
- Do not redesign run detail pages
- Do not change the existing manifest execution model or stages
- Do not introduce raw secret entry into the UI

---

## Canonical route and navigation

- **Canonical page:** `/tasks/manifests`
- **Navigation label:** `Manifests`
- **Remove:** separate `Manifest Submit` tab/nav item
- **Legacy submit route(s):** redirect to `/tasks/manifests` and optionally open/scroll to the run form

This should be the only manifest destination in the main dashboard navigation.

---

## Page structure

The page should have two primary sections in a single vertical flow:

1. **Run Manifest** card
2. **Recent Runs** table

A future third section can be added without changing the core layout:

3. **Saved Manifests** / registry browser (optional, feature-gated)

### Recommended layout

#### Desktop

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Manifests                                                           │
│ Run a manifest and monitor recent executions in one place.          │
├──────────────────────────────────────────────────────────────────────┤
│ Run Manifest                                                        │
│ [ Registry Manifest | Inline YAML ]                                 │
│                                                                      │
│ Registry mode:                                                       │
│ [ Manifest name.................... ] [ Action v ] [ Run Manifest ] │
│                                                                      │
│ Inline mode:                                                         │
│ [ YAML editor / textarea......................................... ] │
│ [ Action v ]                                      [ Run Manifest ]  │
│                                                                      │
│ [ Advanced options ▸ ]                                               │
├──────────────────────────────────────────────────────────────────────┤
│ Recent Runs                                                         │
│ [status] [manifest] [search] [refresh]                              │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ Run ID │ Manifest │ Action │ Status │ Stage │ Started │ View   │ │
│ └──────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

#### Mobile

```text
Manifests
Run a manifest and monitor recent executions in one place.

[ Run Manifest ]
  [ Registry | Inline YAML ]
  fields stack vertically
  [ Advanced options ▸ ]
  [ Run Manifest ]

[ Recent Runs ]
  filters in a wrap row
  runs shown as compact cards or stacked rows
```

---

## Section details

## 1) Header

**Title:** `Manifests`

**Subtitle:** concise help text, for example:

> Run a manifest and monitor recent executions in one place.

Optional header actions:

- `Refresh`
- `Open latest run` if there is an active or most recent run worth surfacing

Do **not** put a separate `New Manifest Run` button in the header if the form is already visible on the page.

---

## 2) Run Manifest card

This is the replacement for the old **Manifest Submit** tab.

### Design principles

- always on the same page as run history
- compact by default
- advanced options collapsed by default
- preserves draft values when switching between source types during the same session
- clearly distinguishes required inputs from optional runtime controls

### Source type toggle

Top control in the card:

- `Registry Manifest`
- `Inline YAML`

Switching modes should preserve entered values for each mode independently.

### Fields

| Field | Shown when | Required | Notes |
| --- | --- | --- | --- |
| `manifest name` | Registry Manifest | Yes | Autocomplete from `/api/manifests` if available; free-text fallback otherwise |
| `inline YAML` | Inline YAML | Yes | Code editor or textarea with YAML formatting assistance |
| `action` | Both | Yes | Use existing backend-supported action values |
| `dry run` | Advanced | No | Boolean toggle |
| `force full sync` | Advanced | No | Boolean toggle |
| `max docs` | Advanced | No | Positive integer |
| `priority` | Advanced | No | Existing supported priority values |

### Primary action

- Primary button label: **`Run Manifest`**
- Button remains in the card footer or aligned to the main fields
- Disable while submission is in progress

### Secondary actions

Optional:

- `Reset`
- `Copy payload` or `Preview payload` only if there is existing dashboard precedent for advanced submit forms

### Validation rules

- Registry mode requires a manifest name
- Inline mode requires non-empty YAML
- `max docs` must be a valid positive integer if provided
- `action` must be selected
- Raw secrets must not be accepted directly in submitted content or helper fields; only env/Vault references should be used
- YAML validation errors should be shown inline before submit whenever practical

### Submission behavior

On successful submit:

- stay on **`/tasks/manifests`**
- show a success toast
- insert the new run at the top of **Recent Runs**
- optionally highlight or auto-scroll to the newly created row
- optionally offer `View run details`

Do **not** navigate to a different tab or legacy queue route as the default success path.

---

## 3) Recent Runs section

This is the replacement for the old **Manifests** tab content.

### Data source

Use the existing history endpoint:

- **`/api/executions?entry=manifest&limit=200`**

### Table purpose

Users should be able to answer these questions immediately after running a manifest:

- Did the run start?
- Is it still running?
- Which stage is it in?
- Did it succeed or fail?
- How do I open details/logs?

### Recommended columns

| Column | Notes |
| --- | --- |
| `Run ID` | Link to run detail page |
| `Manifest` | Manifest name or inline label |
| `Action` | Submitted action |
| `Status` | Queued / Running / Succeeded / Failed / Cancelled |
| `Stage` | For running jobs, show current stage when available |
| `Started` | Relative + exact timestamp on hover if supported |
| `Duration` | Elapsed or total |
| `Triggered By` | Optional if available |
| `Actions` | View details, cancel if supported |

### Manifest-specific status detail

Manifest runs already have a staged workflow. Where available, surface the current stage inline:

- `validate`
- `plan`
- `fetch`
- `transform`
- `embed`
- `upsert`
- `finalize`

For example:

- `Running · fetch`
- `Failed · transform`

This helps distinguish a generic execution list from a manifest-aware run table.

### Filters

Keep filters lightweight:

- status
- manifest name
- free-text search
- optional action filter if that is already supported in the data model

A heavy filter builder is unnecessary for phase 1.

### Empty state

If no manifest runs exist:

> No manifest runs yet. Start by running a registry manifest or submitting inline YAML above.

The empty state should point directly back to the form.

---

## Optional future section: Saved Manifests

This is **not required** for the first merge, but the page should leave room for it.

Potential future data sources:

- `/api/manifests`
- `/api/manifests/{name}`
- `/api/manifests/{name}/runs`

When added later, this can appear as:

- a compact sidebar on desktop, or
- a section below Recent Runs, or
- a feature-gated panel that powers manifest name autocomplete and quick launch

For phase 1, it is enough to support autocomplete or lookup if the endpoint exists. A full catalog browser can wait.

---

## Interaction model

### Default load

- Page opens to **Manifests**
- **Run Manifest** card is visible
- Advanced options are collapsed
- Recent runs load immediately below

### Submit from registry mode

1. User enters/selects manifest name
2. User chooses action
3. Optional advanced options expanded if needed
4. User clicks **Run Manifest**
5. Button enters loading state
6. Success toast appears
7. New run row is inserted at the top of the table

### Submit from inline YAML mode

1. User switches source to `Inline YAML`
2. YAML editor becomes visible
3. User pastes or writes manifest YAML
4. User chooses action and optional advanced settings
5. User clicks **Run Manifest**
6. Same in-place success behavior as registry mode

### Failure handling

If submission fails:

- keep the user on the page
- preserve entered data
- show inline form error plus toast if appropriate
- do not clear the form automatically

---

## Responsive behavior

### Desktop

- Run form spans full width above the table, or sits in a visually compact card
- Keep the primary action visible without requiring expansion of advanced options

### Tablet / mobile

- Stack all form controls vertically
- Convert the runs table to compact rows/cards if needed
- Keep submit action pinned to the bottom of the card section if the form becomes long
- Maintain a clear visual order: form first, history second

---

## Accessibility

- Source type toggle must be keyboard reachable and screen-reader labeled
- Form validation must be announced and associated with fields
- YAML editor should have an accessible textarea fallback
- Toasts should not be the only signal of success or error
- Table row actions must have clear labels, not icon-only controls without accessible names

---

## API and data notes

### Required now

- **Recent runs:** `/api/executions?entry=manifest&limit=200`
- **Submit:** reuse the current manifest submission API/handler

### Optional progressive enhancement

- **Manifest lookup/autocomplete:** `/api/manifests`
- **Manifest metadata/details:** `/api/manifests/{name}`
- **Manifest-centric history:** `/api/manifests/{name}/runs`

### No required backend redesign

This UI merge should primarily be a routing and composition change:

- mount the existing submit behavior into the Manifests page
- keep run history on the same page
- unify success handling so the page refreshes in place

---

## Routing and cleanup

### Canonical route

- `/tasks/manifests`

### Redirects

- old manifest submit route(s) should redirect to `/tasks/manifests`
- if helpful, redirect with an anchor or query to focus the run form

### Navigation cleanup

- remove the dedicated `Manifest Submit` tab
- keep only `Manifests`

### Success path cleanup

If there are any legacy redirects to older task/queue pages after submit, this merge is a good point to remove them and keep the user anchored in the manifest experience.

---

## Why inline form is preferred over a drawer/modal

An inline form above the runs table is the recommended default because it:

- keeps submit and monitoring in one visible flow
- avoids extra click depth
- works better for advanced fields than a small modal
- makes the empty state and success state feel connected to the same page

A drawer is still acceptable as a secondary implementation choice, but it should launch from the same **Manifests** page rather than from a separate nav tab.

---

## Acceptance criteria

- There is only one top-level manifest destination in the dashboard: **Manifests**
- Users can submit either a registry manifest or inline YAML from the **Manifests** page
- Recent manifest runs are visible on the same page without navigation
- Successful submit updates the current page in place
- Advanced options are collapsed by default
- Legacy `Manifest Submit` navigation is removed or redirected
- No raw secret entry is introduced into the UI
- The design leaves room for future `/api/manifests` registry support without requiring another top-level tab

---

## Recommended implementation sequence

### Phase 1

- Build unified **Manifests** page at `/tasks/manifests`
- Move existing submit form into top card
- Keep existing recent runs table below
- Remove extra nav tab
- Update success handling to stay on page and prepend new run

### Phase 2

- Add manifest autocomplete from `/api/manifests` if available
- Improve inline YAML validation and payload hints
- Add better row highlighting / deep-linking to the newly created run

### Phase 3

- Add optional **Saved Manifests** section if registry/catalog support is ready

---

## Final recommendation

Ship **one page called `Manifests`**.

Make it a compact command center:

- **top:** run a manifest
- **bottom:** watch the result

That is the smallest change that removes the artificial split between **Manifests** and **Manifest Submit** while preserving room for future manifest registry capabilities.
