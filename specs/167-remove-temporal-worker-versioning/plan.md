# Implementation Plan: Remove Temporal Worker Deployment Routing

**Branch**: `167-remove-temporal-worker-versioning`  
**Date**: 2026-04-13  
**Spec**: [spec.md](./spec.md)

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Temporal Python SDK, Pydantic settings, pytest  
**Storage**: No storage schema changes  
**Testing**: Focused unit tests for worker runtime and deployment-safety gates; full unit suite if time allows  
**Target Platform**: Docker Compose MoonMind runtime workers  
**Project Type**: Backend/runtime cleanup  
**Constraints**: Remove the old internal contract entirely; no compatibility aliases or disabled-mode fallback

## Constitution Check

- **II. One-Click Agent Deployment**: PASS. Removing current-version routing improves fresh compose startup reliability.
- **VII. Powerful Runtime Configurability**: PASS. Dead configuration is removed rather than retained.
- **IX. Resilient by Default**: PASS. Replay safety remains enforced through patch/cutover evidence.
- **XI. Spec-Driven Development**: PASS. Spec, plan, and tasks are included.
- **XIII. Delete, Don't Deprecate**: PASS. The worker-versioning path is deleted rather than hidden behind aliases.

## Implementation Approach

1. Remove Temporal Worker Deployment imports and helper functions from worker startup.
2. Construct workers with task queue, workflow/activity registrations, concurrency, and no versioning kwargs.
3. Delete the worker-versioning settings field, validator, and `.env-template` entry.
4. Remove worker-versioning requirements from deployment-safety validation and tests.
5. Delete the dedicated Worker Deployment runbook and update canonical docs/specs to direct polling.
6. Verify with focused unit tests and search for stale references.
