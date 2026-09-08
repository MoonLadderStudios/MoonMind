# Omnigent-Backed Authentication Design

**Status:** Proposed  
**Document Class:** Canonical declarative  
**Viewpoint:** System / Feature Design View  
**Implementation posture:** Deferred until explicitly adopted and the authentication boundary is qualified  
**Owners:** MoonMind Platform and Security  
**Updated:** 2026-09-07  
**Audience:** API, dashboard, deployment, and Omnigent integration contributors  
**Owning Surface:** Human authentication across `api_service/auth_providers.py`, API composition, browser transports, and deployment configuration  
**Related Docs:** [Workflow Chat Panel](../UI/WorkflowChatPanel.md), [Omnigent Bridge](../Omnigent/OmnigentBridge.md), [Provider Profiles](../Security/ProviderProfiles.md), [Secrets System](../Security/SecretsSystem.md), [Primary Runtime Provider Strategy](../Omnigent/PrimaryRuntimeProviderStrategy.md)  
**Authority:** Candidate design only. Current providing contracts remain authoritative until an explicit adoption change reconciles them.

> Adding this document does not remove Keycloak, enable Omnigent authentication for MoonMind, migrate users, change defaults, or qualify a deployment. Implementation sequencing and live progress belong in existing issues or `docs/tmp/`, not in this proposal.

## Advance organizer

**One sentence:** Omnigent Server authenticates people, while MoonMind retains stable application user IDs and decides what those people may access or control.

**One paragraph:** Replace bundled Keycloak and redundant MoonMind password-login behavior with the account and login functionality of the already-running Omnigent Server. Integrate through a narrow authentication adapter, preferably using a supported server-side validation contract behind the MoonMind browser origin. Preserve MoonMind's authorization, workflow ownership, native-chat facade, durable evidence, and service credentials. This is a moderate integration rather than an orchestration rewrite, but credential separation, account identity, browser transports, and session lifecycle are release requirements, not follow-up hardening.

## DOC-REQ-201 Proposed decision and scope

The intended normal authenticated deployment has one human account authority: Omnigent Server. Built-in accounts provide the self-hosted login path. Omnigent's external OIDC integration remains an optional, separately qualified path for deployments with an identity provider. Removing bundled Keycloak does not prohibit an operator-managed external Keycloak deployment behind that OIDC path.

The ordinary user signs in to MoonMind without selecting an authentication backend or completing a second login for Workflow Chat. Technical provider details remain visible in authorized deployment diagnostics. Authentication mode is deployment configuration, not a workflow, Harness, Agent Profile, or Provider Profile setting.

The proposal does not make Omnigent a drop-in enterprise identity platform or assume parity with every Keycloak feature. It does not move model-provider OAuth enrollment, GitHub credentials, orchestration, authorization, or workflow data into Omnigent's accounts system. It does not require another always-on authentication container or a fork of Omnigent Server.

Explicit local no-auth operation may remain an advanced deployment posture. It is never a fallback for missing configuration, failed authentication, or an unavailable identity service. Changing the existing default posture requires its own explicit adoption decision and secure first-run behavior.

## REVIEW-201 Source baseline and limits

Source review baseline: MoonMind `8ff5144bd03c8f006621eabc982e12ebac944a30` and its pinned Omnigent submodule `f04b0354fb5344c1ea8b92795ceb6760a9ad7595`. These are source observations, not executed authentication tests or evidence about a deployed image. Implementation must recheck the then-pinned source and actual server artifact.

