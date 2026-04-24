# Quickstart: Temporal Boundary Models

## Focused Validation

Run the focused unit tests:

```bash
./tools/test_unit.sh tests/unit/schemas/test_temporal_boundary_models.py tests/unit/workflows/temporal/test_boundary_inventory.py
```

Run the focused integration-style boundary contract test:

```bash
pytest tests/integration/temporal/test_temporal_boundary_inventory_contract.py -q --tb=short
```

## Full Validation

Run the repository unit suite before completion:

```bash
./tools/test_unit.sh
```

Run hermetic integration CI when Docker is available:

```bash
./tools/test_integration.sh
```

## Expected Result

- The inventory exposes `MM-327` and `TOOL`.
- Contract model tests reject unknown fields and blank identifiers.
- Boundary inventory tests cover activity, workflow, signal, update, query, and Continue-As-New entries.
- Activity names listed as modeled or compatibility-tracked remain present in the default Temporal activity catalog.

## Requirement Coverage

- `FR-001`, `FR-002`, `FR-003`, `SC-001`: Covered by the focused inventory tests that load the deterministic boundary inventory and assert activity, workflow, signal, update, query, and Continue-As-New entries.
- `FR-004`, `SC-002`: Covered by the schema tests that validate aliases, nonblank normalization, duplicate detection, and extra-field rejection.
- `FR-005`, `FR-006`: Covered by inventory assertions that each contract names schema homes, request models, response or snapshot models, and compatibility rationale where needed.
- `FR-007`, `SC-004`: Covered by the local-only handoffs tracker check in the task workflow and by keeping canonical Temporal documentation unchanged.
- `FR-008`, `SC-003`: Covered by the integration boundary contract test that compares inventory names with the default activity catalog and known workflow message constants.
