---
name: security-review
description: Assess a repository or architecture, build a threat model, and review business logic and authorization directly, including when no scanner findings exist. Use for an explicitly requested security assessment, not routine feature work.
---

# Security Review

Use the selected Omnigent harness, Provider Profile, model and remaining task
budget. Read [the portable procedures](references/procedures.md) before composing
an investigation or fix. The adapted upstream methods and their license are
identified in [provenance](references/PROVENANCE.json).

Establish the actual candidate, requested target, permitted actions, credential
references, exclusions and remaining budget from the admitted request. Selecting
this Skill grants no additional authority. Keep these constraints in subsequent
steps and stop on cancellation; do not launch another provider or reset a budget.

For a direct review, follow entrypoints to the checks they depend on. Map assets,
principals, resources, trust crossings and security invariants. Examine who can
invoke each action on which resource, including object ownership, replay,
state transitions, negative paths and boundary values. Separate enforced checks
from assumptions. Record code locations, actual routes, identities and resources
examined. A single-user application can still have multiple provider accounts,
workflows and independent deployments; do not invent tenant or human-role systems.

For a repository assessment, first identify languages, exposed entrypoints and
the requested coverage. Reuse completed native scan/report artifacts. If an
admitted registered operation is available, use its existing scoped ContainerJob
interface as described in the procedures. Missing scanner or cloud/web capability
is a named coverage limitation; continue direct logic review within scope.

Save the threat model and evidence-referenced `security-review/v1` interpretation
artifact using [the report contract](references/report.md), alongside a readable
summary. Do not overwrite native findings. An assessment-only request allows
review artifacts, but no source edits, credential changes, deployment or source
publication. Use `security-investigate` for a bounded finding/hypothesis and
`security-remediate` only for an already authorized fix and focused retest.
