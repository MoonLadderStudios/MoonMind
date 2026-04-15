# Temporal Type Safety Story Breakdown

- Source design: `docs/Temporal/TemporalTypeSafety.md`
- Original source document reference path: `docs/Temporal/TemporalTypeSafety.md`
- Story extraction date: `2026-04-15T07:09:12Z`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The Temporal Type Safety design makes every MoonMind Temporal workflow, activity, message, query, and Continue-As-New boundary an explicit serialized contract. It standardizes Pydantic v2 request/response models, shared payload conversion, typed workflow call sites, managed-session message contracts, artifact-reference handling for large payloads, and compatibility practices that protect replay and in-flight executions. The design is normative desired state: implementation tracking remains in docs/tmp, compatibility shims stay narrow and temporary, and completion requires schema, boundary round-trip, replay/in-flight, and static-analysis coverage.

## Coverage Points

- **DESIGN-REQ-001 - Temporal boundaries are public contracts** (requirement, 1 Purpose; 3.1 Boundary types are real contracts): Workflow inputs, activity names and payloads, messages, queries, and continuation payloads must be modeled and reviewed as serialized API contracts rather than convenience dictionaries.
- **DESIGN-REQ-002 - One structured argument and return model per boundary** (requirement, 3.2 One structured argument per boundary; 4.1 Request/response models): New Temporal entrypoints use one named request model and one named response model when data is returned; raw dictionaries and multiple loose scalar parameters are not target-state interfaces.
- **DESIGN-REQ-003 - Pydantic v2 canonical model policy** (constraint, 3.3 Pydantic v2; 4.2 Model configuration): Temporal boundary models default to Pydantic v2 with strict extra handling, stable aliases, explicit identifier normalization, enums/literals for closed sets, and tightly scoped metadata bags.
- **DESIGN-REQ-004 - Shared Temporal data conversion** (integration, 3.4 The data converter is part of the contract): Temporal clients and workers must share a Pydantic-aware payload conversion policy with explicit serializers and immediate validation at any legacy JSON-shaped boundary.
- **DESIGN-REQ-005 - Compatibility and replay safety outrank model tidiness** (migration, 3.5 Compatibility outranks tidiness; 10 Compatibility and evolution rules): Contract evolution is additive-first, preserves activity/workflow/message names where required, and uses Worker Versioning, patching, replay testing, or explicit cutover plans for non-additive changes.
- **DESIGN-REQ-006 - Workflow determinism remains mandatory** (constraint, 3.6 Determinism remains the workflow rule): Typing boundaries must not move nondeterministic work into workflows; external reads, clocks, subprocesses, network calls, filesystem I/O, and provider inspection stay in activities.
- **DESIGN-REQ-007 - Approved schema module ownership** (artifact, 4.1 Request/response models; 14 Canonical implementation anchors): Activity, managed-session, and runtime execution models belong in the named schema modules or established domain schema modules, with typed_execution and activity catalog as implementation anchors.
- **DESIGN-REQ-008 - Known workflow fields are modeled, not hidden in JSON blobs** (constraint, 4.3 No anonymous JSON islands; 4.4 No generic type variables): Business, idempotency, billing, routing, and operator-visible fields must be named model fields with concrete element types rather than anonymous parameters/options/payload bags or unresolved generics.
- **DESIGN-REQ-009 - Typed activity signatures and call sites** (requirement, 5 Activities): Activities expose single typed request models, return named typed models, keep stable activity type strings, and workflow code constructs typed requests through typed execution facades.
- **DESIGN-REQ-010 - Provider-shaped data terminates at activity boundary** (integration, 5.4 Provider-specific data stops at the activity boundary): Activities may consume provider-specific payloads internally, but workflow-facing results must be MoonMind canonical contracts such as AgentRunHandle/Status/Result and managed-session response models.
- **DESIGN-REQ-011 - Legacy activity shims are narrow and temporary** (migration, 5.5 Compatibility shims are narrow and temporary): Any temporary legacy dict acceptance occurs only at the public entry boundary, validates immediately into the canonical model, is documented as compatibility behavior, and is removed only after in-flight safety is handled.
- **DESIGN-REQ-012 - Typed workflow run and Continue-As-New contracts** (state-model, 6 Workflow run / Continue-As-New inputs): Workflow run methods and Python workflow initializers use named typed input models; Continue-As-New payloads use the same input model or a dedicated typed continuation model with intentional minimal state.
- **DESIGN-REQ-013 - Typed Signals, Updates, and Queries** (requirement, 7 Signals, Updates, and Queries): Each Signal, Update, and Query uses the right Temporal primitive and named typed request/response or snapshot model; catch-all action envelopes are compatibility shims only.
- **DESIGN-REQ-014 - Mutating Updates validate before history acceptance** (state-model, 7.3 Validators are mandatory for mutating Updates): Public mutating Updates require validators that check typed shape, reject stale epochs, illegal states, and duplicate misuse, and avoid side effects or blocking work.
- **DESIGN-REQ-015 - Managed-session controls use explicit typed operation contracts** (requirement, 8 Managed-session-specific rules): Follow-up, interrupt, steer, clear, cancel, terminate, attach-runtime-handles, and future controls each get their own request models instead of a generic control bag.
- **DESIGN-REQ-016 - Managed-session state protects epochs, idempotency, serialization, and snapshots** (state-model, 8.2-8.5 Managed-session-specific rules): Unsafe stale controls carry and validate sessionEpoch, mutating Updates dedupe by Temporal Update ID or request ID, conflicting mutations are serialized deliberately, and operator-visible status comes from typed workflow state.
- **DESIGN-REQ-017 - Binary and large payloads use intentional wire shapes** (artifact, 9 Binary, large payload, and serialization policy): Raw nested bytes and large histories are not approved; typed base64 fields, top-level bytes contracts, artifact refs, and explicit serializer/validator behavior are required.
- **DESIGN-REQ-018 - Testing covers schemas, Temporal round trips, replay compatibility, and static analysis** (requirement, 11 Testing and tooling requirements): Completion requires schema validation tests, real Temporal boundary round-trip tests, replay/in-flight compatibility coverage when applicable, and static analysis or review gates against raw dict/Any/provider-shaped leaks.
- **DESIGN-REQ-019 - Escape hatches are documented transitional mechanisms** (migration, 12 Approved escape hatches): Temporary Mapping-or-model inputs, legacy message envelopes, and bounded metadata bags require explicit comments and compatibility justification and do not become default architecture.
- **DESIGN-REQ-020 - Anti-patterns are actively removed or blocked** (constraint, 13 Anti-patterns): Review and tooling must reject raw dict execute_activity calls, public raw dict handlers, generic action envelopes, nested raw bytes, provider-specific top-level activity result dicts, unnecessary Any, and large conversational workflow history.
- **DESIGN-REQ-021 - Implementation tracking stays in docs/tmp** (artifact, Header; 14 Canonical implementation anchors): Canonical documentation remains desired state and points to docs/tmp/017-TemporalTypeSafety.md for implementation tracking instead of embedding migration backlog inline.

