# Implementation Plan: Provider Profiles Phase 4 — Runtime Materialization and Secret Refs

**Branch**: `113-provider-profiles-p5` | **Date**: 2026-03-28 | **Spec**: [spec.md](spec.md)

## Summary

Introduces `ProviderProfileMaterializer` and `SecretResolverBoundary` to decouple environment
construction from auth mode branching. Replaces legacy `MANAGED_API_KEY_*` environment markers with
a `secret_refs`-driven pipeline. Refactors `ManagedAgentAdapter` and `AgentLauncher` to use the new
materialization path.

## Technical Context

**Language/Version**: Python 3.11
**Primary Dependencies**: FastAPI, SQLAlchemy, Temporalio
**Storage**: PostgreSQL (ManagedRuntimeProfile, ManagedAgentOAuthSession)
**Testing**: pytest
**Target Platform**: Linux server (Docker worker)
**Project Type**: Single web application

## Constitution Check

No violations. Pre-release project. Backward-compat shims explicitly excluded per compatibility
policy.

## Project Structure

```text
moonmind/workflows/adapters/
├── materializer.py        # ProviderProfileMaterializer
├── secret_boundary.py     # SecretResolverBoundary interface
└── managed_agent_adapter.py  # Updated to use materializer

moonmind/workflows/temporal/runtime/
└── launcher.py            # Updated secret resolution path

tests/unit/
└── services/temporal/runtime/test_launcher.py
```

## Key Decisions

- Secret resolution is performed async in `launcher.py` before calling the sync materializer, to
  avoid rewriting the entire materializer pipeline to async.
- `DictSecretResolver` filters resolved secrets by the requested `secret_refs` keys so that only
  relevant secrets are injected.
- `cmd` is sourced exclusively from `build_command()` (which applies model/effort/prompt overrides);
  the materializer's command output is discarded at the call site.
