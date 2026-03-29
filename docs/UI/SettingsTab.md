# Settings Tab

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-28

## 1. Purpose

The Mission Control **Settings** tab is the single operator-facing home for configuration and administrative controls.

Mission Control should keep the primary product surface centered on tasks, task detail, proposals, schedules, and other task-adjacent workflows. Settings exists to hold the supporting configuration and operational controls that shape how those workflows run.

The Settings tab replaces the older split where **Settings**, **Secrets**, and **Workers** were separate top-level destinations.

## 2. Information Architecture

The desired top-level navigation model is:

- Tasks and other task-adjacent product surfaces
- Settings

Within **Settings**, Mission Control exposes subsection navigation rather than separate top-level tabs.

### 2.1 Providers & Secrets

This subsection is the primary configuration surface for runtime and provider access.

It contains:

- **Provider Profiles** as the durable runtime/provider launch contract
- **Managed Secrets** and secret-health surfaces
- bindings between secrets and provider-profile roles
- OAuth-backed provider-profile lifecycle entry points when applicable
- provider credential health, readiness, and validation feedback

This subsection should reflect the canonical architecture:

- [Provider Profiles](../Security/ProviderProfiles.md) define runtime, provider, materialization mode, policy, and secret references.
- [Secrets System](../Security/SecretsSystem.md) defines storage, encryption, resolution, audit, and lifecycle behavior.

Secrets are therefore an underlying system used by provider profiles and runtime launch configuration, not a standalone top-level product area.

### 2.2 User / Workspace Settings

This subsection holds user-scoped and workspace-scoped configuration that does not primarily belong to provider credential management or operational controls.

Examples include:

- user preferences
- workspace defaults
- task-authoring defaults
- UI behavior and presentation preferences
- future operator-configurable product settings surfaced from the broader project configuration model

This section is expected to expand substantially over time as more of MoonMind's runtime configuration is exposed through Mission Control.

### 2.3 Operations

This subsection contains the operational and administrative controls that were previously presented as the `Workers` page.

It contains:

- worker pause and resume controls
- drain and quiesce operations
- recent operational audit actions
- system-control surfaces that are operational rather than task-authoring oriented

These controls are important, but they are narrower than the primary task console and belong under Settings rather than beside task workflows in top-level navigation.

## 3. Layout

The Settings page should use a single route, `/tasks/settings`, with subsection selection handled inside the page.

Recommended structure:

1. Page header explaining that Settings holds configuration and administrative controls
2. Section switcher with:
   - `Providers & Secrets`
   - `User / Workspace`
   - `Operations`
3. A section-specific content region

The content region should optimize for operational clarity:

- overview copy first
- actionable tables and forms second
- advanced or high-risk controls grouped into clearly labeled cards

## 4. Routing

Canonical route:

- `/tasks/settings`

Supported subsection query model:

- `/tasks/settings?section=providers-secrets`
- `/tasks/settings?section=user-workspace`
- `/tasks/settings?section=operations`

Legacy routes such as `/tasks/secrets` and `/tasks/workers` should redirect into the corresponding Settings subsection rather than remain first-class tabs.

## 5. Design Rules

- Keep **Settings** singular and coherent. Avoid reintroducing overlapping top-level admin tabs.
- Prefer provider-profile-backed configuration over ad hoc raw API-key forms.
- Keep secrets management adjacent to provider profiles, because that is how operators actually make runtimes usable.
- Keep operations controls available, but subordinate to the broader Settings information architecture.
- Let this page expand over time to cover the many runtime and workspace settings exposed by the project without fragmenting the main navigation again.
