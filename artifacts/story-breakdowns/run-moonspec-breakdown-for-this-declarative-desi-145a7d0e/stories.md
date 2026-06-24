# MoonSpec Breakdown Result

Source design: inline user request

Story extraction date: 2026-06-24T20:41:45Z

## Result

ERROR "Imperative input: provide the underlying declarative design or explicitly confirm imperative input"

## Classification

- sourceDocumentClass: imperative-input
- Primary framing: imperative
- Source reference path: none

The supplied input was:

> Create Jira issues to implement this plan based on the suggested implementation order

This is an instruction to create Jira issues from an unspecified plan. It does not describe the desired system state, architecture, contracts, operator-visible behavior, acceptance semantics, or implementation boundaries needed for MoonSpec breakdown.

## Design Summary

No declarative design was available to summarize. The input was classified before extraction, as required by `docs/Workflows/MoonSpecDocumentModel.md`.

## Coverage Points

No `DESIGN-REQ-*` coverage points were extracted because decomposition stopped at the imperative-input gate.

## Ordered Story Candidates

No stories were generated.

## Coverage Matrix

No coverage matrix was generated.

## Dependencies

No story dependencies were generated.

## Out Of Scope

Creating Jira issues, writing `spec.md`, creating directories under `specs/`, planning, task generation, implementation, verification, publishing, pull request creation, or Jira transitions were not performed in this breakdown step.

## Coverage Gate Result

NOT_RUN - input was classified as imperative and no explicit imperative override was provided.

## Required Resolution

Provide a readable declarative design document path or paste the underlying declarative design. Alternatively, explicitly confirm imperative input if decomposition of a checklist or plan is intended.
