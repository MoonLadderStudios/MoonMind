# Quickstart: Provider Profiles Phase 2

## Setup

1. Check out the branch: `git checkout 110-provider-profiles-p2`
2. Spin up services: `docker compose up -d`
3. Enter web container: `docker compose exec api bash`
4. Apply migrations: `alembic upgrade head`

## Testing the Persistence Improvements
1. Invoke the API to create a new profile with raw secrets in the payload. Observe the 422 Unprocessable Entity block.
2. Complete an OAuth flow in the frontend. Confirm that the finalized agent session generated a `ManagedAgentProviderProfile` with `default_model` correctly extracted from the identity provider.
