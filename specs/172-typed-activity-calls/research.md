# Research: Typed Temporal Activity Calls

## Shared Temporal Data Converter

Decision: Add an importable MoonMind data converter constant that points to Temporal's Pydantic-aware converter and use it from Temporal client construction.
Rationale: The source design treats converter policy as part of the contract. A named project constant makes client/worker drift testable and avoids scattered direct imports.
Alternatives considered: Continue importing `pydantic_data_converter` directly in each module; rejected because it is harder to audit and assert as a shared contract.

## Typed Activity Request Models

Decision: Add strict Pydantic request models in `moonmind/schemas/temporal_activity_models.py` for managed runtime status/fetch/cancel inputs and external run identifier inputs.
Rationale: These are high-risk workflow history payloads that currently use dicts and scalar shims. Models with `extra="forbid"` make the canonical wire shape explicit.
Alternatives considered: Dataclasses; rejected because the project standard for Temporal boundary I/O is Pydantic v2.

## Compatibility Boundary

Decision: Keep legacy dict alias handling only inside activity runtime entry validation, then proceed with canonical typed models.
Rationale: In-flight histories may still contain `external_id`, `externalId`, `run_id`, or `runId`. Accepting those at the public edge protects replay while avoiding provider-shaped data inside workflow logic.
Alternatives considered: Remove all legacy aliases immediately; rejected because Temporal-facing contracts are compatibility-sensitive for in-flight histories.

## Typed Execution Facade

Decision: Extend `execute_typed_activity` overloads for migrated runtime activity names and use it from the AgentRun activity router.
Rationale: Workflow code needs a single typed helper so static analysis sees request and response contracts while the existing route catalog still controls task queues and timeouts.
Alternatives considered: Call `workflow.execute_activity` directly with typed models; rejected because it would not satisfy the typed facade requirement and would leave call sites inconsistent.

## Provider Data Boundary

Decision: Continue allowing adapters to interact with provider-shaped data inside activities, but require workflow-side code to validate responses into `AgentRunHandle`, `AgentRunStatus`, and `AgentRunResult`.
Rationale: Workflows must store canonical MoonMind state in history. Provider details remain in bounded metadata only after normalization.
Alternatives considered: Store raw provider responses and normalize later; rejected because it leaks provider-specific history shape into workflow replay.
