# Quickstart

To verify the provider profiles feature locally:

1. Bring up your local environment: `docker compose up -d`
2. Apply migrations (if not auto-applied during deploy): `docker exec moonmind-api-1 alembic upgrade head`
3. Hit the Task Dashboard: `http://localhost:8000`
4. Attempt to execute a `claude_code` run while passing `provider_id='minimax'` via the UI dropdown. Watch it correctly spawn inside the Worker with MiniMax environment variable overlays active.
