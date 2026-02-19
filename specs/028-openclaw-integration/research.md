# Research Findings

## Dedicated Codex Auth Volume
- **Decision**: Provision `openclaw_codex_auth_volume` separate from `codex_auth_volume` and mount it exclusively inside the OpenClaw container.
- **Rationale**: Prevent token clobbering during concurrent refreshes, allow isolated revocation/rotation, and honor the "NOT SHARED" requirement in the objective/spec.
- **Alternatives Considered**: (1) Share `codex_auth_volume` via direct mount—rejected because the Codex CLI refresh process is not concurrency-safe; (2) Use bind mounts into the host filesystem—rejected due to portability and credential sprawl.

## Auth Bootstrap Strategy
- **Decision**: Ship `tools/bootstrap-openclaw-codex-volume.sh` to clone credentials from the primary Codex volume and verify them via `docker compose --profile openclaw run ... codex login status`.
- **Rationale**: Operators already trust the main Codex login flow; cloning keeps onboarding fast while still isolating volumes. Validation ensures copies are complete before enabling OpenClaw.
- **Alternatives Considered**: (1) Manual login every time—slower and error-prone; (2) Sharing the main volume read-only—still risks lock files and refresh collisions.

## Model Lock Enforcement
- **Decision**: Centralize Codex access through an adapter (`openclaw/llm.py`) that reads `OPENCLAW_MODEL`, honors `OPENCLAW_MODEL_LOCK_MODE`, and logs overrides; provide an optional CLI wrapper when shelling out.
- **Rationale**: Keeps enforcement at a single boundary, simplifying tests and auditing. `force` vs `reject` matches policy knobs requested by security.
- **Alternatives Considered**: (1) Let each call site set the model—would drift quickly and is hard to audit; (2) Hardcode model inside environment files—prevents runtime switches required for future migrations.

## Compose Profile & Networking
- **Decision**: Add OpenClaw as a profile-gated service (`profiles: ["openclaw"]`) on the existing `local-network` with `depends_on: api` and volumes `openclaw_codex_auth_volume`, `openclaw_data`.
- **Rationale**: Keeps dev setups unchanged unless they opt-in, ensures API availability, and reuses the single shared network used by other workers.
- **Alternatives Considered**: (1) Always-on service—unacceptable for developers who do not need OpenClaw; (2) New dedicated network—increases orchestration complexity without security gain because traffic is still internal-only.
