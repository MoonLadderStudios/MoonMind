# Contract: Calm Shimmer Motion and Reduced-Motion Fallback

## Public Surface

MM-490 refines the shared executing shimmer contract already used by Mission Control status pills. It does not introduce a new status-pill component or a page-local animation fork.

Observable surfaces include:
- task list table status pills
- task list card status pills
- task detail execution status pills
- any other supported Mission Control status pill that already opts into the shared executing shimmer contract

## Motion Contract

A supported executing status pill:
- keeps the existing shared shimmer selector contract
- animates the shimmer left-to-right across the pill when motion is allowed
- keeps the visible shimmer bounded to the pill's rounded shape
- uses a calm sweep cadence rather than an urgent or warning-like pulse
- reserves an idle gap so shimmer cycles do not overlap visually
- places the strongest emphasis near the pill center during the sweep

## Reduced-Motion Contract

When `prefers-reduced-motion: reduce` applies to an executing status pill:
- animated shimmer movement is disabled
- a static active highlight remains
- the executing state still reads as active without requiring motion
- non-executing pills do not inherit the reduced-motion executing treatment

## Executing-Only Behavior

When the pill represents the executing workflow state:
- the MM-490 motion profile may be active
- the reduced-motion replacement may be active when motion reduction is preferred

When the pill is not executing:
- the MM-490 motion profile is inactive
- the reduced-motion replacement treatment is inactive

## Reuse Across Surfaces

The same shared motion contract applies across:
- list/table status pills
- card status pills
- detail status pills

Page-local motion forks are out of contract.

## Non-Goals

The contract does not allow:
- shimmer activation for non-executing states
- warning-like pulse behavior
- overlapping sweep cycles
- replacing the reduced-motion path with no active-state cue at all
- a separate status-pill component for MM-490

## Traceability

Implementation and verification artifacts for this contract must preserve:
- `MM-490`
- `DESIGN-REQ-007`
- `DESIGN-REQ-010`
- `DESIGN-REQ-012`
- `DESIGN-REQ-014`
