# Feature Specification: remove-runtime-globals

**Feature Branch**: `126-remove-runtime-globals`
**Created**: 2026-04-03
**Status**: In Progress
**Input**: User description: "Remove the global runtime exceptions. Put marked in package.json, import it in skills.tsx, and delete the CDN script from react_dashboard.html. Then track down why mce-autosize-textarea needed a global customElements.define monkeypatch and move that guard into the module that actually owns the element registration or remove the duplicate registration path entirely."

## User Scenarios & Testing

### User Story 1 - Skills preview runs without template globals (Priority: P1)

Frontend maintainers need the skills page to render markdown through the normal module graph so Mission Control does not depend on a page-template global parser.

**Why this priority**: The current template-level parser global hides a leftover compatibility path and makes the skills entrypoint depend on runtime HTML state instead of its own imports.

**Independent Test**: The skills page renders markdown previews correctly when loaded through the React entrypoint, and the shared dashboard template no longer injects a markdown-parser global.

**Acceptance Scenarios**:

1. **Given** the skills page loads through `react_dashboard.html`, **When** the entrypoint renders skill markdown, **Then** it uses its bundled dependency path and the template does not inject a parser CDN script.
2. **Given** skill markdown contains unsafe HTML or links, **When** the preview renders, **Then** the existing sanitization behavior remains enforced.

---

### User Story 2 - Custom element registration is owned by the registering module (Priority: P1)

Frontend maintainers need custom element registration behavior to live with the code that registers the element so Mission Control no longer patches `customElements.define` globally at page boot.

**Why this priority**: A page-template monkeypatch for one element name is strong evidence of stale compatibility code and broadens the runtime blast radius of a narrow registration problem.

**Independent Test**: Mission Control pages boot without a template-level `customElements.define` override, and any remaining duplicate-registration guard exists only in the module that owns the element registration.

**Acceptance Scenarios**:

1. **Given** Mission Control boots any React dashboard page, **When** the page template executes, **Then** it does not replace `window.customElements.define`.
2. **Given** the owning module attempts to register an already-registered custom element, **When** that path still exists, **Then** the guard is scoped to that module and only suppresses the duplicate registration for that element.

### Edge Cases

- Invalid or empty markdown still renders a safe preview instead of breaking the skills page.
- Dashboard pages that do not use the skills preview or the custom element continue to boot without relying on removed globals.
- If no live duplicate-registration path exists for `mce-autosize-textarea`, the cleanup removes the dead compatibility branch instead of relocating it.

## Requirements

### Functional Requirements

- **FR-001**: Mission Control skill markdown rendering MUST use a dependency managed by the frontend build instead of a parser exposed as a page-template global.
- **FR-002**: `api_service/templates/react_dashboard.html` MUST NOT inject a markdown parser CDN script for the React dashboard.
- **FR-003**: `api_service/templates/react_dashboard.html` MUST NOT override `window.customElements.define` to special-case `mce-autosize-textarea`.
- **FR-004**: If duplicate custom element registration protection is still required, it MUST live in the module that owns the registration path and MUST scope the guard to that registration flow only.
- **FR-005**: If no active duplicate registration path exists for `mce-autosize-textarea`, the dead compatibility code MUST be removed entirely rather than preserved behind a global guard.
- **FR-006**: Frontend verification MUST cover the skills markdown rendering path and confirm the cleanup does not regress Mission Control boot/build behavior.

## Success Criteria

- **SC-001**: The skills entrypoint imports its markdown parser directly and no longer references a parser on `window`.
- **SC-002**: `react_dashboard.html` contains neither the marked CDN script nor a `customElements.define` monkeypatch.
- **SC-003**: The skills entrypoint tests continue to pass with no test setup that fakes a template-provided parser global.
- **SC-004**: Frontend typecheck, targeted UI tests, and build verification pass after the cleanup.
