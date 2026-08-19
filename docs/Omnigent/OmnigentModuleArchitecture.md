# Omnigent module architecture and boundaries

**Document Class:** Canonical declarative
**Status:** Accepted
**Owners:** MoonMind Platform
**Authority:** Owns the layer map, allowed dependency directions, canonical
aggregate owners, and extension points for the `moonmind.omnigent` package. It
defines *where* each responsibility lives so a reliability change can be made and
tested inside one boundary. It does not restate the lifecycle transition
contract (see `OmnigentLifecycleReconciler.md`), the durable aggregate schema
(see `ControlPlaneAggregates.md`), or the bridge transport contract (see
`OmnigentBridge.md`).
**Traceability:** MoonLadderStudios/MoonMind#3711 ([Omnigent control plane
10/11]); parent #3701; depends on #3702, #3703, #3704.

## 1. One-sentence summary

Omnigent is organized as a hexagonal architecture: a pure **domain** core
(state, transitions, failure and status vocabulary) sits behind narrow
**ports**, which **adapters** implement for real infrastructure, which thin
**application** use cases and **UI-facade** routers orchestrate, with **evidence**
holding durable artifact-backed proof — and dependencies only ever point inward.

## 2. Component and authority overview

| Layer | Package (canonical / in progress) | Owns | Must not own |
| --- | --- | --- | --- |
| Domain | `omnigent/domain/`, `omnigent/reconciler/` | Pure identifiers, session/turn state, observations, decisions, transitions, failure and status classification | Any I/O, framework, ORM, settings, or environment access |
| Ports | `omnigent/ports/` | Narrow protocol per aggregate and per side-effect boundary | Concrete SQLAlchemy / HTTP / Docker / FastAPI |
| Application | `omnigent/application/` *(target)* | Use-case coordination over ports (reconcile session, manage turn/host/cleanup/recovery/remediation) | Concrete infrastructure or transport |
| Adapters | `omnigent/adapters/`, `omnigent/control_plane/repositories.py` | Concrete port implementations: persistence (PostgreSQL, in-memory), provider HTTP/stream, Temporal, Docker/Compose host, workspace, artifacts, publication | Canonical lifecycle policy |
| UI facade | `omnigent/ui_facade/` *(target)*, `api_service/api/routers/omnigent_bridge*.py` | Browser binding, caller authorization, capability resolution, virtual IDs, route classification, transport policy, provider forwarding | Canonical session lifecycle transitions |
| Evidence | `omnigent/evidence/` *(target)*, `omnigent/bridge_artifacts.py` | Durable artifact-backed evidence, bounded indexes, diagnostics, acceptance schemas | Lifecycle authority |

