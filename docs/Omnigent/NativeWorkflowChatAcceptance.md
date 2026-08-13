# Native Workflow Chat Acceptance & Rollout Gate

Source issue: MoonLadderStudios/MoonMind#3642 (parent #3632; depends on
#3633–#3641; design source #3628).

This document is the declarative desired state for **proving** the native
Omnigent Workflow Chat journey and **gating** its rollout. The feature itself —
the opaque chat binding (#3633), the binding-scoped HTTP/SSE facade (#3634), the
scoped WebSocket + versioned compatibility map (#3635), the effective capability
policy and durable mutation receipts (#3636), the high-security outbound scan
(#3637), the MoonMind-scoped native UI serving (#3638), the Workflow Detail
native Chat route (#3639), the read-only diagnostics fallback (#3640), and
terminal read-only Chat + linked continuation (#3641) — is implemented by the
dependency issues. This gate is the controlling evidence that the assembled
journey is safe to make primary, and the rollout/rollback control that keeps it
gated until that evidence passes.

Implementation PRs and lower-level bridge/unit tests are necessary but never
sufficient: the gate is a single machine-readable report that only ever reports
`passed` when every required scenario passes in its expected lane with resolved,
current, secret-free, image-pinned evidence.

## Two required lanes

The journey is proven across two lanes and the gate requires both:

| Lane | What it proves | Where it runs |
| --- | --- | --- |
| `deterministic` | The MoonMind binding, native-UI serving, facade authorization/allowlist, immutable capability policy, high-security outbound scan, diagnostic fallback, and rollout gating — driven against a controllable fake Omnigent upstream. | Hermetic, required-CI-safe. `tests/integration/reliability_journey/test_native_chat_acceptance_journey.py` and `frontend/src/browser/workflowNativeChat.browser.test.tsx`. |
| `protected_live` | The stock-image Codex journey (§8): interactive Chat against immutable stock Omnigent server/host image digests with a real enrolled Provider Profile, harvested terminal/mutation evidence, and every evidence ref resolving after live resources are removed. | Credentialed job against immutable stock images; no custom host build, no manual provider session id, no direct upstream browser login, no silent direct-Codex/profile/mode/policy fallback. |

## The machine-readable gate report

`moonmind/omnigent/native_chat_acceptance.py` defines the fail-closed contract
(`build_native_chat_acceptance_report`). The `Provider / Omnigent Native Chat
Acceptance` workflow records the deterministic observations, builds each lane
with `tools/build_native_chat_acceptance_lane.py`, and publishes only the report
produced by `tools/merge_native_chat_acceptance_lanes.py` after both exact lane
inventories validate. The report records:

- MoonMind build/commit and the pinned native-chat contract versions
  (`nativeUiBootstrap`, `nativeUiRouteFeature`, `outboundScan`, `telemetry`);
- immutable Omnigent `server`/`ui`/`host` image digests (`@sha256:…`), host
  architecture, and the route/feature **compatibility manifest digest**;
- safe, opaque Workflow/run/Step/AgentRun/binding identities and
  profile/policy/effective-launch/provider-profile refs (never provider, host,
  or credential material);
- one row per required scenario with its lane and resolved evidence refs;
- cleanup that preserves historical diagnostic reads and releases leases;
- a passing secret scan over all retained evidence; and
- `generatedAt` / `expiresAt` / `supersedes` metadata.

Required scenario rows (each maps to an acceptance criterion):

`deterministic-browser-journey`, `binding-authorization-isolation`,
`credential-browser-isolation`, `capability-policy-immutability`,
`high-security-outbound-scan`, `native-ui-and-transports`,
`diagnostic-fallback`, `terminal-and-continuation`,
`protected-stock-image-journey`, `evidence-durability-and-secret-scan`,
`telemetry-and-rollout`.

Any partial, skipped, failed, mutable-image, stale, revoked, superseded,
wrong-lane, identity-mismatched, or secret-bearing input raises
`ConformanceContractError` — the report is never emitted as `passed`.

The server-side Omnigent HTTP/SSE client does not inherit ambient process proxy
configuration. This keeps the policy-selected upstream endpoint authoritative
and prevents provider credentials or allowlisted transport headers from being
silently redirected. Deployments that intentionally require an egress proxy
must supply it through the explicit client/transport authority boundary.

## Rollout, canary, and rollback

`moonmind/omnigent/native_chat_rollout.py` is the rollout control. The temporary
flag `OMNIGENT_NATIVE_CHAT_ROLLOUT` selects the deployment posture and the native
UI serving router (`api_service/api/routers/omnigent_native_ui.py`) consults it
on every request:

| Mode | Behavior |
| --- | --- |
| `enabled` | Serve interactive native Chat **only** when a current acceptance report resolves and matches the deployed build, images, contract versions, compatibility manifest, complete scenario inventory, and safety attestations. |
| `canary` (default when unset) | Apply the same validated-report requirement. Without it, all HTTP/SSE/WebSocket/resource/terminal/control paths fail closed while durable terminal diagnostics remain readable. |
| `read_only` | Roll back: never serve the interactive native UI; present the durable read-only diagnostics projection. Historical reads are preserved. |
| `disabled` | Interactive native Chat is unavailable. |

`OMNIGENT_NATIVE_CHAT_ACCEPTANCE_REF` is a digest-bound local artifact reference
(`file://…#sha256=…`), not a truthy marker. Dangling, stale, malformed, expired,
wrong-commit, wrong-image, or superseded reports never admit traffic. An
unrecognized value fails closed to `read_only` (never to interactive). A
rollback never silently routes messages through a different runtime or the
deferred `SubmitChatInstruction` / `/chat-instructions` path — it presents
read-only diagnostics or disables interactive Chat.

The rollout flag is **temporary**. `rollout_flag_retirement()` records the
retirement contract: once the deterministic and protected-live acceptance
evidence passes and the read-only fallback window completes, the flag is removed
and the unset/default path retains the same evidence-gated `canary` posture.
Retiring the flag therefore cannot turn a missing or stale report into
interactive authority.

## Readiness / operational telemetry

`moonmind/omnigent/native_chat_telemetry.py` exposes the production emission
adapter. Definitions are registered once in the canonical
`moonmind.observability.metrics.REGISTRY`, exported through the shared StatsD
boundary, and guarded by the canonical `FORBIDDEN_LABELS` identity ban. Signals
are emitted at binding, rollout, native-UI, proxy, scan, mutation, replay, and
continuation authority handoffs, and cover binding resolution, native-UI
compatibility/load/reconnect/fallback, scoped HTTP/SSE/WebSocket outcomes,
authorization/substitution/capability/stale-state denials, security-scan
allow/block/enforcement-unavailable, mutation accepted/completed/rejected/
delivery-unknown, terminal replay, continuation creation, and upstream
latency/transport health.

Every label is a low-cardinality bounded dimension (journey stage, bounded
outcome, bounded rollout mode, readiness). **Workflow, user, binding, session,
and credential identity are never metric labels**; unknown label values
normalize to `other`.

## Non-goals

- The deferred `SubmitChatInstruction` / Steer Workflow feature.
- Claude-through-Omnigent parity.
- Replacing the lower-level unit, API, bridge, and conformance suites with one
  browser run — both layers are required.
- Automatically enabling unrestricted terminal, browser, or workspace mutation
  capabilities.
- Treating direct upstream Omnigent access as an acceptable development shortcut
  in the acceptance journey.