## Ordered Story Candidates

### STORY-001: Inventory and model Temporal public boundaries

- Short name: `temporal-boundary-models`
- Source reference: `docs/Temporal/TemporalTypeSafety.md`
- Source sections: 1 Purpose; 3.1 Boundary types are real contracts; 3.2 One structured argument per boundary; 4.1 Request/response models; 3.3 Pydantic v2; 4.2 Model configuration; 4.1 Request/response models; 14 Canonical implementation anchors; 4.3 No anonymous JSON islands; 4.4 No generic type variables; Header; 14 Canonical implementation anchors
- Why: This establishes the canonical contract surface and schema homes that all later type-safety enforcement depends on.
- Description: As a MoonMind maintainer, I need every public Temporal boundary inventoried and represented by named Pydantic v2 request/response models so workflow, activity, message, query, and continuation payloads are reviewable serialized contracts instead of ad hoc dictionaries.
- Independent test: Run focused schema tests that instantiate representative boundary models, verify aliases and normalization, reject unknown fields, exercise enum/literal validation, and confirm no unresolved generic or raw dict model fields are introduced for known workflow-control data.
- Dependencies: None
- Needs clarification: None

Acceptance criteria:
- Every inventoried public Temporal boundary has an owning named request model and, where applicable, named response or snapshot model.
- Boundary models use Pydantic v2 and reject unknown fields by default unless a documented escape hatch applies.
- Known business, idempotency, billing, routing, and operator-visible fields are named model fields, not hidden in parameters/options/payload JSON blobs.
- Approved schema homes and implementation anchors are used or a precise domain schema module rationale is documented.
- No activity or workflow type string is renamed as part of the modeling work.