| Verified observation | Design consequence |
| --- | --- |
| MoonMind's [current-user dependencies][mm-auth-providers] send every non-`disabled` provider to FastAPI Users. | A new configuration value alone does not integrate Omnigent. Both strict and optional dependencies need explicit provider handling. |
| [API composition][mm-main] includes FastAPI Users auth routers when the provider is not `keycloak`. | The new mode must not accidentally retain old login or registration routes through a negative condition. |
| The [user model][mm-user] has a UUID primary key, nullable password storage, and a unique `oidc_provider` / `oidc_subject` pair. | External-identity mapping can preserve application UUIDs. Existing fields are a starting point, not proof that every upstream identity fits safely. |
| The [terminal WebSocket path][mm-websocket] reads a MoonMind JWT through `get_jwt_strategy()` and a query parameter. | Updating the HTTP dependency alone misses a live authentication consumer. |
| Omnigent's [auth provider][og-auth] supports accounts, OIDC, and trusted-header modes. Accounts and OIDC share cookie/bearer validation. | Reuse the verified upstream boundary, not a duplicate password verifier. Each supported deployment mode still needs its own qualification. |
| The [accounts router][og-accounts] implements login, logout, current-user reads, invite-based registration, setup, and account-management behavior. `/auth/me` also checks that the account exists. | There is a concrete server-side integration candidate for accounts mode. Do not infer an identical validation surface for other modes. |
| Omnigent's [runner-token minting][og-auth] uses the same session-token format accepted by the user verifier. The verifier also handles grant-derived tokens with upstream-specific restrictions. | A valid signature or successful identity lookup is not sufficient evidence of a human credential suitable for MoonMind. |
| The [accounts login response][og-accounts] includes a token and can issue refresh material when requested. Logout clears the cookie. | Browser response filtering, accepted request fields, and revocation semantics require explicit treatment. Cookie deletion is not global token revocation. |
| [Compose][mm-compose] places Keycloak behind an optional profile and already configures an Omnigent Server authentication surface. | Removal simplifies the supported deployment surface, but must not be advertised as reducing the default idle-container count when that profile is already off. |

## CONTRACT-201 One authentication authority, retained application authorization

| Responsibility | Proposed owner |
| --- | --- |
| Account credentials, password verification, invitations, login, password reset/change, and external OIDC exchange | Omnigent Server and its configured identity provider where applicable. |
| Validation of the accepted human credential and its current account/session state | A qualified Omnigent authentication contract, consumed by one MoonMind adapter. |
| External-identity mapping to a stable MoonMind UUID and local active status | MoonMind's existing user boundary. |
| Workflow, artifact, secret, Profile, administrative, and native-chat permissions | Existing MoonMind authorization owners. |
| Host enrollment, runner tunnels, worker callbacks, and service-to-service authorization | Their existing machine-credential owners. |
| Model-provider OAuth homes, API keys, generation fencing, leases, and cleanup | Existing Provider Profile and Secrets System owners. |

A local MoonMind user row is an application principal and ownership anchor, not a second password account. Removing duplicate credential management does not require removing that row or converting every foreign key to an Omnigent username.

Omnigent authentication does not grant permission to bypass MoonMind's workflow/session binding. The native chat UI remains behind the binding-scoped facade described by the Workflow Chat Panel contract. The proposal changes the source of authenticated identity, not the rule that MoonMind authorizes every protected HTTP, SSE, and WebSocket operation.

## CONTRACT-202 Preferred integration boundary

```text
Browser at MoonMind origin
    -> narrow login/account facade
    -> Omnigent Server account or OIDC login
    -> qualified human-session validation
    -> MoonMind identity adapter
    -> existing MoonMind User UUID
    -> existing resource and operation authorization
```

The preferred implementation delegates to the already-running server through a small allowlisted authentication client. It does not import the whole Omnigent application into MoonMind, read Omnigent's account tables directly, synchronize password hashes, or share the account database as an API.

`GET /auth/me` is the accounts-mode starting point, not a claim that upstream already exposes a complete, provider-neutral token-introspection API. Its verified account-existence check is useful. Credential purpose, identity stability, account lifecycle, and revocation must satisfy the following contracts before that endpoint can serve as the production authority. The presence of an OIDC login client does not establish that Omnigent is an OIDC issuer for MoonMind.

The client uses a deployment-owned endpoint, authenticated or appropriately isolated internal transport, exact permitted routes, finite timeouts, bounded response sizes, and strict response validation. It does not follow arbitrary redirects, accept a workflow-selected auth server, attach a runtime service token in place of the caller's credential, or share a mutable cookie jar across users. Validation caches and in-flight requests, if present, are bounded and isolated by authority and credential.

Only the necessary authentication routes are exposed outside a workflow binding. Login and setup cannot require an existing authenticated chat binding, but they also cannot become an unrestricted `/auth/*` or `/v1/*` proxy. Account-management routes retain their own authentication and authorization. Existing credential stripping on ordinary runtime requests remains in force.

