# Data Model: Managed Agents Authentication

## Database Schema

`managed_agent_auth_profiles` (PostgreSQL via SQLAlchemy)

- `profile_id` (String, PK): E.g. "gemini_ultra_nsticco"
- `runtime_id` (String, Index): E.g. "gemini_cli", "claude_code"
- `auth_mode` (String): "oauth" or "api_key"
- `volume_ref` (String, Optional): Docker volume name for oauth mode
- `volume_mount_path` (String, Optional): Container-side mount path
- `account_label` (String): Human-readable identifier
- `api_key_ref` (String, Optional): Secret store reference for API keys
- `max_parallel_runs` (Integer): Concurrency limit for this profile
- `cooldown_after_429` (Integer): Cooldown duration in seconds
- `rate_limit_policy` (String): "backoff" or similar
- `enabled` (Boolean): Master switch for the profile
- `created_at` (DateTime)
- `updated_at` (DateTime)

## Temporal Workflow State

`AuthProfileManager` singleton internal state:
- `leases`: Dict tracking which agent workflow ID currently occupies which slots
- `cooldowns`: Dict tracking which profiles are currently disabled and until when
- `queue`: ordered list of awaiting connection requests
