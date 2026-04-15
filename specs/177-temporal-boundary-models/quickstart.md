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
