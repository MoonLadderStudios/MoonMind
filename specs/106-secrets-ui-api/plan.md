# Implementation Plan: 106-secrets-ui-api

**Feature Branch**: `106-secrets-ui-api`  
**Created**: 2026-03-28  
**Status**: Draft  
**Input**: User description: "Implement Phase 4 of docs/Security/SecretsSystem.md: UI and API Surfaces"

## Constitution Check

- [x] I. Orchestrate, Don't Recreate
- [x] II. One-Click Agent Deployment
- [x] III. Avoid Vendor Lock-In
- [x] IV. Own Your Data
- [x] V. Skills Are First-Class and Easy to Add
- [x] VI. The Bittersweet Lesson
- [x] VII. Powerful Runtime Configurability
- [x] VIII. Modular and Extensible Architecture
- [x] IX. Resilient by Default
- [x] X. Facilitate Continuous Improvement
- [x] XI. Spec-Driven Development Is the Source of Truth
- [x] XII. Canonical Documentation
- [x] XIII. Pre-Release Velocity

## Proposed Changes

### 1. API Service (Backend)

We will introduce a new router for Secrets Management.

#### `api_service/api/routers/secrets.py` (NEW)
- `POST /api/v1/secrets`: Create a new secret
- `PUT /api/v1/secrets/{slug}`: Update a secret directly
- `POST /api/v1/secrets/{slug}/rotate`: Rotate an existing secret with explicit logging
- `GET /api/v1/secrets`: List all secrets (returning metadata only: slug, status, updated_at, details) without the `ciphertext`
- `PUT /api/v1/secrets/{slug}/status`: Update the status of a secret (disable, reactivate)
- `DELETE /api/v1/secrets/{slug}`: Hard-delete or mark-deleted (we will hard-delete using a new service method `delete_secret` or mark as `DELETED` status)
- `GET /api/v1/secrets/{slug}/validate`: Check if a reference resolves successfully without returning the content

#### `api_service/services/secrets.py` (MODIFY)
- Add `delete_secret` method.
- Return Pydantic schemas in `list_metadata` rather than raw sqlalchemy row outputs to ensure `ciphertext` stays suppressed.

#### `api_service/api/schemas.py` (MODIFY)
- Add Pydantic validation schemas for Secret create/update requests and SecretMetadata responses to guarantee no `ciphertext` fields are serialized out.

#### `api_service/main.py` (MODIFY)
- Mount the new `secrets.py` router under `/api/v1/secrets`.

### 2. Frontend Web Application

We will build the standard operator-facing UI in the frontend repository (`frontend/`).

#### `frontend/src/routes/secrets` (or equivalent) (NEW)
- Main Secrets Dashboard:
  - Table of configured secrets with dynamic status pills (Active, Disabled, Rotated, Missing).
  - List metadata (slug, created, imported_from) but mask/hide actual secret values.

#### `frontend/src/components/secrets/SecretManager.tsx` (NEW)
- Add Secret Modal/Form:
  - Simple `slug`, `value` entry for first-run additions (e.g. `ANTHROPIC_API_KEY`).
  - Editing an existing secret provides a blank input with a "Leave blank to keep current value" placeholder.
- Rotate action with confirmation.
- Delete/Disable actions.

#### `frontend/src/routes/dashboard` (or equivalent) (MODIFY)
- "First-run path" banner: prompt the user to configure Provider API Keys and GitHub PAT.

## Complexity Tracking

- Backend schema additions are straightforward Pydantic models.
- The new router is standard FastAPI.
- Frontend React additions will follow the existing project structure and component library.

## Alternatives Considered

- Using a third-party managed secrets UI (like Vault UI). Rejected per Constitution Principle II (One-Click Agent Deployment, local-first).
