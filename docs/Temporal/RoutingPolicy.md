# Temporal Production Routing Policy

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-RoutingPolicy.md`](../tmp/remaining-work/Temporal-RoutingPolicy.md)

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

## Source persistence

Tasks keep their recorded source (`queue`, `system`, or `temporal`) for the lifetime of that record. New submissions use routing rules and feature flags (`TEMPORAL_DASHBOARD_SUBMIT_ENABLED`, etc.); when Temporal is disabled or unavailable, routing may fall back per deployment policy. Further cutover work is in the tracker linked above.

## Support & Debugging
Engineers can force the resolution using the explicit `?source=temporal` (or `queue`/`system`) query string parameter if the canonical resolver fails or during testing.
