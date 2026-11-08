# Application Access Assessment

`moonmind.security.application_access_target` checks explicit expected rights against bounded HTTP, WebSocket and SignalR JSON requests. It complements ZAP route/identity observations: a successful request or a scanner alert alone does not establish whether a selected identity may read or change a particular object. Application identities are target test accounts, not MoonMind operator roles.

## Reviewed expectations

An `application-access-spec/v1` input names `sourceRef`, `policyRef`, and `policyReviewRef`. These identify the application specification, expected-access policy, and its review. They are evidence references, not grants. Code-aware suggestions should cite that policy and await review; observed behavior never defines expected rights.

The shared `authorizationMatrix` contains identities and cases. An identity names `id`, `accountRef`, `sessionRef`, `integrationRef`, and an opaque `credentialRef`. An anonymous identity instead declares `anonymous: true` and has no credential. Each matrix case names `id`, `identityRef`, the target account/session/integration, `resourceRef`, `action`, and expected `allow` or `deny`. A `resources` registry binds each resource ID to its owner identity and account/session/integration context. Add owner, non-owner, anonymous, and elevated identities only where the reviewed test requires them.

Each ordered `cases` entry binds a matrix `caseRef`, `resourceRef`, `policyRuleRef`, and:

- `request`: `id`, relative `path`, `method`, optional `credentialRef`, and JSON `payload`. HTTP is the default. Real-time requests additionally name an exact admitted operation as described below.
- `identityCheck`: a GET using the same selected credential, plus nonempty `identityAssertions` that establish the actual test identity before and after the operation.
- Nonempty `allowAssertions` and `denyAssertions`, plus optional `protectedAssertions` that detect protected data in a denied response.
- Optional read-only `stateCheck` and `stateAssertions`. Mutation cases require both. A separately admitted owner credential can inspect the object's state without granting the requesting identity that right.

JSON assertions have `id`, bounded dotted `path`, and `operator: equals` with a scalar `value`, or `operator: exists`. State assertions use `unchanged`, `changed`, or `equals`. Types are significant: boolean `true` is not integer `1`. A missing state value is unknown, never evidence of no change. Canonical zero-based array indices are supported, for example `arguments.0.requestId`; negative or padded indices and executable expressions are unsupported. Use synthetic object identifiers and test data, not secrets, as expected values.

For example, a denied object read can require `error == "forbidden"`, define successful access as `record.id == "test-record"`, and detect leakage with `record.privateValue` existing. A 200 response with a login/error page does not pass the access assertion. A denial containing protected data or changing protected state is a finding, provided identity and evidence are established.

## Mutation and recovery contract

Methods other than GET, HEAD, and OPTIONS require an explicit active assessment, admitted method/action, and test data. Each mutation declares a unique stable `mutationRef`, a `mutationRefPath` whose JSON request value equals that reference, and a `receiptAssertion` that checks the same reference in the post-state response. Denied mutations assert unchanged protected state. Accepted or uncertain effects require a read-only receipt. A directly observed denial with no protected data and complete unchanged-state evidence needs no receipt side effect. Missing evidence remains inconclusive rather than being guessed safe.

The observer durably records an uncertain attempt before reserving and sending the primary request. It saves sanitized projections after the response and reconciles state/receipt without replaying an attempted mutation. A lost response remains inconclusive even if a receipt confirms its effect, because its response predicates were not observed. An existing receipt without the prior capture also blocks a new send. Use a new reviewed case/reference for a deliberately new operation, not as an automatic retry.

Atomic capture files preserve completed cases, attempt identities, state digests, spent requests, reserved rate slots, and the original wall-clock deadline. A process lock prevents concurrent writers. `--resume` takes an immutable retained capture and must preserve the exact specification, scope, and time limit. Canceled, expired, or exhausted captures cannot start successor work. Recovery never resets the budget. Keep the latest capture as a job artifact before removing the original host's storage.

## Deployment profile and invocation

Use an ordinary deployment-controlled workload profile with an image source containing MoonMind and entrypoint `["python","-m","moonmind.security.application_access_target"]`. Its mode uses a declared action class, for example `application-access`, and these argv entries:

```json
["--spec={spec}","--resume={resume}","--max-seconds={maxSeconds}",
 "--capture=/outputs/access-capture.json","--output=/outputs/access-assessment.json"]
```

Declare `spec` as required `input`, `resume` as optional `input`, and `maxSeconds` as an integer bounded by the engagement and profile time limits. Inputs are captured attachments, never arbitrary paths. Declare exactly these assessment outputs:

```json
[{"name":"capture","relativePath":"access-capture.json"},
 {"name":"assessment","relativePath":"access-assessment.json"}]
```

The profile fixes `MOONMIND_TARGET_ASSESSMENT_SCOPE` as a JSON string in `environment`. It contains the existing `TargetScope`: `web` origin, routes/methods, exclusions, start paths, mode, rate, timeout and egress policy; `credentialSlots` mapping opaque references to file-delivered `application_auth` slots; and a bounded `maxRequests`. Use `networkMode: bridge` with the same `egressPolicyRef`. The existing reviewed engagement must admit the profile/action, web target, resource/time limits, network and credentials. Source-control or model credentials confer no application access.

