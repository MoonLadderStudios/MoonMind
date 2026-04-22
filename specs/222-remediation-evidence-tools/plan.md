# Implementation Plan: Remediation Evidence Tools

**Branch**: `run-jira-orchestrate-for-mm-433-expose-t-1a03b536` | **Date**: 2026-04-21 | **Spec**: `specs/222-remediation-evidence-tools/spec.md`
**Input**: Single-story Jira Orchestrate request for MM-433 / STORY-003.

## Summary

Implement MM-433 by adding a narrow typed evidence-tool service at the Temporal remediation boundary. The service consumes the `remediation.context` artifact produced by MM-432 and exposes context, artifact-read, bounded log-read, and optional live-follow operations. It does not execute remediation actions or create new privileges; every target evidence read is gated by the persisted remediation link and the context artifact.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: SQLAlchemy async ORM, existing Temporal artifact service, remediation link/context models
**Storage**: No new persistent tables; live-follow cursor persistence is provided as an injected handoff callback for the caller/runtime.
**Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py`

## Constitution Check

- I. Orchestrate, Don't Recreate: PASS. Adds a MoonMind boundary over existing evidence surfaces.
- II. One-Click Agent Deployment: PASS. No new service dependency.
- III. Avoid Vendor Lock-In: PASS. No provider-specific behavior.
- IV. Own Your Data: PASS. Reads MoonMind-owned context and artifact refs.
- V. Skills Are First-Class: PASS. Provides a reusable service surface for remediation skills.
- VI. Replaceability: PASS. Thin typed boundary with injected readers/followers.
- VII. Runtime Configurability: PASS. Evidence policy comes from the request/context.
- VIII. Modular Architecture: PASS. New remediation tool module only.
- IX. Resilient by Default: PASS. Invalid or missing context fails deterministically.
- X. Continuous Improvement: PASS. Cursor handoff supports resumable follow behavior.
- XI. Spec-Driven Development: PASS. This artifact set defines MM-433.
- XII. Docs Separation: PASS. Canonical docs remain desired-state; this is implementation traceability.
- XIII. Pre-release Compatibility: PASS. Adds the canonical surface without aliases.

## Scope

Code:

- `moonmind/workflows/temporal/remediation_tools.py`
- `moonmind/workflows/temporal/__init__.py`

Tests:

- `tests/unit/workflows/temporal/test_remediation_context.py`

## Out Of Scope

- Action execution registry.
- Lock acquisition or action idempotency ledger.
- New REST or MCP transport.
- Raw task-run store access from the remediator.