### INV-201 Human and machine credential classes cannot collapse

The integration accepts an explicitly qualified human session. Host enrollment tokens, runner binding tokens, owner JWTs minted for runners, delegated device grants, worker tokens, model OAuth material, and repository credentials do not become unrestricted MoonMind user credentials.

Rejecting only the `Authorization` header is insufficient: a credential in the same upstream session-token format can be placed in a cookie. Calling a verifier that returns only `user_id`, or calling `/auth/me` with a credential that happens to resolve to an existing account, does not establish its purpose.

The preferred resolution is a supported upstream validation/session contract that distinguishes credential purpose and audience or otherwise proves the session was established by the accepted human login flow. Any required upstream change must be broadly useful, narrow, and separately reviewed. A MoonMind-only wrapper around indistinguishable tokens is not evidence of separation.

An alternative is a narrowly established MoonMind browser session created only from a verified upstream human-login exchange, with upstream validation and lifecycle still authoritative. Such a design must justify any additional session state and prove login provenance. It must not offer an exchange endpoint that upgrades an arbitrary Omnigent bearer into a human session. The adoption change chooses one mechanism; it does not ship both as fallback paths.

The pinned verifier alone does not meet this separation requirement. Production adoption remains blocked until the selected mechanism is proven. Required CLI or machine access retains an explicitly scoped contract rather than borrowing browser authority.

## CONTRACT-203 Stable identity and administrative authority

The logical mapping is:

```text
trusted identity-authority namespace + stable subject/account incarnation
    -> existing MoonMind user UUID
```

The authority namespace is deployment-owned and bound to the selected upstream authority. It is not an arbitrary browser-supplied provider string. Repointing the auth endpoint or changing account/OIDC mode cannot silently reconnect identical usernames to existing ownership.

Prefer extending the current identity fields when they can represent the selected contract safely. Do not force a full issuer URL into a short provider column or introduce a second mapping store by default. A schema extension is justified only by an actual identity or migration requirement. Mapping creation and explicit linking are transactional and uniqueness-enforced under concurrent first requests.

A username or email is not automatically an immutable subject. The accepted contract must define rename, deletion, and recreation behavior. A deleted account recreated with the same label must not inherit the old MoonMind user's workflows or secrets. Where upstream lacks a stable account-incarnation identifier, adoption needs a proven no-reuse rule or an explicit linking/tombstone mechanism. An unqualified identifier is not fixed by hashing its spelling.

Omnigent accounts can use usernames that are not email addresses. MoonMind's existing email-bearing schema and API require a deliberate compatible representation or schema change. Never fabricate a verified contact address or automatically merge accounts by a supplied email. Locally disabled users stay disabled after successful upstream authentication. Provisioning a permitted new app principal does not grant access to existing users' resources.

MoonMind remains authoritative for application permissions. An upstream `is_admin` flag is not automatically copied into `User.is_superuser`. Initial local-administrator binding and subsequent grants must be explicit, authenticated, auditable decisions. Administrative powers over the shared account authority remain a trusted deployment boundary, not an isolation guarantee from that authority's administrators.

## CONTRACT-204 Authentication applies consistently to every transport

`get_current_user()` and `get_current_user_optional()` remain the shared API integration points. Composition explicitly chooses the configured provider and fails on unknown values. Audit and converge direct consumers of FastAPI Users, `current_active_user`, `get_jwt_strategy()`, and bespoke WebSocket authentication rather than assuming every route already passes through those dependencies.

The same principal resolution and resource checks cover dashboard bootstrap, API/MCP access where human auth applies, artifact downloads, OAuth enrollment terminals, native-chat documents and assets, SSE, WebSockets, and protected full-page views. Public health, static, login, callback, or setup surfaces are explicitly classified, not accidental exceptions.

Optional authentication preserves existing endpoints that intentionally accept independent worker credentials. Missing user credentials may be optional there; invalid user credentials must not silently produce an anonymous principal, a local administrator, or selection of another credential source. Any endpoint that supports multiple credential classes defines explicit precedence and rejects ambiguous combinations.

