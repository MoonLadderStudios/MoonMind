# Implementation Plan: remove-runtime-globals

**Branch**: `126-remove-runtime-globals` | **Date**: 2026-04-03 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/126-remove-runtime-globals/spec.md`

## Summary

Remove the last React dashboard template globals by bundling markdown parsing through the frontend module graph, deleting the template CDN script, and removing the stale page-boot `customElements.define` monkeypatch that no longer corresponds to any in-repo `mce-autosize-textarea` registration path.

## Technical Context

**Language/Version**: TypeScript/React 19, HTML template, npm package metadata  
**Primary Dependencies**: Vite frontend build, React Query, packaged markdown parser, Vitest  
**Storage**: N/A  
**Testing**: `npm run ui:typecheck`, `npm run ui:test -- frontend/src/entrypoints/skills.test.tsx`, `npm run ui:build:check`, `./tools/test_unit.sh`  
**Target Platform**: Backend-served Mission Control web UI  
**Project Type**: Web application with FastAPI-hosted HTML shell and Vite entrypoints  
**Performance Goals**: Preserve current page boot/render behavior with no additional network fetches for parser globals  
**Constraints**: Remove compatibility code instead of preserving globals, keep markdown sanitization intact, avoid changing unrelated dashboard boot behavior  
**Scale/Scope**: One entrypoint (`skills.tsx`), one shared HTML template, associated frontend tests, and package metadata/lockfile

## Constitution Check

- **II. One-Click Agent Deployment**: PASS. The cleanup stays within the existing repo-managed frontend build and does not add deployment prerequisites.
- **V. Skills Are First-Class and Easy to Add**: PASS. The skills page keeps working while removing hidden runtime coupling to the HTML shell.
- **VI. Design for Deletion / Thin Scaffolding**: PASS. This feature deletes stale compatibility scaffolding instead of extending it.
- **VII. Powerful Runtime Configurability**: PASS. No runtime configuration behavior changes; hidden globals are removed rather than made configurable.
- **VIII. Modular and Extensible Architecture**: PASS. Markdown parsing and any element-registration logic stay with the module that owns them.
- **IX. Resilient by Default**: PASS. Verification covers the affected entrypoint, build path, and full unit suite to catch regressions.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. Cleanup requirements and validation are captured before implementation.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. No canonical docs need migration prose for this targeted runtime cleanup.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. Dead globals are removed outright; no backward-compat wrapper is retained.

## Scope

### In Scope

- Add the markdown parser as a real frontend dependency and import it in `frontend/src/entrypoints/skills.tsx`
- Remove `window.marked` usage and the `marked` CDN script from `api_service/templates/react_dashboard.html`
- Remove the `mce-autosize-textarea` `customElements.define` monkeypatch from `react_dashboard.html`
- Update tests so the skills entrypoint no longer depends on a template-provided parser global
- Regenerate package lock/build artifacts required by the dependency change

### Out of Scope

- Changing markdown sanitization policy or supported HTML tags beyond what this cleanup requires
- Introducing a new rich-text editor or custom element system
- Refactoring unrelated dashboard boot logic, theme initialization, or page layout

## Research Summary

- The only in-repo references to `window.marked` are in `frontend/src/entrypoints/skills.tsx`, its test file, and the shared dashboard template script tag.
- A repo-wide search, including unignored files, found no in-repo owner or dependency reference for `mce-autosize-textarea`; the only references are the template monkeypatch and built output that still reflects current source.
- Because no active registration path exists in source or lockfile, the safest cleanup is to delete the global monkeypatch rather than relocate it.

## Structure Decision

- Keep markdown rendering ownership inside `frontend/src/entrypoints/skills.tsx` by importing the packaged parser there and preserving the existing `sanitizeHtml()` post-processing.
- Keep the shared HTML shell limited to generic boot concerns (theme initialization and asset injection), with no page-specific globals.
- Treat the `mce-autosize-textarea` guard as dead compatibility code and delete it from the template because there is no owning module to move it into.
- Update the skills entrypoint test setup to exercise direct parser imports instead of stubbing `window.marked`.

## Verification Plan

1. Run `npm run ui:typecheck`.
2. Run `npm run ui:test -- frontend/src/entrypoints/skills.test.tsx`.
3. Run `npm run ui:build:check`.
4. Run `./tools/test_unit.sh`.