Scope:
- Inventory public workflow run inputs, activity boundaries, Signals, Updates, Queries, and Continue-As-New payloads that are covered by TemporalTypeSafety.md.
- Add or update named Pydantic v2 request/response/snapshot models in the approved schema modules or existing domain schema modules.
- Apply strict model configuration: extra="forbid", stable aliases, explicit identifier normalization, enums/literals for closed sets, and concrete collection element types.
- Document any still-required metadata bags or temporary compatibility inputs as explicit transitional exceptions.

Out of scope:
- Changing Temporal activity or workflow type names.
- Implementing data converter rollout or workflow call-site migration beyond what is needed to compile schema references.
- Creating specs directories or implementation task lists.

Requirements:
- Model workflow, activity, message, query, and continuation payloads as serialized contracts.
- Default to one structured request argument and one structured response model for public Temporal entrypoints.
- Use strict Pydantic v2 configuration and concrete collection element types.
- Keep canonical docs desired-state-only while any implementation tracking remains in docs/tmp.

Owned design coverage:
- DESIGN-REQ-001: Defines the public Temporal boundary inventory as contracts.
- DESIGN-REQ-002: Owns the one-request/one-response model target shape.
- DESIGN-REQ-003: Owns Pydantic v2 model defaults and validation policy.
- DESIGN-REQ-007: Places models in approved schema homes and anchors.
- DESIGN-REQ-008: Prevents anonymous JSON islands and unresolved generics.
- DESIGN-REQ-021: Keeps implementation tracking out of canonical docs.

Handoff: Specify one story that inventories the current Temporal public boundary surface and adds or updates named Pydantic v2 request, response, continuation, and snapshot models in approved schema homes. Preserve activity/workflow names, reject unknown fields by default, and keep implementation-tracking notes under docs/tmp rather than changing canonical desired-state docs.

### STORY-002: Enforce typed Temporal payload conversion and activity calls

- Short name: `typed-activity-calls`
- Source reference: `docs/Temporal/TemporalTypeSafety.md`
- Source sections: 3.4 The data converter is part of the contract; 3.6 Determinism remains the workflow rule; 5 Activities; 5.4 Provider-specific data stops at the activity boundary; 5.5 Compatibility shims are narrow and temporary
- Why: Models alone do not make the Temporal wire contract safe; the data converter and call sites must validate and serialize the same canonical shapes.
- Description: As a MoonMind workflow maintainer, I need Temporal clients, workers, and workflow activity call sites to share typed payload conversion and typed execution helpers so model annotations match the serialized wire shape and provider-specific data cannot leak into workflow histories.
- Independent test: Run Temporal boundary round-trip tests that pass typed activity request models through payload conversion into real workflow/activity execution and assert typed canonical results, including one provider-shaped activity adapter case that returns a MoonMind canonical model.
- Dependencies: STORY-001
- Needs clarification: None

Acceptance criteria:
- Temporal clients and workers use the same Pydantic-aware payload conversion policy for Temporal-facing code.
- Workflow code constructs typed activity request models at call sites rather than inline raw dictionaries.
- New or migrated activities expose single typed request models and named structured return models where applicable.
- Provider-specific response dictionaries are not returned directly to workflows.
- Legacy dict acceptance, where still required, is edge-only, immediately validated into canonical models, and documented as temporary compatibility behavior.

Scope:
- Configure or verify a Pydantic-aware Temporal data converter for Temporal-facing clients and workers.
- Migrate workflow activity call sites to construct typed request models and use typed_execution facades or equivalent overloads.
- Update activity public signatures to accept one typed request model and return named typed models where structured data is returned.
- Normalize any legacy JSON-shaped activity boundary immediately into canonical models at the public edge.
- Ensure provider-shaped payloads are converted to MoonMind canonical workflow-facing contracts before returning from activities.

Out of scope:
- Refactoring managed-session update/signal semantics beyond activity-call dependencies.
- Renaming stable activity type strings.
- Building broad static-analysis gates beyond targeted checks needed for this story.

Requirements:
- Share Temporal payload conversion policy across clients and workers.
- Use typed_execution or equivalent facades so static tooling can see real activity contracts.
- Keep workflow code deterministic while moving validation and provider normalization to activity/service boundaries.
- Treat compatibility shims as narrow and temporary.

