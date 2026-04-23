# Contract: Shared Executing Shimmer for Status Pills

## Public Surface

MM-488 does not introduce a new status-pill component. It extends the shared Mission Control status-pill contract already used by list, card, and detail surfaces.

Observable surfaces include:
- task list table status pills
- task list card status pills
- task detail execution status pills
- other supported Mission Control status pills that opt into the same shared executing contract

## Host Contract

A supported executing status pill:
- keeps the existing `.status` semantic pill styling
- preserves its visible status text and any existing icon content
- exposes a preferred executing-state selector contract:
  - `data-state="executing"`
  - `data-effect="shimmer-sweep"`
- may also expose the additive fallback marker `.is-executing`

The shimmer contract is additive. It must not require a wrapper that changes layout or pill dimensions.

## Executing-Only Behavior

When the pill represents the executing workflow state:
- the shared shimmer modifier is active
- the effect stays inside the rounded pill bounds
- host text remains readable at all times

When the pill is not executing:
- the shimmer modifier is inactive
- non-executing states do not inherit executing shimmer styling

## Reduced Motion

When `prefers-reduced-motion: reduce` applies to an executing status pill:
- the animated shimmer sweep is disabled
- the pill keeps a non-animated active treatment
- host text remains readable and visually primary

## Reuse Across Surfaces

The same shared contract applies across:
- list/table status pills
- card status pills
- detail status pills

Page-local animation forks are out of contract. Shared styling and shared selectors are required.

## Non-Goals

The contract does not allow:
- task row layout changes
- icon replacement
- text casing changes
- polling or live-update behavior changes
- shimmer on non-executing states
- a separate shimmer implementation per page

## Traceability

Implementation and verification artifacts for this contract must preserve:
- `MM-488`
- `DESIGN-REQ-001`
- `DESIGN-REQ-002`
- `DESIGN-REQ-003`
- `DESIGN-REQ-004`
- `DESIGN-REQ-011`
- `DESIGN-REQ-013`
- `DESIGN-REQ-016`