Submit the profile selection with the existing `moonmind container run --spec job.json` or `container.run_job` MCP tool. The existing admission and worker verify the rendered profile; neither accepts caller-supplied environment or target expansion. All primary, identity, and state requests are checked before the first credential read or network request. The client requires the worker's matching egress proxy, refuses redirects, isolates cookie/session state, and counts verification requests against the same rate, request, and time budgets. HTTP responses are bounded to 256 KiB; each input/capture is bounded to 8 MiB. The case contract supports at most 512 cases and eight admitted credential slots. Interactive login flows are unsupported. Real-time support is explicitly bounded below.

## Bounded WebSocket and SignalR operations

Add the same optional `realtime` authority to the reviewed web target and the deployment profile's `TargetScope`. It contains `cases`, `maxConnections`, `maxMessages`, `maxBytes`, `maxMessageBytes`, and `maxReceiveMessages`. The profile may select fewer exact named cases or lower caps, never widen them. Saved authority changes, including JSON scalar type changes, invalidate the old reviewed generation. The review discloses each endpoint, identity, effect, exact payload and cap. The Settings document's existing size limit still applies; prefer small synthetic inputs.

A named operation contains `caseRef`, `protocol` (`websocket` or `signalr-json`), relative `path`, `messageType`, exact JSON `payload`, `effect` (`read_only` or `mutation`), and either `credentialRef` or explicit `anonymous: true`. A WebSocket payload must contain `type` equal to `messageType`; channel/object identifiers belong in that exact payload. SignalR additionally declares `negotiationPath`, admits POST there, and uses `payload: {"arguments": [...]}` with `messageType` as its hub target. The handshake endpoint must admit GET. Neither a successful upgrade nor a server-discovered channel grants authority.

For example, an anonymous read-only subscription can be declared as:

```json
{"caseRef":"read-record","protocol":"websocket","path":"/events",
 "anonymous":true,"messageType":"subscribe","effect":"read_only",
 "payload":{"type":"subscribe","channel":"test-record"}}
```

The corresponding application `request` uses `realtimeCaseRef: "read-record"`, the same protocol/path/identity/message type/payload/effect, and `method: "GET"` for the upgrade. Mutation classification uses the explicit effect, not that GET method. A SignalR mutation's stable reference may live at `arguments.0.requestId`; it still requires the same state and receipt proof as HTTP. Identity and state verification remain HTTP GETs before/after the operation. Connectivity is never identity proof.

Each operation opens one connection, sends one exact application message, observes one bounded response/completion, then closes. SignalR supports negotiate version 1, WebSockets/Text, JSON handshake version 1 and correlated type-3 completion. A completion's error string is used only for explicit predicates, not assumed to be denial. Binary frames, MessagePack, streams, server invocations, automatic reconnect, fallback transports, negotiation redirects/access tokens, coalesced extra instructions and unsupported result shapes stop with partial unsupported coverage. All upgrade redirects are refused before the destination is contacted. Use a new reviewed case for further work, never a discovered instruction.

Connections count attempted WebSocket upgrades. Message/byte caps count protocol bodies, including SignalR negotiation and handshake, excluding HTTP headers and wire framing. `maxReceiveMessages` bounds frames/records per operation; `maxMessageBytes` bounds a body/record, and `maxRequests` counts protocol turns and verification probes. Reservations conservatively cover the maximum possible receive bodies before a send; an operation may be refused even if a hypothetical small response would fit. After a completed exchange actual counters replace that reservation. An interrupted exchange retains the reservation. Cancellation closes the socket without waiting for a peer-controlled close handshake; restart reconciles attempted effects without reopening their socket or renewing any counter or deadline.

The capture retains typed, bounded transcript events containing request/case/correlation IDs, fixed stage/direction categories and body lengths. No frame body, negotiation token, cookie, credential, or free-form exception is retained. HTTP-only and legacy RGS evidence keep their previous canonical digests. The RGS observer keeps its existing domain-specific message path; it refuses this new named scope until it can preserve these transport counters, so use the application-access observer for these cases.

## Evidence and comparison

The `application-access-capture/v1` artifact contains the admitted scope, ordered progress, expected-spec digest, identity/response/state predicate results, request evidence hashes, and non-replayable attempt/receipt references. Raw responses, cookies, credential values and protected object values are not retained. State comparisons retain digests rather than values; treat those and the original specification as sensitive assessment evidence.

The collector retains the capture before the report and publishes the exact materialized specification JSON, including when the input came from an archive or workspace snapshot. It verifies scope, input digests and recomputed conclusions, then binds the actual ContainerJob terminal outcome and exact `spec`/`capture` artifact references. Failed, canceled and timed-out jobs remain partial. Confirmed exposure or state changes survive missing secondary reconciliation evidence as findings with partial coverage. Missing, invalid or mismatched files are reported as collection gaps, never successful coverage.

Pass the report artifact to `security.assess` with `tools: ["retained-report"]`, and the captured origin in `scope.assets`. The consumer authorizes and reads both dependencies, verifies their byte digests, re-evaluates the sanitized evidence, and retains them with the report. Results remain readable after job cleanup. Each case exposes expected/observed access, authentication state, outcome, reason codes and stable object/identity context. Wrong or expired identities, inaccessible state, malformed responses and exhausted budgets remain inconclusive.

Comparability includes the complete reviewed specification and captured target scope. Removing a case, identity, object, assertion or route cannot mark an old finding fixed. Consistency means only that these declared cases agreed with this reviewed policy; undeclared objects and unobserved rights remain unassessed. No result claims verified remediation automatically.