Realized today: the pure lifecycle reducer and transition contract
(`reconciler/`, #3702), the durable aggregates and repositories
(`control_plane/`, #3703/#3704), the pure failure vocabulary
(`domain/failures.py`), the narrow repository ports (`ports/`), and the
in-memory persistence adapters (`adapters/persistence/`). The remaining
application, UI-facade, and evidence layers are being extracted incrementally;
the large legacy bridge modules continue to operate as facades/adapters until
their behavior is moved behind these boundaries. No layer is a rewrite gate — the
decomposition preserves normal-create, retry, terminal, cleanup, chat, and
historical-read behavior at each step.

## 3. Allowed dependency directions

```text
adapters ─▶ application ─▶ ports ─▶ domain
   │                         ▲          ▲
   └─────────────────────────┴──────────┘
```

- **Domain** depends only on the Python standard library and small pure schema
  utilities. It must never import FastAPI/Starlette, SQLAlchemy, the Temporal
  SDK, HTTP clients, Docker or subprocess launchers, artifact services,
  OpenTelemetry exporters, application settings, or environment variables.
- **Ports** depend only on domain types and the canonical record dataclasses.
  They declare narrow protocols — one aggregate per port — not one all-purpose
  store interface.
- **Application** depends on domain and ports only. It coordinates use cases and
  produces commands/results; it never imports a concrete adapter.
- **Adapters** implement ports for concrete infrastructure. Provider-native and
  infrastructure-native vocabulary stays here and is translated into canonical
  domain observations and outcomes.

These directions are enforced by `tools/check_omnigent_architecture.py`
(forbidden infrastructure/framework/settings imports and environment reads in the
infra-free `domain`/`ports`/`application` layers, no back-edges/cycles across
layers, web-framework containment to the API/`ui_facade` boundary, direct
SQLAlchemy confined to `adapters/persistence/`, provider-native vendor vocabulary
kept out of the infra-free layers — no vendor/runtime name such as `codex`,
`claude`, `jules`, `gemini`, `anthropic`, or `openai` in an import target or a
non-docstring string literal, so the pure layers speak only canonical vocabulary
and providers are translated at the adapter boundary — and single canonical
vocabulary for the conflict/failure/fencing tables (`OmnigentFailureReason`,
`ControlPlaneOutcome`, `FencingScope`) and for the status/capability vocabulary
(`ProviderStatusClass`, `SessionLifecyclePhase`, `TerminalOutcome`, `LeaseState`,
`SubmissionState`, `DesiredLifecycle`, `DecisionKind`, `ReasonCode`), so
provider-status normalization and transition/decision tables are never duplicated
across the large modules) and covered by
`tests/unit/omnigent/test_architecture_boundaries.py`.

## 4. Canonical aggregate owners

Each durable aggregate has exactly one repository port and one production
adapter; no aggregate is mutated through another aggregate's interface.

| Aggregate | Port | Production adapter |
| --- | --- | --- |
| Canonical provider session | `ports.SessionRepositoryPort` | `control_plane.repositories.SessionRepository` (+ `adapters.persistence.InMemorySessionRepository`) |
| Turn attempt | `ports.TurnRepositoryPort` | `control_plane.repositories.TurnAttemptRepository` (+ `adapters.persistence.InMemoryTurnAttemptRepository`) |
| Observation index | `ports.ObservationRepositoryPort` | `control_plane.repositories.ObservationRepository` (+ `adapters.persistence.InMemoryObservationRepository`) |
| Command / idempotency journal | `ports.CommandRepositoryPort` | `control_plane.repositories.CommandRepository` (+ `adapters.persistence.InMemoryCommandRepository`) |
| Reconciliation decision journal | `ports.DecisionRepositoryPort` | `control_plane.repositories.DecisionRepository` (+ `adapters.persistence.InMemoryDecisionRepository`) |

Every adapter implementing a port passes the same shared behavioural contract in
`tests/helpers/omnigent_port_contracts.py`, run against the in-memory reference
adapters, the SQLAlchemy repositories on SQLite, and the PostgreSQL adapters, so
in-memory test doubles and the PostgreSQL adapter are proven interchangeable
behind one interface for all five aggregates. Cooperating in-memory adapters that
share a session/turn/command backing state are exposed through
`adapters.persistence.InMemoryControlPlaneStore`, mirroring
`control_plane.OmnigentControlPlaneStore`.

## 5. Boundaries

- **Provider boundary.** Provider HTTP and stream vocabulary lives in provider
  adapters and the domain compatibility/classification modules. The reducer and
  the rest of the domain consume canonical `ProviderStatusClass` /
  `OmnigentFailureReason` values, never raw provider status or route strings.
- **Temporal boundary.** Workflow code carries immutable refs and compact
  metadata; source loading, resolution, and materialization happen in
  Activities/adapters. Payload shapes crossing the worker boundary are
  compatibility-sensitive.
- **Browser facade boundary.** The UI facade owns caller authorization,
  capability resolution, virtual IDs, route classification, and provider
  forwarding. It maps typed application outcomes to HTTP/WebSocket responses and
  never performs canonical lifecycle transitions itself.
- **Evidence boundary.** Evidence owns durable artifact-backed proof, bounded
  indexes, diagnostics, and acceptance schemas. Evidence records outcomes; it
  does not decide lifecycle authority.

## 6. Extension points

Add capability at the boundary, not by widening a legacy module:

- **A provider route:** add or extend a UI-facade route contract and forward
  through the provider adapter; do not add lifecycle mutation to the router.
- **A lifecycle state or transition:** extend the domain transition vocabulary in
  `reconciler/` (closed decision/reason codes) and let the reducer drive it; do
  not add a state branch inside an adapter or router.
- **An observation:** add its canonical type to the domain observation
  vocabulary and append it through `ObservationRepositoryPort`; adapters
  translate provider-native events into it.
- **A command (logical side effect):** record it through `CommandRepositoryPort`
  with an idempotency key and claim/deliver through the port so it executes
  exactly once.
- **A host mode:** implement the host port with a new adapter (Docker, Compose,
  or a future runtime); keep host-native vocabulary inside the adapter and expose
  canonical host observations.

A change that cannot be expressed through an existing port or domain vocabulary
is a signal to add a narrow port or a domain type — not to reach across a
boundary from a router, adapter, or legacy module.