The browser uses a same-origin secure session arrangement in supported networked deployments. Production cookies have appropriate host, path, Secure, HttpOnly, and SameSite attributes. The design must qualify the actual reverse proxy, base URL, callback paths, and iframe behavior. Local HTTP exceptions remain explicitly scoped to supported local development, not inferred from arbitrary forwarded headers.

Cookie-authenticated mutations and WebSocket handshakes require appropriate request-forgery and exact-origin defenses. OIDC redirects preserve state, nonce, and PKCE checks and accept only approved return destinations. Never weaken these checks or enable broad cross-origin credential forwarding to fix an embedding problem.

Long-lived user tokens do not belong in URL query strings, browser local storage, rendered HTML, or native-chat bootstrap messages. Replacing the current terminal query-token path is part of adoption, not deferred cleanup. A narrowly scoped one-use transport ticket is acceptable only if a real transport limitation requires it and its authority, lifetime, replay protection, and redaction are defined.

## CONTRACT-205 Session lifecycle and credential hygiene

The browser login facade accepts only the fields required for the human flow. In accounts mode it does not relay caller-controlled requests for unattended refresh credentials. Upstream login token bodies are not reflected into ordinary browser JSON merely because upstream returns them. A changed form or an upstream comment about what browsers normally send is not a security control.

The selected session design defines maximum lifetime, refresh behavior where needed, logout, password change/reset, account disable/delete, and administrative revocation. Cookie clearing, local logout, grant revocation, and global session invalidation are distinct operations. The user interface describes the actual action performed.

Account deletion must deny future authenticated MoonMind access within a documented bound. Logout and revocation have explicit replay tests. Long-lived streams cannot retain authorization forever after expiry, revocation, or permission loss; revalidation/closure follows a bounded policy, with fresh authorization before privileged mutations. Reconnecting reuses no stale authority from a prior browser identity.

Local JWT verification is not the initial default. It would expand signing-key access and can omit account or grant state checks. Any later local-validation optimization needs an approved key-distribution model, required claim validation, bounded account/revocation freshness, rotation behavior, and parity tests. A shared HMAC verification secret also permits token minting, so copying it into MoonMind is a security-boundary decision.

Passwords and raw access, refresh, cookie, or runner credentials never enter Temporal payloads, workflow plans, logs, traces, diagnostics, artifacts, URLs, or source control. User sessions are not runtime credentials. Secret references and authorized safe audit metadata use the existing owners rather than another secret system.

## QUALITY-201 Failure containment and operational behavior

| Condition | Required behavior |
| --- | --- |
| Missing, invalid, expired, deleted-account, or wrong-purpose human credential | Deny authentication without another identity or no-auth fallback. |
| Authenticated user lacks resource or operation permission | Preserve the existing authorization denial. Login success cannot widen access. |
| Omnigent validation times out, is unavailable, or returns malformed data | Return a bounded authentication-service-unavailable outcome, ordinarily HTTP 503, rather than a false invalid-password response or redirect loop. |
| User mapping store is unavailable or linking conflicts | Fail safely and distinguish the condition from invalid credentials. Never return the disabled-mode stub. |
| Auth configuration is incomplete, unknown, or inconsistent | Fail startup/readiness or the affected authenticated surface actionably. Do not infer `disabled` mode. |
| Logout, account switch, or transport replacement | Dispose browser streams, timers, credential-bound caches, and stale view state before another identity can reuse them. |

Initial remote validation deliberately couples protected interactive access to Omnigent Server availability. Stored workflows and artifacts remain intact, but protected reads can be unavailable during an auth outage. Do not promise that local artifact storage makes authenticated history reads independent of the identity service.

No stale-positive authorization fallback is introduced to conceal that tradeoff. A later positive cache requires an explicit revocation-staleness budget and no extension beyond credential expiry. Privileged operations use the documented freshness policy. Authentication calls have bounded concurrency and do not allow chat streams to exhaust all validation/control capacity.

Already-admitted durable work continues under its existing workflow and machine-authority rules. Browser logout is neither automatic workflow cancellation nor permission for a new human action. Administrative disable/cancellation policy is handled by the owning workflow mechanisms. Workers never depend on a human browser refresh token to preserve or clean up an execution.

## INV-202 Migration preserves ownership and recoverability

Removal of the bundled service and migration of authentication are separate concerns. Keycloak may be removed independently only when no installation or retained-data consumer requires it; that removal never establishes Omnigent authentication by itself.

