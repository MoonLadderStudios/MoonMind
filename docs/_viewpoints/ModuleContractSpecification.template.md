# <Module Name> — Module Contract Specification

**Status:** Draft
**Document Class:** Canonical declarative
**Viewpoint:** Module Contract Specification
**Owner:** <team or person>
**Last Updated:** <YYYY-MM-DD>
**Audience:** <primary readers>
**Related:** <links to related canonical docs and/or source issues>

> Template per the [MoonSpec Documentation Architecture Standard](../DocumentationArchitecture.md). Delete this line and any sections that do not apply.

## 1. Purpose

What contract this module exposes and who consumes it.

## 2. Interface

The operations, inputs, and outputs the module guarantees (signatures, payload shapes, status values).

## 3. Guarantees & invariants

What callers can rely on: ordering, idempotency, error semantics, compatibility commitments.

### CONTRACT-001 <concise stable contract heading>

The durable interface guarantee this contract owns.

## 4. Failure modes

How the contract behaves on invalid input, downstream failure, and degraded conditions.

## 5. Versioning & compatibility

How changes to this contract are versioned and what compatibility callers can expect. For Temporal-facing contracts (workflows, activities, signals, updates), explicitly address replay safety, in-flight compatibility, and workflow boundary test coverage.

### TEST-001 <concise stable verification heading>

The required boundary or regression coverage for this contract.