Owned design coverage:
- DESIGN-REQ-004: Owns shared Pydantic-aware converter behavior.
- DESIGN-REQ-006: Keeps nondeterministic provider/file/network work in activities.
- DESIGN-REQ-009: Owns typed activity signatures and workflow call sites.
- DESIGN-REQ-010: Owns provider-to-canonical return normalization.
- DESIGN-REQ-011: Owns edge-only legacy activity shims.

Handoff: Specify one story that wires Pydantic-aware Temporal payload conversion through clients/workers and migrates representative activity signatures and workflow call sites to typed request/response models. The story must prove the real serialized path with Temporal round-trip tests and keep provider-shaped data behind activity boundaries.

### STORY-003: Type workflow inputs, messages, continuation state, and managed-session controls

- Short name: `typed-workflow-messages`
- Source reference: `docs/Temporal/TemporalTypeSafety.md`
- Source sections: 6 Workflow run / Continue-As-New inputs; 7 Signals, Updates, and Queries; 7.3 Validators are mandatory for mutating Updates; 8 Managed-session-specific rules; 8.2-8.5 Managed-session-specific rules
- Why: The highest-risk user-facing Temporal surface is managed-session control and status; it needs typed public operations and state safeguards rather than generic action bags.
- Description: As an operator relying on long-running managed sessions, I need workflow run inputs, Continue-As-New state, Signals, Updates, Queries, and managed-session controls to use explicit typed contracts with validators, epoch safety, idempotency, and serialized mutation handling so live workflows remain deterministic and controllable.
- Independent test: Run workflow boundary tests for a managed-session workflow that exercise typed run input, one typed Update with validator rejection for stale epoch, one typed Signal, one typed Query snapshot, Continue-As-New state preservation, and serialized conflicting mutations.
- Dependencies: STORY-001
- Needs clarification: Confirm which legacy catch-all managed-session messages currently require in-flight compatibility before removal or shim retention.

Acceptance criteria:
- Workflow run and initializer signatures use the same typed input model where Python workflow messages require initialized state.
- Continue-As-New carries only intentional typed state, with no opaque continuation JSON bag.
- Each new or migrated public Signal, Update, and Query uses the correct Temporal primitive and named typed request/response or snapshot model.
- Mutating Updates validate typed request shape, stale epochs, illegal states, and duplicates before history acceptance without side effects or blocking work.
- Managed-session controls have one request model per operation and no generic steady-state control bag.
- Session state used for operator-visible status and continuation is typed, bounded, and mutation-safe.

Scope:
- Migrate workflow run methods and Python workflow initializers to named typed input models.
- Model Continue-As-New payloads as the workflow input model or a dedicated minimal typed continuation model.
- Replace public canonical dict-shaped Signals, Updates, and Queries with typed request/response or snapshot models.
- Add validators for mutating Updates that reject stale epochs, illegal states, and duplicate misuse before history acceptance.
- Split managed-session controls into one explicit request model per operation and type session snapshots, continuation state, and operator-visible fields.
- Serialize conflicting mutating handlers using workflow-safe primitives or a main-loop queue where blocking or wait conditions are possible.

Out of scope:
- Activity payload conversion not directly used by workflow message handlers.
- UI changes outside the API/client surfaces required to call explicit Updates or Signals.
- Removing a legacy catch-all message before replay/in-flight safety is established.

Requirements:
- Use typed workflow run and Continue-As-New contracts.
- Use explicit Signal/Update/Query contracts and validators.
- Model managed-session operations individually.
- Protect sessionEpoch, idempotency, serialized mutation handling, and typed snapshots.

Owned design coverage:
- DESIGN-REQ-012: Owns workflow run and Continue-As-New typing.
- DESIGN-REQ-013: Owns typed Signals, Updates, and Queries.
- DESIGN-REQ-014: Owns mutating Update validators.
- DESIGN-REQ-015: Owns one-operation managed-session request models.
- DESIGN-REQ-016: Owns session epoch, idempotency, serialization, and typed snapshots.

Handoff: Specify one story that types workflow inputs, Continue-As-New payloads, Signals, Updates, Queries, and managed-session control messages. Include validator behavior, epoch/idempotency handling, serialized mutation handling, and typed session snapshots, with workflow boundary tests covering real invocation shapes.

### STORY-004: Move binary and large Temporal payloads to explicit serializers or artifacts

