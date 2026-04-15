# Contract: Temporal Type-Safety Gates

## Purpose

Define the review-gate surface for MM-331 from TOOL board. The gate evaluates Temporal type-safety migration evidence and returns deterministic findings that can be asserted in tests and read by reviewers.

## Rule Categories

| Rule ID Prefix | Category | Source Mapping |
| --- | --- | --- |
| `TEMPORAL-COMPAT-*` | Compatibility and evolution safety | DESIGN-REQ-005 |
| `TEMPORAL-TEST-*` | Schema, boundary, replay, and static-analysis evidence | DESIGN-REQ-018 |
| `TEMPORAL-ESCAPE-*` | Transitional escape-hatch constraints | DESIGN-REQ-019 |
| `TEMPORAL-ANTI-*` | Known unsafe Temporal anti-patterns | DESIGN-REQ-020 |

## Finding Shape

```json
{
  "ruleId": "TEMPORAL-ANTI-001",
  "status": "fail",
  "target": "workflow.execute_activity raw dictionary payload fixture",
  "message": "Raw dictionary activity payloads are not allowed at workflow call sites.",
  "remediation": "Use a named request model and typed execution boundary, or document a boundary-only compatibility shim.",
  "evidenceRef": null
}
```

## Required Behavior

- A compatibility-sensitive change without replay, in-flight, or cutover evidence returns a failed `TEMPORAL-COMPAT-*` or `TEMPORAL-TEST-*` finding.
- A safe additive change with evidence returns passing findings.
- A non-additive change without an explicit migration or cutover plan returns a failed compatibility finding.
- A raw dictionary activity payload returns a failed `TEMPORAL-ANTI-*` finding.
- A public workflow handler whose canonical interface is a raw dictionary returns a failed `TEMPORAL-ANTI-*` finding.
- A generic action envelope for new public controls returns a failed `TEMPORAL-ANTI-*` finding.
- A provider-shaped top-level workflow-facing result returns a failed `TEMPORAL-ANTI-*` finding.
- An unnecessary untyped status or value leak where a closed model exists returns a failed `TEMPORAL-ANTI-*` finding.
- Nested raw bytes or large conversational state in workflow history returns a failed `TEMPORAL-ANTI-*` finding unless represented through an intentional compact reference or approved serialized boundary.
- An escape hatch without transitional, boundary-only, compatibility-justified documentation returns a failed `TEMPORAL-ESCAPE-*` finding.

## Evidence Requirements

Accepted evidence may be one of:

- unit test name proving schema or gate behavior
- workflow-boundary test name proving typed round trip behavior
- replay or replay-style fixture name
- explicit cutover or migration note for an unsafe non-additive change

Evidence must identify the reviewed target. Generic statements such as "tested manually" do not satisfy the gate.

## Non-Goals

- This contract does not define a full inventory of every Temporal boundary.
- This contract does not rename any Temporal workflow, activity, signal, update, or query.
- This contract does not permit compatibility aliases that change execution semantics or billing-relevant values.
