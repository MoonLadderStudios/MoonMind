# Quickstart: Durable Task Edit Reconstruction

## Focused Validation

Run frontend reconstruction tests:

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx
```

Run Python API and workflow tests:

```bash
./tools/test_unit.sh tests/contract/test_temporal_execution_api.py tests/unit/workflows/temporal
```

Run hermetic integration tests when Docker Compose is available:

```bash
./tools/test_integration.sh
```

## End-to-End Scenarios

1. Create a simple inline-instruction `MoonMind.Run`; verify execution detail exposes `taskInputSnapshot.reconstructionMode=authoritative`; open Edit and Rerun and confirm the draft matches the original form.
2. Create an oversized instruction task; verify the snapshot carries refs for large content and the draft restores full instructions from artifact storage.
3. Create a skill-only task with no free-text instructions; verify Rerun restores selected skill and structured inputs and does not fail on missing instructions.
4. Create a template-derived task; verify Rerun restores template identity/version/inputs and customized steps without reading the current template catalog as source of truth.
5. Create a multi-step task with attachments; verify every step and attachment ref round-trips and unreadable attachments produce a bounded warning or disabled reason.
6. Create a rerun from a source execution; verify the rerun request creates a new snapshot and preserves source lineage.
7. Open a pre-cutover execution without a snapshot and only a plan artifact; verify the UI shows degraded read-only recovery copy and blocks submission until replacement input is entered.

## Red-First Expectation

Before implementation, new tests for skill-only reconstruction, snapshot descriptor exposure, snapshot artifact linkage, and Temporal update/rerun ref handling should fail because the current system reconstructs from normalized parameters and optional `inputArtifactRef` only.
