# Remaining work: `docs/Tasks/TaskPresetsSystem.md`

**Source:** [`docs/Tasks/TaskPresetsSystem.md`](../../Tasks/TaskPresetsSystem.md)  
**Last synced:** 2026-03-24

## Open items

### §10 Migration path

- **Phase 1:** Dual output — expand returns both `steps[]` and `plan`; compiler still uses legacy paths where needed.
- **Phase 2:** Plan-first — `plan` + `planArtifactRef` primary; `steps[]` deprecated but populated.
- **Phase 3:** Plan-only — remove `steps[]` from expand; `appliedStepTemplates` → `appliedPreset` + `planArtifactRef`; rename models/API to `Preset` / `/api/presets`.

### §10.3 Code changes

- `catalog.py` expand output shape, `payload.py` compile path, router rename, DB model renames, expansion service final `PlanDefinition` stage.

### §11 Q1 / Q2

- Non-linear `dependsOn` → `PlanEdge` (Phase 2+).
- Optional `planPolicy` overrides per expansion — decide and implement.
