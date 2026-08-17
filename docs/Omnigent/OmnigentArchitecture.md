# Omnigent Control-Plane Architecture

Status: Proposed design
Document Class: System / Architecture Description View
Owners: MoonMind Platform
Last updated: 2026-08-17

**Implementation tracking:** rollout notes, extraction backlogs, and temporary
handoffs belong under `docs/tmp/` or gitignored local-only artifacts, not in this
canonical design document.

## One-sentence summary

The Omnigent control plane is decomposed into six layers — **domain**,
**application**, **ports**, **adapters**, **ui_facade**, and **evidence** — whose
dependencies point inward toward pure policy, so a reliability change touches one
owner and is testable without persistence, transport, or provider infrastructure.

## Component and authority overview

| Layer | Package | Owns | May depend on |
|-------|---------|------|---------------|
| Domain | `moonmind/omnigent/domain/` | Status/turn vocabulary, provider-status compatibility, failure classification (§17), identifiers, lifecycle transitions, commands, decisions | stdlib + pure schema types only |
| Application | `moonmind/omnigent/application/` | Use cases: reconcile session, manage turn/host/cleanup/recovery/remediation | domain, ports |
| Ports | `moonmind/omnigent/ports/` | Narrow protocols for repositories and side effects | domain |
| Adapters | `moonmind/omnigent/adapters/` | Concrete persistence, provider HTTP/stream, host, workspace, artifact, publication implementations | domain, ports, infrastructure |
| UI facade | `moonmind/omnigent/ui_facade/` | Route classification, caller authorization, capability resolution, virtual-id binding, transport/forwarding policy | domain, ports, application |
| Evidence | `moonmind/omnigent/evidence/` | Event journal, harvesting, diagnostics, acceptance schemas | domain, ports |

**Canonical aggregate owner.** The bridge *session* is the canonical aggregate.
Its coalesced status and terminal-safe transitions are defined once, in
`domain/session_state.py` and `domain/transitions.py`. Persistence of that
aggregate is the `SessionRepository` port (`ports/sessions.py`); every adapter
implementing it must produce identical revision/fencing outcomes.

## Allowed dependency directions

Dependencies point inward only:

```text
adapters ─┐            ┌─ ui_facade
          ├─> ports ──>│
evidence ─┘            └─> application ──> ports ──> domain
                                   application ──> domain
```

- **Domain** imports nothing from the other layers and no infrastructure. It is
  the single source of truth for status, failure, and observation vocabulary;
  duplicate definitions elsewhere are a boundary violation.
- **Application** coordinates use cases over ports and domain policy. It never
  imports SQLAlchemy, FastAPI, Docker, Temporal, or a provider client.
- **Ports** are narrow protocols (avoid one all-purpose store/client). They
  depend only on domain types.
- **Adapters** are the only layer that may import infrastructure. Provider-native
  vocabulary is translated into canonical domain observations here and never
  leaks upward.
- **UI facade** owns browser binding, authorization, capability resolution, and
  transport policy. It must **not** own canonical session lifecycle transitions.
- **Evidence** records and projects durable evidence. It must **not** decide
  lifecycle authority.

These rules are enforced deterministically by
`tools/check_omnigent_architecture.py` (rules: `forbidden-layer-import`,
`forbidden-infra-import`, `layer-cycle`, `fastapi-outside-facade`,
`env-read-outside-adapter`, `duplicate-vocabulary`) and pinned by
`tests/unit/omnigent/test_omnigent_architecture_checker.py`.

## Boundaries

- **Provider boundary.** Provider HTTP/stream vocabulary lives in
  `adapters/provider_http` and `adapters/provider_stream`. Native event types are
  mapped to canonical normalized statuses through `domain/observations.py`.
- **Temporal boundary.** The Temporal SDK is confined to `adapters/temporal`.
  Workflow-facing payloads stay compact and replay-safe; domain/application code
  is deterministic and side-effect-free.
- **Browser facade boundary.** FastAPI routers in `api_service` authenticate,
  deserialize, call one `ui_facade`/application operation, map typed outcomes to
  HTTP/WebSocket, and serialize a bounded response. Route classification is owned
  by `ui_facade/route_contract.py` so the single public route contract can be
  served by per-facade routers.
- **Evidence boundary.** Durable evidence bodies are artifacts referenced through
  the `ArtifactStore` port; the event journal keeps only a bounded index.

## Thin router requirement

Route handlers must not own database queries, provider calls, policy
intersections, lifecycle transitions, or artifact composition. Those move behind
an application use case or a `ui_facade` operation.

## How to extend without bypassing the architecture

- **Add a provider route:** classify it in `ui_facade/route_contract.py`; add a
  forwarding decision in `ui_facade/http_proxy.py`/`websocket_proxy.py`; the
  router calls the facade. Do not add provider I/O to the router.
- **Add a lifecycle state:** extend `domain/session_state.py` and
  `domain/transitions.py`; add a normalized-status mapping in
  `domain/observations.py` if a provider event produces it. Nothing outside the
  domain enumerates the vocabulary.
- **Add an observation:** map its event type to a canonical status in
  `domain/observations.py`; adapters translate the raw payload.
- **Add a command:** add a dataclass in `domain/commands.py`; the emitting use
  case returns it; an adapter executes it.
- **Add a host mode:** implement the `HostLauncher`/`LeaseManager` ports in a new
  `adapters/<mode>_host` package. Application code depends on the port, not the
  mode.

## Incremental migration status

This is an incremental, behavior-characterized decomposition (issue
MoonLadderStudios/MoonMind#3711). The layered packages and their enforcement are
established now; the large legacy modules (`bridge_store`, `execute`,
`profile_bound_execution`, `oauth_host_runtime`, `remediation_matrix`, and the
`omnigent_bridge` router) remain as facades/adapters and delegate to the domain
single source of truth where extracted. Replay-safe legacy code required by
existing Temporal histories is retained until its migration/retirement window.
Later phases move repositories, provider/host/workspace/artifact side effects,
and router bodies behind these seams without changing the contracts documented
here.
