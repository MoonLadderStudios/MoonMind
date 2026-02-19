# Contract: Dashboard Theme Runtime Behavior (Phase 3)

## Scope

Defines runtime behavior for theme selection and application on task dashboard routes under `/tasks`.

## API Surface Impact

- No backend API endpoint additions or schema changes are required for Phase 3.
- Existing dashboard APIs remain contract-compatible.

## Theme Resolution Contract

### Inputs

- Persisted user preference (local storage key: `moonmind.theme`)
- Runtime system preference (`prefers-color-scheme: dark`)

### Resolution Order

1. If persisted preference is `light` or `dark`, apply that mode.
2. Otherwise, apply current system preference.

### Output State

- Document root (`<html>`) reflects resolved mode via theme class/state.
- Dashboard surfaces inherit mode through existing token-driven semantic classes.

## Interaction Contract

### Theme Toggle Control

- The dashboard shell exposes a visible theme toggle control.
- Triggering the control must:
  - flip between `light` and `dark`
  - update rendered theme immediately
  - persist the new explicit preference value

### System Preference Listener

- Runtime `prefers-color-scheme` changes must update active theme only when no explicit user preference exists.
- Explicit user choice suppresses system-driven overrides until cleared.

## First-Paint Contract

- A pre-runtime bootstrap must apply resolved theme before first visual paint.
- First-load rendering should not visibly flash the opposite theme.

## Visual Hierarchy Contract (Dark Mode)

- Purple remains primary accent for active/primary interactions.
- Yellow/orange accents are restricted to warning/high-attention use.
- Tables, forms, and live output areas remain readable and actionable.

## Failure Handling Contract

- Invalid or inaccessible stored preference values are treated as unset.
- If browser preference APIs are unavailable, default behavior remains deterministic and usable (light fallback with explicit toggle still functioning).
