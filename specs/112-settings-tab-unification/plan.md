# Implementation Plan: 112-settings-tab-unification

**Feature Branch**: `112-settings-tab-unification`  
**Created**: 2026-03-28  
**Status**: Implemented  
**Input**: User description: "Merge Workers, Settings, and Secrets into a single Settings surface with subsections, and document the Settings tab."

## Constitution Check

- [x] I. Orchestrate, Don't Recreate
- [x] II. One-Click Agent Deployment
- [x] III. Avoid Vendor Lock-In
- [x] IV. Own Your Data
- [x] V. Skills Are First-Class and Easy to Add
- [x] VI. The Bittersweet Lesson
- [x] VII. Powerful Runtime Configurability
- [x] VIII. Modular and Extensible Architecture
- [x] IX. Resilient by Default
- [x] X. Facilitate Continuous Improvement
- [x] XI. Spec-Driven Development Is the Source of Truth
- [x] XII. Canonical Documentation
- [x] XIII. Pre-Release Velocity

## Proposed Changes

### 1. Mission Control information architecture

- Keep `Settings` as the only top-level configuration tab.
- Model the page around three subsections:
  - `providers-secrets`
  - `user-workspace`
  - `operations`
- Redirect legacy `/tasks/secrets` and `/tasks/workers` routes into subsection URLs.

### 2. Frontend

- Replace the old `/me/profile` API-key-only React settings page with a new unified Settings page.
- Fold the reusable managed secret UI into the `Providers & Secrets` section.
- Add provider-profile CRUD controls to that same section.
- Fold the current worker pause/resume screen into the `Operations` section.
- Remove now-unused `secrets` and `workers` React entrypoints.

### 3. Backend/UI shell

- Update navigation to remove top-level `Secrets` and `Workers`.
- Keep legacy paths as redirect routes.
- Pass worker pause endpoint config into the unified Settings page boot payload.
- Update worker-management CTA links to point into Settings.

### 4. Documentation

- Add `docs/UI/SettingsTab.md` as the canonical desired-state description for the Settings tab.

## Complexity Tracking

- The main risk is replacing the current settings page without dropping existing operational functionality.
- Provider profile management needs explicit JSON validation so the UI can edit structured fields safely without inventing compatibility layers.

## Validation

- Run the task dashboard route unit tests.
- Run the Mission Control React mount browser smoke test if the environment is configured.
- Build the frontend bundle to catch strict TypeScript or Vite regressions.
