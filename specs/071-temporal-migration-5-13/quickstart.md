# Quickstart

## Local Development Bring-up

To start the Temporal environment and workers locally:

```bash
docker compose up -d
```

This will start:
- Temporal Server
- PostgreSQL (temporal-db)
- MoonMind API Server
- MoonMind Worker Fleets

## Running the E2E Test

Once the environment is healthy, run the E2E test to validate task orchestration:

```bash
pytest scripts/temporal_e2e_test.py -v
```

## Cleaning State

To reset the environment:

```bash
docker compose down -v
# Or use the provided cleanup script
bash scripts/temporal_clean_state.sh
```