- Short name: `temporal-payload-policy`
- Source reference: `docs/Temporal/TemporalTypeSafety.md`
- Source sections: 9 Binary, large payload, and serialization policy; 12 Approved escape hatches
- Why: Temporal reliability depends on replayable, durable histories; large or accidental payload shapes create operational and compatibility risk.
- Description: As a MoonMind operator, I need Temporal histories to carry only intentional compact payloads and artifact references so large diagnostics, transcripts, summaries, checkpoints, binary data, and special JSON fields do not bloat histories or depend on accidental encoder behavior.
- Independent test: Run schema serialization tests for binary/base64 fields and Temporal round-trip tests for artifact-ref payloads, verifying no large body or nested raw bytes are serialized into workflow history for the covered boundaries.
- Dependencies: STORY-001
- Needs clarification: None

Acceptance criteria:
- No covered Temporal JSON/dict-shaped payload embeds nested raw bytes.
- Large text, structured data, diagnostics, transcripts, summaries, checkpoints, and binary outputs are stored outside Temporal history with compact typed refs and metadata.
- Special JSON behavior is implemented through explicit serializers/validators or a project-standard converter.
- Bounded metadata bags remain annotations only and cannot hide workflow-control fields.
- Tests prove the intended wire shape for binary and artifact-reference cases.

Scope:
- Identify Temporal boundary models or activity/workflow payloads that embed raw nested bytes, large text, transcripts, summaries, diagnostics, checkpoints, binary outputs, or large structured data.
- Replace nested raw bytes with explicit base64-serialized typed fields, true top-level bytes contracts, or artifact/claim-check refs as appropriate.
- Move large payload content to artifact storage or external storage and carry compact typed refs plus metadata through Temporal.
- Add explicit serializers/validators for fields that need controlled JSON behavior.
- Ensure metadata escape hatches remain bounded and do not carry workflow-control fields.

Out of scope:
- Redesigning the artifact-storage architecture itself.
- Changing task queue topology or retry policy semantics.
- Migrating unrelated non-Temporal storage formats.

Requirements:
- Use intentional wire shapes for binary data.
- Prefer artifacts or claim-check references over large workflow-history payloads.
- Make serializer behavior explicit instead of relying on generic JSON coercion.

Owned design coverage:
- DESIGN-REQ-017: Owns binary, large-payload, artifact-ref, and serializer policy.
- DESIGN-REQ-019: Owns bounded metadata-bag constraints where used.

Handoff: Specify one story that audits Temporal payloads for raw nested bytes and large history content, moves those bodies to explicit serializers or artifact references, and verifies the resulting wire shapes with schema and Temporal round-trip tests.

### STORY-005: Add Temporal type-safety compatibility, replay, and review gates

- Short name: `temporal-type-gates`
- Source reference: `docs/Temporal/TemporalTypeSafety.md`
- Source sections: 3.5 Compatibility outranks tidiness; 10 Compatibility and evolution rules; 11 Testing and tooling requirements; 12 Approved escape hatches; 13 Anti-patterns
- Why: Without enforcement, the target-state models can drift back to raw dicts, unsafe Any, generic messages, and replay-breaking changes.
- Description: As a reviewer of Temporal changes, I need compatibility rules, replay/in-flight tests, static analysis, and anti-pattern checks to fail unsafe type-safety migrations before they can break running workflows or reintroduce raw dictionary contracts.
- Independent test: Run tests that intentionally exercise disallowed raw dict activity calls, public raw dict handlers, unknown provider-shaped status leakage, and an in-flight/replay compatibility fixture, then verify the checks fail or pass exactly as policy requires.
- Dependencies: STORY-001, STORY-002, STORY-003, STORY-004
- Needs clarification: None

Acceptance criteria:
- Compatibility-sensitive workflow, message, activity, or Continue-As-New changes include replay/in-flight regression coverage or explicit versioned cutover notes.
- Static analysis, lint, or targeted tests catch raw dict activity payloads and public raw dict handlers in covered Temporal modules.
- Review gates reject unnecessary Any leaks, provider-shaped top-level workflow-facing activity results, nested raw bytes, and large conversational state in workflow history where practical.
- Escape hatches are documented as transitional and bounded.
- Additive-first evolution is the default and unsafe non-additive changes require an explicit migration plan.