Existing MoonMind UUIDs, foreign-key relationships, workflow ownership, audit actors, Provider Profiles, and historical evidence retain their identity. Account linking uses authenticated proof or an explicit operator-approved mapping. It does not rewrite historical actors to a new username or remap every user to the first Omnigent account.

In an existing disabled-auth deployment, the default user's work belongs to the selected local principal. Connecting it to an Omnigent administrator requires an explicit secure ownership-claim decision. The first remote visitor, first successful login, or matching email cannot automatically claim that principal.

A fresh deployment's setup path must prevent an unintended remote first-admin claim and use supported upstream setup/bootstrap behavior. It must complete the normal local journey without a permanent insecure bypass. Existing upstream local-account migration behavior is not evidence that MoonMind's application data has been migrated.

Cutover has a recoverable database/configuration snapshot, verified ownership mapping, a compatible previous artifact where rollback is required, and an operator-controlled recovery path. Recovery cannot mean enabling unauthenticated network access. Mapping/schema changes and active workflows require compatibility evidence; restoring a container alone is not necessarily a rollback.

No authentication cleanup deletes Provider Profile-owned OAuth homes, host enrollment state, unrelated database roles, shared database instances, or retained workflow evidence. Old Keycloak state is retained or removed according to explicit backup/retention decisions, not a destructive Compose-volume shortcut.

## DOC-REQ-202 Configuration simplification and current-contract differences

The candidate provider selector is an explicit `AUTH_PROVIDER=omnigent` branch in the existing configuration boundary, not a parallel setting family layered over ambiguous defaults. The final names and precedence belong to the configuration owner upon adoption.

MoonMind's human-auth mode and Omnigent's accounts/OIDC/header mode have separate meanings. Their selected combination must be validated. Header trust is not a way for public callers to assert an email, and choosing Omnigent cannot silently choose an upstream single-user fallback.

The supported replacement is qualified before bundled Keycloak's service, realm/setup assets, database initialization, settings, developer helpers, and obsolete tests are retired. Inventory actual consumers before deletion. Superseded MoonMind password/register/reset routes are removed or unmounted in the new mode. Storage/model helpers still needed by the application are retained without preserving a second public login system.

This changes the identity source behind today's MoonMind authentication boundary. It does not override the existing Workflow Chat requirement that MoonMind authenticates and authorizes each request, and it does not change model-provider OAuth ownership. Existing defaults and disabled-auth behavior remain current until adoption explicitly changes them.

Pre-release cleanup removes superseded internal paths cohesively. Compatibility exists only for actual persisted data, in-flight work, or an explicit cutover requirement, not speculative aliases or permanent dual-auth fallbacks. Obsolete configuration must fail actionably rather than silently change security posture.

## QUALITY-202 Required acceptance evidence

These are proposed observable requirements, not a completed test checklist.

| Boundary | Required proof |
| --- | --- |
| Production composition | The configured provider selects the intended dependency, and old password/registration routes cannot bypass it. Unknown modes fail safely. |
| Human versus machine authority | Host, runner-owner, delegated, worker, and wrong-authority tokens cannot obtain human authority through headers, cookies, login exchange, or a current-user endpoint. Include the pinned same-format runner-token regression. |
| Identity and roles | Concurrent provisioning is stable; conflicting links, renamed/recreated accounts, local disable, authority changes, and forged admin claims cannot inherit ownership or elevate privileges. |
| Multi-user resources | User A cannot read or mutate user B's workflows, artifacts, Profiles, secrets, terminals, or bound chat using modified URLs, IDs, cookies, or streams. |
| Browser journey | Real login, permitted setup/invitation, logout, reload, expiry, iframe/full-page chat, artifact access, SSE, and WebSocket flows work through the composed API and actual browser client. |
| Session lifecycle | Deleted accounts, logout replay, password reset/change, administrative revocation, refresh races, and long-lived transport expiry satisfy documented bounds. |
| Proxy and browser security | Origin/CSRF, redirect-state/PKCE where applicable, forwarded-header spoofing, refresh-field injection, query-token leakage, and cross-user client-cookie contamination are rejected. |
| Failure containment | Server outage, malformed validation response, cache expiry, mapping-store failure, overload, and reconnect yield bounded outcomes without authentication downgrade or retry storms. |
| Migration and removal | Existing UUIDs, permissions, OAuth state, histories, and active-work cleanup survive cutover; rollback restores a supported secure posture without resurrecting removed bypasses. |
| Evidence quality | Reports distinguish source review, hermetic API tests, real browser/container tests, and external-IdP qualification. A passing accounts row cannot qualify OIDC or trusted-header deployments. |

