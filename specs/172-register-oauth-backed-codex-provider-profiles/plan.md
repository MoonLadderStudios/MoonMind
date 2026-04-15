# Implementation Plan: Register OAuth-backed Codex Provider Profiles

**Spec**: `specs/172-register-oauth-backed-codex-provider-profiles/spec.md`  
**Intent**: Runtime  
**Source Design**: `docs/ManagedAgents/OAuthTerminal.md`

## Summary

Tighten the OAuth/Profile boundary for Codex OAuth enrollment. The implementation applies Codex auth-volume defaults at OAuth session creation, requires volume verification during finalization, and registers Provider Profiles with the OAuth-backed materialization shape required by the design.

## Technical Context

- API surface: `api_service/api/routers/oauth_sessions.py`
- Request schema: `api_service/api/schemas_oauth_sessions.py`
- Workflow activity boundary: `moonmind/workflows/temporal/activities/oauth_session_activities.py`
- Verification boundary: `moonmind/workflows/temporal/runtime/providers/volume_verifiers.py`
- Tests: `tests/unit/api_service/api/routers/test_oauth_sessions.py`, `tests/unit/auth/test_oauth_session_activities.py`, `tests/unit/auth/test_volume_verifiers.py`

## Constitution Check

- I Orchestrate, Don't Recreate: PASS. The feature uses existing OAuth workflow, Provider Profile, and verifier boundaries.
- II One-Click Agent Deployment: PASS. Defaults match local Compose auth-volume conventions.
- III Avoid Vendor Lock-In: PASS. Codex/OpenAI defaults are provider-specific metadata behind the existing Provider Profile model.
- IV Own Your Data: PASS. Credentials remain in operator-controlled Docker volumes.
- V Skills Are First-Class and Easy to Add: PASS. No executable skill contract changes.
- VI Replaceability and Evolution: PASS. The change tightens existing contracts rather than adding scaffolding.
- VII Runtime Configurability: PASS. Explicit request values can override defaults where provided.
- VIII Modular and Extensible Architecture: PASS. API, activity, and verifier responsibilities stay separated.
- IX Resilient by Default: PASS. Failed or unavailable verification produces deterministic failed sessions.
- X Facilitate Continuous Improvement: PASS. Failure reasons are persisted on session rows.
- XI Spec-Driven Development: PASS. This spec, plan, and tasks trace the implementation.
- XII Canonical Documentation Separation: PASS. Runtime implementation notes stay in spec artifacts.
- XIII Pre-Release Velocity: PASS. No compatibility aliases or hidden fallbacks were introduced.

## Design Decisions

- Default Codex OAuth sessions to `codex_auth_volume` and `/home/app/.codex` at API ingress so the workflow receives a complete volume target.
- Store provider metadata in OAuth session `metadata_json` as compact strings, not credentials.
- Make API finalization fail when verification fails. The workflow path already required verification; this removes API divergence.
- Register OAuth-backed Provider Profiles with `RuntimeMaterializationMode.OAUTH_HOME` from both the API finalizer and the Temporal activity finalizer.
- Verify Codex credentials relative to the mounted Codex home so `/home/app/.codex/auth.json` is checked correctly.

## Test Strategy

- Unit tests for API OAuth creation defaults and finalization behavior.
- Unit tests for activity-side profile registration shape.
- Unit tests for verifier path behavior and Docker command construction.
- Final verification through targeted pytest and full unit runner.

