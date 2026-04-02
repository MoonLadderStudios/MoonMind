I reviewed the current Mission Control implementation.

Right now, these two tabs are not really peers:

* “Manifests” is actually a manifest run history page. It loads manifest executions from `/api/executions?entry=manifest&limit=200` and shows them in a table, with a single “New Manifest Run” button.
* “Manifest Submit” is a run creation form. It lets you either run an existing registry manifest or submit inline YAML, plus options like action, dry run, force full sync, max docs, and priority.

There is also a mismatch between UI and backend capabilities: the backend already supports a real manifest registry with list/detail/upsert/run endpoints at `/api/manifests`, `/api/manifests/{name}`, and `/api/manifests/{name}/runs`, but the dashboard currently exposes only run history plus the submit form, not the registry/catalog itself.

The cleanest merge is:

## Best option: one “Manifests” workspace

Make `/tasks/manifests` the only page, with two sections:

* top or left: recent manifest runs
* top-right or above the table: “Run Manifest” panel

A good layout would be:

* header: **Manifests**
* primary action area:

  * source toggle: **Registry Manifest** / **Inline YAML**
  * manifest name
  * action
  * submit button
* collapsible “Advanced options”:

  * dry run
  * force full sync
  * max docs
  * priority
* below that: recent runs table

Why this works:

* users usually want to submit a run and immediately see it appear in history
* it removes the awkward “go to second page just to launch”
* it keeps the operator context intact

I would strongly default the form to a compact state, not a giant always-open form. The advanced options should stay collapsed unless needed.

## Most elegant interaction patterns

### 1. Inline composer above the runs table

This is the strongest choice for Mission Control.

Structure:

* compact card at top:

  * “Run a manifest”
  * source type segmented control
  * registry selector or inline YAML area
  * submit
* runs table below

This feels operational and efficient.

### 2. Slide-over or modal launched from the same page

Keep the current runs page, but replace the separate tab with:

* “New Run” button on `/tasks/manifests`
* opening a right-side drawer or modal containing the existing submit form

This is very clean if you want the page itself to remain mostly history-focused.

Best when:

* manifest submission is occasional
* you want the list to stay dominant

### 3. Single page with internal sub-sections

Still one tab, but with internal pills:

* Runs
* Run Manifest
* Registry

This is a good future-proof structure if you plan to expose the actual registry next, since the backend already supports it.

I would only do this if you intend to add manifest catalog/editing soon. Otherwise it risks recreating the same fragmentation inside one page.

## What I would recommend specifically

I would do this:

**One nav item: “Manifests”**

Page sections:

1. **Run Manifest** card at the top

   * source: Existing Manifest / Inline YAML
   * manifest name
   * action
   * submit button
   * advanced options disclosure

2. **Recent Runs** table below

   * current manifest runs list
   * clicking a row opens the execution detail

3. Later, optionally add:

   * **Saved Manifests** sidebar or secondary section using `/api/manifests`

That gives you a unified page now, without overcomplicating it.

## A subtle but important UX improvement

Today the “Manifests” page title is misleading, because it is really “Manifest Runs.”

If you merge them, the ambiguity becomes much less of a problem because the page can legitimately be about the whole manifest workflow. But I would still consider one of these titles:

* Manifests
* Manifest Operations
* Manifest Runs

Of those, **Manifests** is best if you add both running and registry concepts on one page.

## Things worth cleaning up during the merge

There is at least one sign this code path is stale relative to the newer unified dashboard routing: the submit flow still redirects to legacy `/tasks/queue/{id}` style paths.

So a merge is a good moment to also:

* align redirects with the unified task detail routing
* stop treating manifest submission as a special legacy flow
* make successful submissions update the current page instead of bouncing users around

The nicest behavior would be:

* user submits from the same page
* success toast appears
* new run is inserted at the top of Recent Runs
* optionally auto-open its detail page

## My ranking

1. One page with compact inline composer + recent runs table
2. One page with drawer/modal composer
3. One page with internal Runs / Run / Registry sub-tabs

If you want the most elegant version for MoonMind as it exists today, I would choose **#1**.

If you want, I can next turn this into a concrete redesign plan for the Mission Control manifests page, including suggested sections, control layout, and the specific route/component changes in the React entrypoints.