Use the existing required unit and hermetic integration owners. Networked authentication and browser behavior need real composition-boundary tests, not only mocked verifier success. External OIDC qualification is separately authorized; required CI must not need production identities or model inference. Adoption records exact MoonMind, Omnigent Server, configuration, and frontend artifacts plus the unexecuted or blocked cases.

## DESIGN-201 Alternatives, effort, and adoption

| Alternative | Assessment |
| --- | --- |
| Delegate to the running Omnigent Server through one narrow adapter | Preferred. Reuses account ownership and avoids another permanent service. Accepts a documented availability dependency and requires a qualified human-session contract. |
| Import Omnigent's verifier and share signing material | Not the initial default. Smaller request overhead, but tighter implementation/key coupling and incomplete account/revocation semantics unless deliberately supplied. |
| Establish a narrow local browser session after upstream human login | Conditional alternative when it is the simplest way to prove human-login provenance. Does not authorize a second password system or arbitrary bearer exchange. |
| Retain bundled Keycloak or use an external identity provider | Appropriate only for explicit requirements not satisfied by the qualified replacement. External OIDC can preserve those deployment choices without bundling Keycloak. |
| Copy Omnigent's account implementation or replace MoonMind authorization with upstream sharing | Rejected. Creates duplicate account authority or removes application governance. |

The expected work is moderate: the current-user abstraction and UUID user model limit the refactor, while transport coverage, account linking, session lifecycle, and credential-purpose separation require substantive engineering. No schedule or claim of enterprise-feature parity is implied. Missing upstream identity/session capabilities can increase the scope and must be resolved before default promotion, not hidden behind a permissive adapter.

Adoption requires agreement on the exact upstream human-session/identity contract, stable subject or account-incarnation policy, administrator binding, session invalidation bounds, supported origin topology, and treatment of existing users. Accounts mode can be qualified first without claiming OIDC or header-mode support. This proposal cannot itself resolve those source-dependent choices by inventing an upstream API.

The affected owners are the current auth dependencies and API composition, user model/migrations, direct HTTP/WebSocket consumers, dashboard account/bootstrap flows, native-chat facade, and Compose/configuration/operator documentation. Reconcile the providing Security, UI, and Omnigent documents when the proposal is selected. Execution plans and issue breakdowns remain outside this document.

Follow the [proposal lifecycle](README.md): promote adopted durable contracts into their owning documents and remove the superseded proposal/discovery entry in the same adoption change. Until then, this document records a design for later consideration and authorizes no implementation or deployment change.

## Source references

[mm-auth-providers]: https://github.com/MoonLadderStudios/MoonMind/blob/8ff5144bd03c8f006621eabc982e12ebac944a30/api_service/auth_providers.py
[mm-main]: https://github.com/MoonLadderStudios/MoonMind/blob/8ff5144bd03c8f006621eabc982e12ebac944a30/api_service/main.py
[mm-user]: https://github.com/MoonLadderStudios/MoonMind/blob/8ff5144bd03c8f006621eabc982e12ebac944a30/api_service/db/models.py
[mm-websocket]: https://github.com/MoonLadderStudios/MoonMind/blob/8ff5144bd03c8f006621eabc982e12ebac944a30/api_service/api/websockets.py
[mm-compose]: https://github.com/MoonLadderStudios/MoonMind/blob/8ff5144bd03c8f006621eabc982e12ebac944a30/docker-compose.yaml
[og-auth]: https://github.com/omnigent-ai/omnigent/blob/f04b0354fb5344c1ea8b92795ceb6760a9ad7595/omnigent/server/auth.py
[og-accounts]: https://github.com/omnigent-ai/omnigent/blob/f04b0354fb5344c1ea8b92795ceb6760a9ad7595/omnigent/server/routes/accounts_auth.py
