# MoonMind Roadmap — Execution Handoff (disposable)

> **Status:** imperative working handoff (disposable execution scaffolding; not canonical).
> Durable desired state lives in [`docs/MoonMindRoadmap.md`](../MoonMindRoadmap.md). When this handoff and a canonical design disagree, the declarative design wins.
> Owner: MoonMind Engineering. Previous full tracker dated 2026-07-29 (285 lines) condensed here per MoonLadderStudios/MoonMind#3965; full history in git.
> Related consolidation: MoonLadderStudios/MoonMind#3961, #3962, #3963, #3964, #3970. Do not recreate MoonLadderStudios/MoonMind#3971's canceled demo project or another duplicate epic.

## Open execution items (unresolved; status lives in issues, not merges)

| Priority | Issue | Scope and current evidence |
| --- | --- | --- |
| P0 | MoonLadderStudios/MoonMind#3507 | Authoritative normal-workflow workspace materialization; no merged completion PR |
| P0 gate | MoonLadderStudios/MoonMind#3508 | Credentialed browser-to-stock-host matrix; admission hardened in #3541, live matrix still absent |
| P0 | MoonLadderStudios/MoonMind#3510 | Evidence-gated resume and Checkpoint Branch flow; v2 manifest landed in #3509/#3554, default orchestration open; claims 5.1, 5.4, 5.5 |
| P0 gate | MoonLadderStudios/MoonMind#3512 | Remediation UI, verification, audit, controlled rollout; substrate #3511/#3544, authority gaps open; claim 6.2 |
| P1 | MoonLadderStudios/MoonMind#3514 | Production in-session retrieval and authoring; capability substrate #3514/#3552, host delivery open |
| P1 security | MoonLadderStudios/MoonMind#3516 | Restricted-egress enforcement; candidate PR #3555 open, live Docker allow/deny and negative conformance required |
| P1 | MoonLadderStudios/MoonMind#3517 | Agent-profile product journeys and selectors; model/API landed in #3547, CRUD/sync/smoke open |
| P2 | MoonLadderStudios/MoonMind#3518 | Protected evidence, staged Codex cutover, compatibility, retirement; phase machine #3548, deployed phase `opt_in` |

## Closed-but-incomplete residuals (need reopened or follow-up owner before claims close)

- MoonLadderStudios/MoonMind#3509 (PR #3554): checkpoint slice complete; acceptance evidence and production resume stay with #3510.
- MoonLadderStudios/MoonMind#3511 (PR #3544): remediation substrate; `cleanup.request_janitor` target-authorization and helper-container linkage gaps need successor ownership.
- MoonLadderStudios/MoonMind#3513 (PR #3545): initial-context implementation; durable controlling verification for claim 7.1 still blocked.
- MoonLadderStudios/MoonMind#3515 (PR #3546): policy foundation; cross-boundary consumption, approvals, and ownership need follow-up.
- MoonLadderStudios/MoonMind#3519 (PR #3549): embedded foundation only; graduation evidence absent, mode stays experimental.
- MoonLadderStudios/MoonMind#3520 (PR #3550): early Claude foundation; parity deferred until Codex cutover stabilizes.

## Unique safety and qualification obligations (must not be lost)

- Checkpointless headless remediation (PR #3556 patch `run-workflow-headless-remediation-v1`) completes a full cycle without canonical checkpoint authority: open security gap tracked in #3510/#3512; enforce the head guard or explicitly bound the path.
- Protected browser-originated support matrix (#3508, #3448, support rows, cutover promotion) requires independently resolvable digest-checked artifacts.
- Restricted-egress live proof (#3516): Docker allow/deny, DNS, redirect, IPv6, direct-IP, stale-attestation, gateway-health, static/on-demand, cleanup, and negative conformance.
- Unknown status remains unresolved; merge counts alone never mark an item complete.

## Disposal condition

Delete this file only after every row above has a durable issue owner with current source/PR/evidence references and all links in [`docs/MoonMindRoadmap.md`](../MoonMindRoadmap.md) are updated. Otherwise keep this handoff as the sole cross-cutting execution tracker.
