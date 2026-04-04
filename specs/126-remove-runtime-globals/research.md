# Research: remove-runtime-globals

## Decision 1: Bundle markdown parsing through the frontend module graph

- **Decision**: Add `marked` as a normal frontend dependency and import it directly in `frontend/src/entrypoints/skills.tsx`.
- **Rationale**: The skills page is the only consumer, and using a direct import removes hidden coupling between the React entrypoint and `react_dashboard.html`.
- **Alternatives considered**:
  - Keep the template CDN script and `window.marked`: rejected because it preserves a global compatibility path the user explicitly wants removed.
  - Add a local markdown parser wrapper module first: rejected as unnecessary indirection for a single consumer.

## Decision 2: Preserve sanitization after markdown parsing

- **Decision**: Continue running the rendered HTML through the existing `sanitizeHtml()` function after markdown conversion.
- **Rationale**: The current behavior already strips unsafe elements and attributes, and this cleanup should not widen the allowed HTML surface.
- **Alternatives considered**:
  - Trust the parser output directly: rejected because it would weaken the current safety posture.
  - Replace the sanitizer as part of this cleanup: rejected because it would broaden scope beyond removing globals.

## Decision 3: Delete the `mce-autosize-textarea` global monkeypatch

- **Decision**: Remove the template-level `customElements.define` override entirely.
- **Rationale**: Repo-wide search found no source-owned registration path, no package reference, and no other runtime code referring to `mce-autosize-textarea`, so the monkeypatch appears to be dead compatibility code.
- **Alternatives considered**:
  - Move the guard into an owning module: rejected because no owning module exists in the current codebase.
  - Keep the monkeypatch defensively: rejected because it preserves global behavior for a non-existent path and conflicts with the repo's delete-don't-deprecate policy.
