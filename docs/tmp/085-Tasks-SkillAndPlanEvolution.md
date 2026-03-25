# Remaining work: `docs/Tasks/SkillAndPlanEvolution.md`

**Source:** [`docs/Tasks/SkillAndPlanEvolution.md`](../../Tasks/SkillAndPlanEvolution.md)  
**Last synced:** 2026-03-24

## Open items

### Recommendations & migration strategy (§)

- Strict JSON-schema validation for Tools/Plans; central registry loader; snapshot digest — verify completeness vs `SkillAndPlanContracts.md` and code.
- Plan executor, dispatcher, observability, security bullets — many are partially implemented in `MoonMind.Run`; align doc with shipped behavior.

### Implementation roadmap (§)

1. Registry loader + digest artifact.
2. `plan.validate` activity depth.
3. Interpreter loop completeness (policy, failure modes).
4. `mm.tool.execute` / dispatcher fidelity to spec.
5. Progress Query surface.
6. Metrics per tool/node.
7. `allowed_roles` and related security.
8. **Migration:** v1 schema lock-in and consumer documentation.

### Legacy note

- Doc still mentions `skill` dispatch path alongside `agent_runtime`; align with [`docs/tmp/skill-system-alignment.md`](../skill-system-alignment.md) / orchestrator removal if that branch is deleted.
