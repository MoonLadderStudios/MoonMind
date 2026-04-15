# Data Model: Temporal Type-Safety Gates

## Review Gate Rule

- **Purpose**: Defines one compatibility, testing, escape-hatch, or anti-pattern requirement.
- **Fields**:
  - `id`: stable rule identifier, for example `TEMPORAL-COMPAT-001`
  - `category`: one of `compatibility`, `testing`, `escape_hatch`, `anti_pattern`
  - `description`: reviewer-facing rule summary
  - `severity`: `error` for merge-blocking violations, `warning` for non-blocking advisory findings
  - `sourceDesignRequirement`: mapped `DESIGN-REQ-*` identifier
- **Validation rules**:
  - `id`, `category`, `description`, and `severity` are required.
  - Every in-scope source design requirement must map to at least one rule.

## Review Gate Finding

- **Purpose**: Deterministic output emitted when a rule evaluates one target.
- **Fields**:
  - `ruleId`: matching review gate rule identifier
  - `status`: `pass` or `fail`
  - `target`: workflow, activity, message, fixture, or evidence item under review
  - `message`: concise result
  - `remediation`: required when `status` is `fail`
  - `evidenceRef`: optional reference to replay, in-flight, schema, or boundary test evidence
- **Validation rules**:
  - Failed findings must include remediation.
  - Passing compatibility-sensitive findings must include either an evidence reference or a reason the change is not compatibility-sensitive.

## Compatibility Evidence

- **Purpose**: Proves a Temporal contract change is safe for live or replayed histories.
- **Fields**:
  - `changeKind`: workflow, activity, signal, update, query, or Continue-As-New contract change
  - `safetyMode`: `additive`, `replay_tested`, `in_flight_tested`, or `versioned_cutover`
  - `evidenceRef`: test name, fixture name, or cutover note reference
  - `nonAdditiveReason`: required for non-additive changes
- **Validation rules**:
  - Additive changes may pass when callers and handlers tolerate new optional fields or widened values.
  - Non-additive changes require explicit replay/in-flight evidence or cutover notes.
  - Activity, workflow, signal, update, and query names remain stable unless a cutover plan exists.

## Escape Hatch Justification

- **Purpose**: Documents why a temporary compatibility shape remains allowed.
- **Fields**:
  - `target`: boundary method, model field, or message shape
  - `reason`: compatibility need
  - `boundaryOnly`: true when the escape hatch is constrained to public entry validation
  - `transitional`: true when explicitly marked as temporary
  - `semanticRisk`: whether it can affect execution, billing, routing, or provider behavior
- **Validation rules**:
  - `boundaryOnly` and `transitional` must be true.
  - `semanticRisk` must be false for accepted escape hatches.
  - Hidden business logic behind an escape hatch fails the gate.

## Temporal Pattern Case

- **Purpose**: Representative fixture used to prove known unsafe patterns are rejected and approved alternatives are accepted.
- **Fields**:
  - `pattern`: raw dictionary activity payload, public raw dictionary handler, generic action envelope, provider-shaped workflow-facing result, untyped status leak, nested raw bytes, large workflow-history state, or an approved safe alternative
  - `target`: fixture or source target being checked
  - `expectedRuleId`: associated rule identifier for the evaluated case
- **Validation rules**:
  - The evaluator determines pass or fail from the pattern registry, not caller-provided outcome metadata.
  - Every anti-pattern listed in the source brief must have at least one failing fixture.
  - Safe alternatives must pass so the gate does not merely reject all Temporal changes.

## State Transitions

```text
unchecked -> evaluated_pass
unchecked -> evaluated_fail
evaluated_fail -> remediated -> evaluated_pass
evaluated_fail -> accepted_cutover -> evaluated_pass
```

- `accepted_cutover` is allowed only for non-additive compatibility changes with explicit migration or cutover notes.
- Escape hatches cannot transition to pass unless transitional and boundary-only constraints are documented.
