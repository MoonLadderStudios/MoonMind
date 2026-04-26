# Contract: Grid UI Marker Diagnostic Evidence

## Purpose

Diagnostics for this story must provide enough structured evidence to distinguish producer churn from renderer churn during Grid UI marker/decal operations.

## Required Event Fields

| Field | Requirement |
| --- | --- |
| `source` | Identifies the producer, renderer, or lifecycle source of the operation. |
| `markerType` | Identifies the marker/decal category affected by the operation. |
| `reason` | Explains why the operation occurred. |
| `ownerController` | Identifies the owning controller when available, or records an explicit unknown/null owner state. |
| `tileCount` | Reports how many tiles or locations are affected. |
| `operationType` | Identifies spawn, queued spawn, clear, clear all, decal spawn, or decal clear operation category. |

## Validation Expectations

- Producer-originated events and renderer-originated events must be distinguishable without relying on log message prose.
- Clear operations that affect zero tiles must still include all required fields.
- Diagnostic evidence must be available to automated tests or test fixtures in the target project.
- Event shape and validation evidence must preserve `MM-525` traceability.
