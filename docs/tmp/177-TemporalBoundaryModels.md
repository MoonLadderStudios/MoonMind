# Temporal Boundary Models Tracker

This tracker belongs to MM-327 / `specs/177-temporal-boundary-models`. It records migration and compatibility notes for the Temporal boundary inventory so canonical documentation can remain desired-state-only.

## Covered In This Story

- Deterministic inventory model for representative public Temporal boundary contracts.
- Pydantic v2 schema models for inventory entries and model references.
- Tests that preserve the Jira source key `MM-327` and TOOL board scope.
- Tests that compare covered activity names against the default activity catalog.

## Compatibility-Sensitive Follow-Up

- Expand the inventory to every activity in `build_default_activity_catalog()` after each activity family has named request and response models.
- Convert remaining workflow call sites that still pass raw dictionaries to typed wrappers once replay and in-flight compatibility evidence exists.
- Keep compatibility shims documented at public Temporal boundaries only; remove entries from this tracker when they graduate to fully modeled contracts.

## Current Repository Note

The Jira brief references `docs/tmp/story-breakdowns/mm-316-breakdown-docs-temporal-temporaltypesafet-c8c0a38c/stories.json`. This checkout contains the equivalent handoff at `docs/tmp/story-breakdowns/breakdown-docs-temporal-temporaltypesafety-md-in-9e0bd9a2/stories.json`.
