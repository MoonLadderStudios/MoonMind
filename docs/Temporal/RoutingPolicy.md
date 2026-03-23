# Temporal Production Routing Policy

## Purpose
This document defines how backend routing is handled across the `queue`, `system`, and `temporal` execution sources.

## Task Classes & Feature Flags
- Workflows are primarily routed through `TemporalExecutionService`.
- Tasks and identifiers specify their destination based on explicit backend flags.
- **Queue/system** vs **Temporal**: Temporal should be selected by routing rules based on `TEMPORAL_DASHBOARD_SUBMIT_ENABLED` and associated feature flags.

## Runtime Picker Exclusion
Consistent with `docs/UI/MissionControlArchitecture.md`, the `temporal` source is kept **out** of the worker runtime picker. It is treated as a backend substrate layer, not a user-facing choice.

## Source Resolution
Source resolution in `api_service/api/routers/task_dashboard.py` ensures deterministic outcomes for list/detail routes.
When `source_hint` is provided, it serves as an override. Without it, the underlying engine queries each source type systematically to determine where a task resides.

## Migration & Rollout
1. Existing tasks maintain their current source (`queue`, `system`).
2. New tasks that trigger Temporal capabilities will run under the `temporal` source.
3. Partial enablement allows fallback to `queue` or `system` if Temporal services are unavailable or disabled by feature flags.

## Support & Debugging
Engineers can force the resolution using the explicit `?source=temporal` (or `queue`/`system`) query string parameter if the canonical resolver fails or during testing.
