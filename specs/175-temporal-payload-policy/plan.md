# Implementation Plan: Temporal Payload Policy

**Branch**: `175-temporal-payload-policy` | **Date**: 2026-04-15 | **Spec**: `specs/175-temporal-payload-policy/spec.md`

## Summary

Add a reusable compact Temporal mapping validator and apply it to the existing Temporal-facing metadata/provider-summary escape hatches used by managed-session, agent-runtime, integration signal, and integration lifecycle models. Preserve existing activity/workflow names and existing artifact-ref fields.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Pydantic v2, Temporal Python SDK
**Storage**: Existing artifact refs only; no new storage
**Unit Testing**: Focused schema tests during iteration; `./tools/test_unit.sh` for final required unit-suite verification.
**Integration Testing**: No new compose-backed integration fixture is required because this story changes schema-level Temporal payload contracts only; if workflow/activity invocation code changes while implementing this story, run `./tools/test_integration.sh` to cover hermetic Temporal boundaries.
**Source Design**: `docs/Temporal/TemporalTypeSafety.md` sections 9 and 12

## Constitution Check

- **I. Orchestrate, Don't Recreate**: PASS. The change strengthens orchestration contracts without replacing agents.
- **II. One-Click Agent Deployment**: PASS. No deployment prerequisite changes.
- **III. Avoid Vendor Lock-In**: PASS. Artifact refs and JSON policy remain provider-neutral.
- **IV. Own Your Data**: PASS. Large payloads remain in MoonMind artifact storage instead of Temporal history.
- **V. Skills Are First-Class and Easy to Add**: PASS. No skill runtime contract changes.
- **VI. Evolving Scaffolds**: PASS. The policy is small and replaceable behind schema validation.
- **VII. Powerful Runtime Configurability**: PASS. No hardcoded runtime behavior is introduced beyond schema bounds.
- **VIII. Modular and Extensible Architecture**: PASS. Validation lives in a shared schema helper.
- **IX. Resilient by Default**: PASS. Histories remain compact and replay-friendly; Temporal names are unchanged.
- **X. Facilitate Continuous Improvement**: PASS. Tests provide objective verification.
- **XI. Spec-Driven Development**: PASS. This spec/plan/tasks set tracks the runtime implementation.
- **XII. Canonical Docs Separate Desired State From Migration Backlog**: PASS. Canonical docs are not changed.
- **XIII. Pre-Release Compatibility Policy**: PASS. No internal compatibility alias or fallback semantic transform is added.

## Project Structure

```text
moonmind/schemas/temporal_payload_policy.py
moonmind/schemas/agent_runtime_models.py
moonmind/schemas/managed_session_models.py
moonmind/schemas/temporal_models.py
moonmind/schemas/temporal_signal_contracts.py
tests/schemas/test_temporal_payload_policy.py
tests/schemas/test_temporal_activity_models.py
```

## Implementation Strategy

1. Add a reusable validator for compact JSON mappings that rejects raw bytes, unsupported value types, large strings, and oversized serialized mappings.
2. Apply the validator to Temporal-facing `metadata` and `providerSummary` fields that function as approved escape hatches.
3. Add schema tests that prove rejection of raw bytes/large bodies and acceptance of compact artifact refs.
4. Re-run existing explicit binary serializer tests to confirm `Base64Bytes` behavior remains intact.
5. Run the full required unit suite; run hermetic integration only if the implementation expands from schema models into workflow/activity invocation wiring.

## Complexity Tracking

No constitution violation or cross-module migration complexity is introduced. The only compatibility-sensitive choice is preserving existing field names and allowing compact existing metadata keys while bounding payload size and raw bytes.
