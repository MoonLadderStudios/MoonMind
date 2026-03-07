# Research: Task UI Queue Layout Switching

## Decision 1: Centralize queue metadata via `queueFieldDefinitions`
- **Decision**: Define a static `queueFieldDefinitions` array adjacent to `toQueueRows()` containing `{ key, label, render, tableSection }` entries for queue name, runtime, skill, and timeline timestamps, plus a helper `renderQueueFieldValue` that every renderer calls when populating `<td>` and `<dd>` nodes.
- **Rationale**: Keeping the definitions next to the normalization helper guarantees table cells, cards, and future consumers stay in sync as soon as a new field is appended, satisfying DOC-REQ-002/FR-001 while preventing column drift that previously occurred when fields were duplicated inside each renderer.
- **Alternatives**:
  - Duplicate constants per layout. Rejected because it immediately violates the “single source of truth” requirement and forces copy/paste when adding metadata.
  - Store definitions in server-side templates. Rejected because `dashboard.js` already runs entirely client-side; injecting config from the backend would require additional API plumbing for no gain.

## Decision 2: Generate both layouts in one render path
- **Decision**: Introduce `renderQueueLayouts(rows)` that returns a wrapper containing `.queue-table-wrapper` (with `data-sticky-table` hint) and `.queue-card-list` markup while keeping the shared `rows` array in memory; the helper short-circuits to the existing empty-state paragraph when no rows are present.
- **Rationale**: Rendering both HTML blobs at once means filters, auto-refresh, and manual sort actions update cards and tables in lockstep without triggering extra fetches, fulfilling DOC-REQ-001/003. The DOM cost is acceptable (<2× row count) and we can rely on CSS to display the appropriate layout per breakpoint.
- **Alternatives**:
  - Rerun fetches per layout or maintain independent render paths. Rejected because it would double API pressure, make state synchronization fragile, and complicate `renderQueueListPage` call sites.
  - Use client-side media query listeners to re-render only when the viewport crosses thresholds. Rejected because CSS alone can toggle visibility, keeping JS simpler and more reliable.

## Decision 3: Restrict cards to queue rows and guard other sources
- **Decision**: `renderQueueCards(rows)` filters `row.source === "queue"` before producing list items, and `renderQueueLayouts` checks for mixed sources so `.queue-table-wrapper` can stay visible on mobile if orchestrator/manifests rows are present.
- **Rationale**: Only queue rows have the metadata/doc contract spelled out in docs/TaskUiQueue.md. Keeping orchestrator/manifests table-only prevents partial cards that would confuse operators and lets us expand card coverage iteratively (DOC-REQ-003 edge-case note).
- **Alternatives**:
  - Render cards for every source. Rejected because orchestrator rows lack link/action parity, so cards would expose incomplete data.
  - Hide non-queue rows entirely on mobile. Rejected because it would drop orchestrator/manifests visibility on the "Active" tab, violating FR-006.

## Decision 4: Tailwind-first responsive styling with sticky table escape hatch
- **Decision**: Encode `.queue-layouts` grid, card styles, and breakpoint rules in `dashboard.tailwind.css` using MoonMind tokens; hide cards at `min-width:768px` and hide tables below that breakpoint unless the wrapper is marked `data-sticky-table="true"`, in which case tables remain visible even on mobile.
- **Rationale**: Tailwind/PostCSS already produces `dashboard.css`, so adding semantics there keeps dark-mode theming and tokens consistent. The sticky flag allows us to continue showing the legacy table for orchestrator/manifests rows on small viewports while still giving queue users cards (DOC-REQ-001/006/edge cases).
- **Alternatives**:
  - Inline styles or new stylesheet. Rejected because it would bypass the Tailwind build and risk token drift.
  - CSS-only detection without sticky flag. Rejected because CSS cannot distinguish mixed-source datasets; the data attribute lets JS signal when mobile tables must stay visible.

## Decision 5: Validation + bundle measurement strategy
- **Decision**: Extend existing dashboard Jest tests (run via `./tools/test_unit.sh`) to snapshot `renderQueueCards`, `renderQueueTable`, and `renderQueueLayouts`, and document manual responsive QA + gzip measurements in quickstart/docs.
- **Rationale**: Tests ensure `queueFieldDefinitions` stays authoritative and catch future regressions when new fields are added. Documented manual steps (320 px/768 px/1024 px toggles + gzip delta) are required by DOC-REQ-008 and keep rollout audits repeatable.
- **Alternatives**:
  - Rely solely on manual QA. Rejected because specs demand automated validation and shared definitions make snapshot tests cheap.
  - Add a new CLI just for bundle measurements. Rejected because `du`/existing npm scripts suffice; extra tooling would slow CI for marginal benefit.
