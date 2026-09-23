# Keycloak Removal Plan

**Document Class:** Imperative working document
**Status:** Account-replacement plan superseded; scoped legacy retirement reference only
**Reviewed:** 2026-09-20
**Current target:** [Single-User Application Design](../SingleUserApplicationDesign.md)
**Deployment owner:** [Docker Compose Update System](../Steps/DockerComposeUpdateSystem.md)
**Remaining operational record:** [#4131](https://github.com/MoonLadderStudios/MoonMind/issues/4131)

## 1. Current decision

MoonMind is a single-user application, not an application-account product. The former proposal to replace Keycloak with built-in accounts, invitations, roles, identity linking, and an application-login mode matrix is superseded. Do not implement those features, a new upstream account adapter, or a seeded default person to complete this old plan.

The current design keeps one protected operator-admission boundary and separate scoped machine/provider authority. It does not authorize disabling deployed access controls, exposing a multi-person data set, or deleting identity data. Intended architecture is not proof that a particular release or deployment has finished migrating.

This existing file remains only to redirect old references and preserve necessary legacy deployment guidance. It is not another canonical security contract, migration coordinator, approval step, or reason to block unrelated implementation.

## 2. Work ownership

| Former package | Current disposition |
| --- | --- |
| K1 inventory | Inspect only actual retained data and affected deployment consumers through existing tools. Repository eligibility belongs to #4346; live Keycloak obligations remain in #4131. |
| K2 upstream account qualification | Superseded as a product goal. Reuse primitives only for actual operator/machine boundaries in #4347 and #4352; do not build an account integration to retire accounts. |
| K3 identity/account lifecycle | Account lifecycle is not planned. #4346 and subsystem migrations preserve eligible data, with protected refusal for mixed or unresolved attribution. |
| K4 API/dashboard cutover | #4347, #4351, #4352, and #4353 migrate real product consumers. Demonstration endpoints and always-admin substitutes do not complete the change. |
| K5 code/docs/test cleanup | Feature owners remove their replaced code, #4354 handles shared residuals, #4356 owns missing integrated tests, and #4357 reconciles documentation. #4128 and #4130 are superseded, not additional account-roadmap prerequisites. |
| K6 deployment/retention | #4355 owns the existing update/recovery implementation. #4131 records any remaining, separately authorized legacy service/shared-consumer/data disposition. |

Current issue descriptions govern remaining work. Reuse landed code, tests, and partial branches. A closed predecessor is not proof of full product readiness or an instruction to recreate its old behavior.

## 3. Determine whether legacy work remains

For a deployment actually being changed, use authorized bounded reads to identify relevant Keycloak services, exposed routes, shared realm consumers, source data, credentials, and private recovery obligations. No broad inventory of unrelated systems is required. A repository search or removed Compose entry cannot prove that an old container or external consumer is gone.

When there is positive evidence that no relevant legacy dependency remains, record that disposition and do no additional migration. Do not recreate an identity service or demand account-era rehearsals. Missing permissions, incomplete observations, and unreachable deployments remain unknown, not zero and not evidence that a new subsystem is needed.

Deployments remain independent. Observations about one device do not certify another or create an all-devices readiness gate for the first device. Pending operational access is not a code defect and does not consume implementation retries.

## 4. Protected transition and recovery

Use the existing conversion and in-place deployment owners. Preserve actual operator URLs, published bindings, ingress/MFA requirements, credential confidentiality, and admitted work. Establish the replacement protection before removing the old protection. The single-user migration must reject genuinely mixed or unresolved data before broadening visibility.

Under explicit production authorization, stop or migrate only the identified obsolete service/consumer after verifying its replacement. Shared realm consumers cannot be abandoned because MoonMind no longer needs the realm. Distinguish application-login material from provider OAuth, GitHub/registry credentials, worker capabilities, and Omnigent runtime sessions.

Keep the minimum private, restore-usable data/configuration/key material needed for the real recovery window. Do not publish exports, tokens, cookie material, or raw identity records. Backward recovery is valid only where schema, data, retained history, and intervening writes permit it. Otherwise use the existing forward-repair path, not restoration of a whole shared PostgreSQL/Temporal snapshot over newer work.

No broad volume prune, `down -v`, unrelated credential revocation, or deletion of shared roles/databases is permitted by this plan. Irreversible removal requires explicit authority and a verified resource owner. Temporary recovery support has concrete consumers and a removal condition, not a permanent parallel login or fleet system.

## 5. Verification and reporting

Repository correctness uses existing focused boundary tests and current-candidate CI: real operator routes, browser/origin protections, scoped machine access, eligible conversion and protected refusal, retained work, and the actual update/recovery path. Reuse the owning tests instead of another accounts-by-mode-by-client conformance matrix.

Live observations stay in #4131 or protected operation records. Verify the actual configured operator route and relevant retired-token/service behavior for each performed operation. Container health, a fixture, and a model verdict do not prove a live cutover. Authorized automation may perform these checks; manual rehearsal is not the default method or an unrelated PR prerequisite.

Record actual completed, not-applicable, and pending work separately. Closing an obsolete implementation issue neither certifies a deployment nor cancels its remaining retention obligation. Do not claim work is scheduled unless an execution owner accepted it.

## 6. Historical evidence and eventual retirement

The [original account-replacement plan](https://github.com/MoonLadderStudios/MoonMind/blob/65e1cf06b8bc82cab3a8ccf5c880d2cea7681b72/docs/tmp/KeycloakRemovalPlan.md) retains the September 7 source analysis, old package details, and upstream references. It describes a superseded target, not instructions to restore it. Consult it only for a concrete old-release data or recovery question.

Keep lasting behavior in the single-user and owning security/deployment documents. Remove or archive this temporary reference after its remaining backlinks and real recovery consumers have another home. No new permanent retirement registry is needed.

This review changed documentation and backlog scope only. It did not inventory a live realm, execute a migration, revoke credentials, or retire a service.
