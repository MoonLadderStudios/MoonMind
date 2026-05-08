# Quickstart: Publish Remediation Audit Evidence

## Prerequisites

- Work from the repository root.
- Use the active feature directory: `specs/323-publish-remediation-audit`.
- Keep `MM-623` and the original Jira preset brief traceable in every downstream artifact.
- Managed-agent local test mode should use `MOONMIND_FORCE_LOCAL_TESTS=1`.

## Unit Test Strategy

Run focused unit tests while developing:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py
```

Expected unit coverage:
- Remediation artifact type validation and metadata safety.
- Decision log outcome matrix for attempted, skipped, denied, escalated, prevention, and no-PR decisions.
- Remediation summary full field set for repaired, no-action, degraded, unsafe, and escalated outcomes.
- Queryable audit event validation, redaction, timestamp handling, and idempotency keys.
- Target-side annotation payload validation and safe refs.

Before final verification, run the full unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Integration Test Strategy

Run the required hermetic integration suite:

```bash
./tools/test_integration.sh
```

Expected integration coverage:
- Representative remediation run paths publish every applicable remediation artifact and bounded non-applicable reasons.
- Side-effecting action execution publishes action request, action result, verification, decision log, summary, queryable audit event, and target-side annotation evidence.
- Diagnosis-only and no-action runs publish context, plan or bounded no-plan reason, decision log, and summary without action artifacts.
- Degraded and escalated runs expose degraded/evidence-unavailable state in artifacts, summary, and audit trail.
- Artifact metadata and previews do not expose raw URLs, local paths, storage keys, auth headers, tokens, or secrets.

## End-to-End Validation Scenario

1. Create or load a target execution and a remediation execution with a pinned target run.
2. Build the remediation context artifact.
3. Evaluate an allowed remediation action and mutation guard.
4. Execute the action through the remediation evidence tool service.
5. Publish repair/prevention lifecycle summary evidence and decision logs.
6. Persist a compact queryable audit event for the side-effecting action decision.
7. Publish a target-side remediation annotation without removing target-native artifacts.
8. Query artifacts and audit records by remediation identity and target identity.
9. Confirm summary fields, decision refs, audit metadata, and target annotations agree.
10. Confirm no artifact metadata, previews, logs, or audit records expose secret-like values or raw storage/local references.

## Traceability Check

```bash
rg -n "MM-623|DESIGN-REQ-022|DESIGN-REQ-023|DESIGN-REQ-028" specs/323-publish-remediation-audit
```

Expected result: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/remediation-audit-evidence.md`, `quickstart.md`, and final verification evidence preserve the Jira issue key and source coverage IDs.
