# Contract: Shimmer Quality Regression Guardrails

## Public Surface

MM-491 validates the shared executing shimmer treatment already used by Mission Control status pills. It does not introduce a new component, route, or page-local variant.

Observable surfaces include:
- task list table status pills
- task list card status pills
- task detail execution status pills
- any other supported Mission Control status pill that already opts into the shared executing shimmer contract

## Guardrail Contract

A supported executing status pill:
- keeps the existing shared shimmer selector contract
- keeps status text readable while the shimmer sweeps across the pill
- keeps the visible shimmer bounded to the pill's rounded shape
- does not interact with scrollbars
- does not change pill dimensions or surrounding layout
- remains an intentional active treatment in light and dark themes

## Reduced-Motion Contract

When `prefers-reduced-motion: reduce` applies to an executing status pill:
- animated shimmer movement is disabled
- a static active fallback remains
- the executing state still reads as active without requiring motion
- non-executing pills do not inherit the reduced-motion executing treatment

## State-Matrix Contract

When the pill represents the executing workflow state:
- the shared shimmer treatment may be active
- the reduced-motion fallback may be active when motion reduction is preferred

When the pill represents a listed non-executing workflow state:
- the shared shimmer treatment is inactive
- the reduced-motion fallback is inactive

Future variants such as `finalizing` remain out of contract until a later story explicitly broadens the state matrix.

## Non-Goals

The contract does not allow:
- shimmer activation for non-executing states
- replacing the shimmer with an unrelated alternate effect family to satisfy the story
- layout-shifting wrappers or page-local variants
- removing the reduced-motion active cue entirely

## Traceability

Implementation and verification artifacts for this contract must preserve:
- `MM-491`
- `DESIGN-REQ-004`
- `DESIGN-REQ-009`
- `DESIGN-REQ-011`
- `DESIGN-REQ-014`
- `DESIGN-REQ-016`
