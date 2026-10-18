# <Interface name> contract

Optional outline from the [documentation guide](../DocumentationArchitecture.md). Keep only useful sections, then remove this note.

## Provider and consumers

What provides this interface and who uses it.

## Inputs and outputs

Operations, payloads, status values, and defaults that callers need to understand. Link to schemas where they already express the details clearly.

## Guarantees and failure behavior

Relevant ordering, idempotency, errors, and recovery behavior, with reasons for non-obvious constraints.

## Compatibility

What callers can rely on when the interface changes. For Temporal interfaces, explain relevant replay and in-flight behavior and link to coverage that exercises the actual boundary.
