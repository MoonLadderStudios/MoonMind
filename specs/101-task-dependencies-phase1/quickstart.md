# Quickstart: Task Dependencies Phase 1

## Verify the changes

```bash
# 1. Run unit tests
./tools/test_unit.sh

# 2. Verify enum value exists
python3 -c "from api_service.db.models import MoonMindWorkflowState; print(MoonMindWorkflowState.WAITING_ON_DEPENDENCIES)"

# 3. Verify workflow constant exists
python3 -c "from moonmind.workflows.temporal.workflows.run import STATE_WAITING_ON_DEPENDENCIES; print(STATE_WAITING_ON_DEPENDENCIES)"
```

## Apply the migration (Docker Compose)

```bash
docker compose exec api alembic upgrade head
```

## Verify migration

```bash
docker compose exec db psql -U moonmind -c "SELECT enum_range(NULL::moonmindworkflowstate);"
# Expected output includes 'waiting_on_dependencies' in the enum range
```