Scope:
- Define or update review/static-analysis checks for raw dict execute_activity payloads, public raw dict handlers, unnecessary Any leaks, generic action envelopes, provider-shaped top-level activity result dicts, nested raw bytes, and large conversational workflow history.
- Add replay or replay-style compatibility tests for workflow code, message shape, and Continue-As-New changes when in-flight compatibility is a concern.
- Document additive-first evolution rules and explicit cutover/migration expectations for non-additive changes in implementation tracking or relevant plan artifacts.
- Ensure escape hatches have explicit comments and compatibility justifications.
- Run the full required unit suite or targeted unit tests plus any relevant Temporal boundary tests for changed enforcement code.

Out of scope:
- Implementing the boundary model inventory itself.
- Changing Temporal task queue topology or retry policy semantics.
- Using compatibility aliases that change billing-relevant or execution semantics.

Requirements:
- Preserve replay and in-flight safety during contract evolution.
- Test schemas, Temporal boundary round trips, replay/in-flight compatibility, and static analysis coverage.
- Block documented anti-patterns and constrain approved escape hatches.

Owned design coverage:
- DESIGN-REQ-005: Owns additive-first compatibility, replay safety, and migration planning.
- DESIGN-REQ-018: Owns the four-layer testing and tooling gate.
- DESIGN-REQ-019: Owns escape-hatch documentation and transitional status.
- DESIGN-REQ-020: Owns anti-pattern blocking and removal.

Handoff: Specify one story that adds the compatibility, replay, static-analysis, and review gates needed to keep Temporal type-safety migrations safe. The story should include fixtures or tests for known anti-patterns and ensure all temporary escape hatches are justified and bounded.

## Coverage Matrix

- **DESIGN-REQ-001 - Temporal boundaries are public contracts**: STORY-001
- **DESIGN-REQ-002 - One structured argument and return model per boundary**: STORY-001
- **DESIGN-REQ-003 - Pydantic v2 canonical model policy**: STORY-001
- **DESIGN-REQ-004 - Shared Temporal data conversion**: STORY-002
- **DESIGN-REQ-005 - Compatibility and replay safety outrank model tidiness**: STORY-005
- **DESIGN-REQ-006 - Workflow determinism remains mandatory**: STORY-002
- **DESIGN-REQ-007 - Approved schema module ownership**: STORY-001
- **DESIGN-REQ-008 - Known workflow fields are modeled, not hidden in JSON blobs**: STORY-001
- **DESIGN-REQ-009 - Typed activity signatures and call sites**: STORY-002
- **DESIGN-REQ-010 - Provider-shaped data terminates at activity boundary**: STORY-002
- **DESIGN-REQ-011 - Legacy activity shims are narrow and temporary**: STORY-002
- **DESIGN-REQ-012 - Typed workflow run and Continue-As-New contracts**: STORY-003
- **DESIGN-REQ-013 - Typed Signals, Updates, and Queries**: STORY-003
- **DESIGN-REQ-014 - Mutating Updates validate before history acceptance**: STORY-003
- **DESIGN-REQ-015 - Managed-session controls use explicit typed operation contracts**: STORY-003
- **DESIGN-REQ-016 - Managed-session state protects epochs, idempotency, serialization, and snapshots**: STORY-003
- **DESIGN-REQ-017 - Binary and large payloads use intentional wire shapes**: STORY-004
- **DESIGN-REQ-018 - Testing covers schemas, Temporal round trips, replay compatibility, and static analysis**: STORY-005
- **DESIGN-REQ-019 - Escape hatches are documented transitional mechanisms**: STORY-004, STORY-005
- **DESIGN-REQ-020 - Anti-patterns are actively removed or blocked**: STORY-005
- **DESIGN-REQ-021 - Implementation tracking stays in docs/tmp**: STORY-001

## Dependencies

- **STORY-001** depends on: None
- **STORY-002** depends on: STORY-001
- **STORY-003** depends on: STORY-001
- **STORY-004** depends on: STORY-001
- **STORY-005** depends on: STORY-001, STORY-002, STORY-003, STORY-004

## Out-of-Scope Items and Rationale

- Task Queue topology and retry-policy semantics are excluded because the source design explicitly does not define them.
- Artifact-storage architecture is excluded except for Temporal carrying artifact references, because storage design belongs to other canonical documents.
- Activity names, workflow names, and in-flight history compatibility must not be broken for cleaner typing; compatibility changes require explicit safety work.
- Creating `spec.md` files or directories under `specs/` is excluded from breakdown and belongs to downstream specify work.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
