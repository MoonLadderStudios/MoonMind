# Remaining work: `docs/Tasks/SkillAndPlanContracts.md`

**Source:** [`docs/Tasks/SkillAndPlanContracts.md`](../../Tasks/SkillAndPlanContracts.md)  
**Last synced:** 2026-03-24

## Open items

### §14 Implementation checklist (minimum to start coding)

1. Tool registry file format + loader + validator.
2. Registry snapshot digest + artifact storage.
3. `plan.validate` activity (deep validation).
4. Plan Executor in `MoonMind.Run` (schedule nodes, results, policy).
5. `mm.skill.execute` dispatcher in worker fleet (note: codebase may use `mm.tool.execute` naming — reconcile).
6. Progress query + optional progress artifact.

Cross-check each line against the repository; tick in source doc when done.
