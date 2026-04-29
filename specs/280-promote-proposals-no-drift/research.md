# Research: Promote Proposals Without Live Preset Drift

## Decision 1: Remove full task payload replacement from proposal promotion

**Decision**: Stop accepting `taskCreateRequestOverride` in promotion requests and service calls.

**Rationale**: The reviewed proposal payload is the artifact the operator approved. A full override can replace steps, instructions, authored preset bindings, or provenance at promotion time, creating the live-preset drift MM-560 forbids.

**Alternatives considered**:

- Deep-compare override to stored payload: rejected because it would preserve a complex compatibility path and still invite hidden semantic changes.
- Allow only matching overrides: rejected because bounded first-class promotion controls are clearer.

## Decision 2: Keep `runtimeMode` as a bounded promotion control

**Decision**: Keep runtime selection as an explicit bounded promotion option and apply it inside the service after loading the stored payload.

**Rationale**: Runtime selection changes execution placement, not the reviewed task content. Applying it service-side avoids constructing a replacement envelope in the router.

## Decision 3: Rely on `CanonicalTaskPayload` for unresolved Preset rejection

**Decision**: Use existing task contract validation to reject unresolved `type: "preset"` steps during promotion.

**Rationale**: The task contract already defines executable submission as Tool or Skill steps. Adding a targeted regression test is enough to prove the story behavior without duplicating validation logic.
