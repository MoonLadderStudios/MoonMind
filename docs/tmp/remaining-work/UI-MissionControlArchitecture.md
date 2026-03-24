# Remaining work: `docs/UI/MissionControlArchitecture.md`

**Source:** [`docs/UI/MissionControlArchitecture.md`](../../UI/MissionControlArchitecture.md)  
**Last synced:** 2026-03-24

## Open items

### Rollout plan (§13)

- Phases 1–4 and 3.5 (Temporal read → actions → artifact submit → scheduling → compatibility refinement) — implement or mark complete per feature flag reality.
- Mixed-source list caveat (§12.3) — resolve when single substrate program completes ([`docs/tmp/SingleSubstrateMigration.md`](../SingleSubstrateMigration.md)).

### Implementation checklist (§14)

All sections still contain many unchecked items in the source doc:

- Backend/UI boundary (`sources.temporal`, `statusMaps`, feature flags, canonical resolution).
- List page (Temporal client, normalization, filters, pagination).
- Detail page (resolver, artifacts, metadata, timeline).
- Actions (cancel, update, signals, optimistic refresh).
- Submit (artifact-first, backend-routed Temporal create, redirects).
- Scheduling (per checklist rows in source).

Treat the source checklist as authoritative until boxes are checked in-doc.
