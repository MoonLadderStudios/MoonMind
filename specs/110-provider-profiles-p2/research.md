# Research & Design Decisions: Provider Profiles Phase 2

## Context and Technology Stack
- **Language/Version**: Python 3.11+
- **Database**: PostgreSQL (via SQLAlchemy / Alembic)
- **Primary Dependencies**: FastAPI, Pydantic, Temporal
- **Storage**: RDBMS JSONB columns for profile models and config structures

## Technical Clarifications

### 1. Database Model Additions
**Decision**: Add `default_model` and `model_overrides` using `JSONB` or structured types in the SQLAlchemy `ManagedAgentProviderProfile` model, aligned with `docs/Tasks/SkillAndPlanContracts.md`.
**Rationale**: Pydantic schemas already expect JSON representations for these dictionaries or objects.
**Alternatives considered**: Multiple relational tables rather than JSONB. Rejected because the structure of model overrides is profile-specific and deeply nested, making JSONB the natural PostgreSQL feature fit.

### 2. Service Layer Validation
**Decision**: Ensure `ProviderProfileService.create` and `.update` perform domain-level validation using `ManagedAgentProviderProfileCreate` and `Update` Pydantic models. We will enforce that raw secret strings are rejected and that users pass `SecretRef` objects instead.
**Rationale**: Security constraint; the system must not leak raw secrets to the database via profile rows.
**Alternatives considered**: Validating constraints only in the UI/API. Rejected because it violates defense-in-depth and the explicit constraint in `005-ProviderProfilesPlan.md`.

### 3. OAuth Profile Registration Flow
**Decision**: Modify the OAuth callback logic (`oauth_session_service.py` / `auth_profile_service.py`) to create a `ManagedAgentProviderProfile` instead of legacy structures.
**Rationale**: Explicit task C from Phase 2 plan to eliminate usage of legacy `AuthProfile` persistence.

### 4. Migration Strategy
**Decision**: Create a single Alembic migration that adds the missing columns to `managed_agent_provider_profiles` and handles data migration for any legacy rows using an `UPDATE ... FROM ...` style script or simple SQLAlchemy core updates. Replace the `AuthProfileManager` string constants where possible.
**Rationale**: Explicitly mandated by DOC-REQ-004. Compatibility layers and dual-reads are forbidden.
