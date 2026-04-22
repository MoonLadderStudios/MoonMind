# Data Model: Remediation V1 Policy Guardrails

## Remediation Creation Policy

Purpose: Represents future-facing policy metadata that may describe automatic self-healing intent without making it executable by default.

Fields:
- `enabled`: whether future automatic remediation behavior is explicitly enabled.
- `triggers`: bounded trigger names such as failed, attention_required, or stuck.
- `createMode`: bounded creation behavior such as proposal or immediate_task when future support exists.
- `templateRef`: remediation template identifier.
- `authorityMode`: bounded remediation authority mode.
- `maxActiveRemediations`: maximum active remediations allowed by policy.
- `maxSelfHealingDepth`: maximum nested self-healing depth.

Validation rules:
- Policy metadata alone does not create a remediation link.
- Unsupported executable behavior fails closed until a runtime implementation explicitly supports it.
- Future executable policy must remain bounded by trigger, create mode, template, authority, max-active count, depth, audit, and redaction constraints.

## Remediation Capability Surface

Purpose: Runtime-visible set of typed remediation actions and explicitly denied raw capabilities.

Fields:
- `actionKind`
- `riskTier`
- `targetType`
- `inputMetadata`
- `verificationRequired`
- `verificationHint`

Validation rules:
- Catalog entries must represent typed actions, not raw host, Docker, SQL, storage, network, secret-read, or redaction-bypass capabilities.
- Raw or unknown action requests return structured denial reasons.
- Metadata must not contain secrets, raw access grants, storage keys, or unbounded log bodies.

## Bounded Failure Outcome

Purpose: Structured result used when remediation cannot proceed safely.

Allowed examples:
- validation failure
- evidenceDegraded
- no_op
- precondition_failed
- lock_conflict
- escalated
- unsafe_to_act
- verification_failed
- failed

Validation rules:
- Missing target visibility, partial evidence, unavailable live follow, target rerun, failed precondition, lock conflict, stale lease, missing container, unsafe termination, and remediator failure must resolve to bounded outcomes.
- Bounded outcomes must not silently succeed or fall back to raw access.

## State Transitions

```text
policy metadata present
  -> inert metadata when explicit support is absent
  -> bounded policy evaluation when explicit support exists
  -> manual/proposal/remediation creation only if allowed
  -> structured denial otherwise

action capability request
  -> typed catalog action allowed for evaluation
  -> raw/unsupported action denied
  -> bounded outcome recorded
```
