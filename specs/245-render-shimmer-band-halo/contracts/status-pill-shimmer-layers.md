# Contract: Themed Shimmer Band and Halo Layers

## Public Surface

MM-489 refines the shared executing shimmer presentation already used by Mission Control status pills. It does not introduce a new status-pill component or a page-local animation fork.

Observable surfaces include:
- task list table status pills
- task list card status pills
- task detail execution status pills
- any other supported Mission Control status pill that already opts into the shared executing shimmer contract

## Layered Visual Contract

A supported executing status pill:
- keeps the existing `.status` and `.status-running` host styling
- preserves its visible status text and any existing host semantics
- keeps the base executing appearance visible beneath the shimmer treatment
- renders a bright diagonal core band
- renders a wider, dimmer trailing halo
- keeps the layered treatment bounded to the pill interior

## Theme And Token Contract

The MM-489 shimmer treatment:
- derives its visible color roles from existing MoonMind theme tokens
- uses reusable effect tokens or equivalent variables for the tunable values the story owns
- must not introduce disconnected one-off palette values that break light/dark theme coherence

## Interaction And Layout Contract

The layered shimmer treatment:
- remains additive to the existing host pill
- does not require wrappers that change layout or pill dimensions
- does not intercept pointer behavior or change hit testing
- preserves readable host text while the layered treatment is active

## Reuse Across Surfaces

The same shared layered shimmer treatment applies across:
- list/table status pills
- card status pills
- detail status pills

Page-local forks are out of contract.

## Non-Goals

The contract does not allow:
- a separate status-pill component for MM-489
- non-executing shimmer inheritance
- unrelated workflow-state styling changes
- task-row layout changes
- icon replacement
- live-update or polling behavior changes

## Traceability

Implementation and verification artifacts for this contract must preserve:
- `MM-489`
- `DESIGN-REQ-005`
- `DESIGN-REQ-006`
- `DESIGN-REQ-008`
- `DESIGN-REQ-009`
- `DESIGN-REQ-012`
- `DESIGN-REQ-015`
