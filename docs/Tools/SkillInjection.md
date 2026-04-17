# Skill Injection

Status: Superseded by `docs/Tools/SkillSystem.md`
Owners: MoonMind Platform / MoonMind Engineering  
Related: `docs/Tools/SkillSystem.md`, `AGENTS.md`, `docs/ManagedAgents/CodexCliManagedSessions.md`

---

> This document is retained for historical context. The canonical runtime-delivery
> and managed-runtime injection rules now live in `docs/Tools/SkillSystem.md`.

## 1. Purpose

This document defines how MoonMind should **inject resolved agent skills into a runtime** for execution.

It focuses on the runtime delivery contract after skill selection and version resolution have already happened.

This document answers:

1. what the runtime should receive
2. where the runtime should see active skills
3. how MoonMind should avoid repo mutation while still using expected skill paths
4. how collisions should be handled
5. what should appear inline in step instructions versus on disk

This document does **not** redefine:

1. skill catalog storage
2. skill precedence rules
3. task and step skill selection semantics
4. artifact storage internals

Those concerns remain in the broader Agent Skill System design.

---

## 2. Summary

MoonMind should standardize on **one canonical managed-runtime skill injection model**:

1. resolve the exact skill set for the step into an immutable snapshot
2. materialize the full selected skill bundle into a run-scoped internal active directory
3. expose that active directory to the runtime at the expected visible path: `.agents/skills`
4. include a compact inline activation summary in the step instructions
5. keep retrieval-based skill loading out of the normal execution path for now

In short:

- **full skill bundle on disk**
- **compact activation summary inline**
- **`.agents/skills` as the runtime-visible path**
- **run-scoped backing storage owned by MoonMind**

This is the default and preferred model for managed coding runtimes.

---

## 3. Goals

The skill injection system must:

1. keep the runtime-visible path predictable
2. avoid relying on repo-specific skill placement or repo scans during execution
3. avoid mutating checked-in skill sources in place
4. expose only the selected active skills for the current run or step
5. preserve replayability and provenance through immutable refs
6. reduce prompt bloat by keeping full skill bodies out of the inline instruction payload
7. minimize model confusion by using a well-known visible path
8. make collisions rare and diagnosable

## 3.1 Non-goals

This document does not aim to:

1. support multiple first-class injection modes for managed runtimes
2. make MCP or retrieval-based loading part of the normal path
3. require the repo to permanently contain MoonMind-managed skill files
4. treat checked-in `.agents/skills` folders as mutable runtime state
5. make per-skill leaf mounting the canonical design

---

## 4. Locked decisions

This document locks the following decisions.

1. Managed coding runtimes use **one standard skill injection mode**.
2. The mode is: **compact inline activation summary + read-only mounted full skill bundle**.
3. The canonical runtime-visible path is **`.agents/skills`**.
4. The canonical backing storage is a **run-scoped MoonMind-owned active directory**, not the checked-in repo path itself.
5. MoonMind injects the **entire active skill root** at `.agents/skills`, even when only one skill is selected.
6. Repo-visible checked-in skill folders are inputs to resolution, not mutable runtime state.
7. Retrieval- or MCP-based skill loading is deferred.
8. Runtimes must not re-resolve or rediscover skills during execution.

---

## 5. Why this model

### 5.1 Why not inline full skill bodies only

Inlining full skill bodies increases prompt cost, weakens reuse of larger skills, and makes it harder to preserve an exact runtime-visible artifact boundary.

### 5.2 Why not retrieval or MCP first

Retrieval adds a second execution-time dependency and makes the agent responsible for fetching material it may need immediately. That increases complexity and creates a new failure mode where the agent never loads the intended skill content.

### 5.3 Why not a brand-new visible path

A new visible path would reduce collisions, but it would also make the runtime depend on a MoonMind-specific convention that agents are less likely to inspect naturally. Using `.agents/skills` preserves a familiar and expected location while still allowing MoonMind to keep the backing storage elsewhere.

### 5.4 Why not per-skill leaf mounting as the default

Mounting only `.agents/skills/pr-resolver` can work technically for a single-skill case, but it does not scale as cleanly. Owning the entire active root keeps the model stable whether the active set contains one skill or many.

---

## 6. Canonical mental model

MoonMind should distinguish between three different things.

### 6.1 Skill inputs

These are the possible sources used during resolution, such as:

- deployment-managed skills
- built-in skills
- repo-checked-in skills
- repo local-only skills

### 6.2 Active backing store

This is the run-scoped, MoonMind-owned, immutable materialization of the **resolved active skill snapshot**.

Example backing path:

```text
/work/agent_jobs/<job_id>/runtime/skills_active/<snapshot_id>/
```

### 6.3 Runtime-visible active path

This is the path the agent sees and should rely on:

```text
<workspace>/repo/.agents/skills
```

The runtime-visible path is a projection of the backing store, not the authoritative storage model.

---

## 7. Canonical injection contract

For managed runtimes, MoonMind should always perform the following sequence before execution begins.

### 7.1 Resolve

Resolve task and step skill intent into an immutable `ResolvedSkillSet`.

### 7.2 Materialize

Materialize the resolved active skill set into a run-scoped internal directory.

### 7.3 Project

Expose that internal active directory at `.agents/skills` for the runtime.

### 7.4 Activate

Include a compact activation summary in the step instructions telling the agent:

1. which skills are active
2. where they are available
3. any must-follow rules that should be applied immediately

---

## 8. Runtime-visible filesystem shape

The active skill projection should look like this to the runtime:

```text
.agents/
  skills/
    _manifest.json
    pr-resolver/
      SKILL.md
      examples/
      templates/
    safe-python-edit/
      SKILL.md
```

Notes:

1. `SKILL.md` remains the primary entrypoint for a skill.
2. `_manifest.json` is MoonMind-owned metadata for active-snapshot introspection.
3. The active tree contains **only the selected skills for the current run or step**.
4. Unselected skills must not appear in the runtime-visible active tree.

---

## 9. Backing-store shape

A representative internal materialization shape is:

```text
/work/agent_jobs/<job_id>/runtime/skills_active/<snapshot_id>/
  _manifest.json
  pr-resolver/
    SKILL.md
    examples/
    templates/
  safe-python-edit/
    SKILL.md
```

The exact internal path is implementation-specific, but the following rules are fixed:

1. it must be run-scoped
2. it must be owned by MoonMind rather than by repo authors
3. it must be read-only to the runtime where practical
4. it must correspond exactly to one resolved snapshot

---

## 10. Projection rules

MoonMind may expose the active backing store at `.agents/skills` through any adapter-compatible mechanism, including:

- bind mount
- symlink
- overlay view
- copy-on-write staging created before the runtime starts

The mechanism is not the contract.

The contract is:

1. the runtime sees the active skill set at `.agents/skills`
2. the content corresponds exactly to the pinned resolved snapshot
3. the runtime is not expected to discover skills anywhere else

---

## 11. Inline activation summary

The step instructions should always include a small activation block.

### 11.1 Required contents

The block should contain:

1. the active skill names
2. the visible path `.agents/skills`
3. any hard constraints that must be obeyed immediately
4. optional hints about which skill to read first

### 11.2 Example

```text
Active MoonMind skills for this step:
- pr-resolver: analyze review feedback, group findings, and propose minimal fixes
- safe-python-edit: make minimal safe Python changes and preserve behavior

Full skill content is available under .agents/skills.
Read .agents/skills/pr-resolver/SKILL.md before preparing the fix plan.
Hard rules:
- do not broaden scope beyond the reported issue
- prefer the smallest change that resolves the root cause
```

### 11.3 Size rule

The inline activation block should remain compact. It is not a duplicate full-body skill bundle.

---

## 12. Collision policy

Collision handling should be explicit and fail-fast.

### 12.1 Canonical rule

MoonMind owns the **runtime-visible active projection** at `.agents/skills` for the duration of the run.

### 12.2 What counts as a collision

A collision exists when MoonMind cannot safely expose the resolved active skill root at `.agents/skills`.

Examples:

- `.agents` exists as a file instead of a directory
- `.agents/skills` exists as a file instead of a directory or replaceable mount point
- adapter or filesystem restrictions prevent the active projection from being installed

### 12.3 What does not count as a separate collision class

If MoonMind owns the entire `.agents/skills` projection, then a checked-in `.agents/skills/pr-resolver` path is not handled as an independent special case. It is part of the broader `.agents/skills` projection boundary.

### 12.4 Preferred behavior

1. materialize the active set outside the repo
2. project it at `.agents/skills`
3. do not mutate checked-in skill sources in place
4. fail before runtime launch if projection cannot be installed safely

### 12.5 Error behavior

Projection failures must surface a clear pre-launch error describing:

- the conflicting path
- the conflicting object kind
- the attempted projection action
- the suggested operator remediation

---

## 13. Repo-provided skill folders

Repo-provided skill folders remain valid **inputs** to the resolution process.

They are not the canonical runtime state.

If the repo contains:

```text
.agents/skills/
```

that content may be considered during resolution if policy allows it, but the runtime-visible active state still comes from the resolved snapshot projection.

This means:

1. checked-in repo skills may influence selection
2. selected repo skills may appear in the active projection
3. the original checked-in directory should not be mutated in place during runtime setup

---

## 14. Single-skill runs

When only one skill is selected, the active projection should still expose the full root:

```text
.agents/skills/
  _manifest.json
  pr-resolver/
    SKILL.md
```

MoonMind should **not** change the architectural model just because only one skill is active.

The only simplification is that the active root contains a single skill directory.

---

## 15. Manifest contract

Each active projection should include a small MoonMind-owned manifest.

Suggested fields:

- `snapshot_id`
- `resolved_at`
- `skills[]`
- `source_summary`
- `visible_path`
- `backing_path`
- `read_only`

Example:

```json
{
  "snapshot_id": "rss_123",
  "visible_path": ".agents/skills",
  "read_only": true,
  "skills": [
    {
      "name": "pr-resolver",
      "version": "1.2.0"
    }
  ]
}
```

The manifest is for observability and adapter introspection. The agent should primarily read `SKILL.md`.

---

## 16. Runtime adapter responsibilities

Runtime adapters are responsible for the final projection mechanics, but they must preserve the same logical behavior.

Each adapter must:

1. consume a pinned resolved snapshot ref
2. materialize the active backing store exactly once per snapshot
3. expose the active set at `.agents/skills`
4. include the activation summary in the runtime instructions
5. avoid re-resolving or broadening the selected skill set

Adapters must not:

1. scan the repo for additional skills during execution
2. add unselected skills to the visible tree
3. silently fall back to a different visible path
4. rewrite checked-in skill inputs in place

---

## 17. Security and trust model

1. Repo-provided skills remain potentially untrusted input.
2. Deployment policy decides whether repo and local-only skills are allowed.
3. The active projection should be read-only to the runtime where possible.
4. Skill content must not be treated as a secret store.
5. The runtime should not be allowed to mutate the resolved active bundle as a way of changing policy.

---

## 18. Observability

MoonMind should record and surface:

1. the resolved snapshot ref
2. the active backing path
3. the visible path `.agents/skills`
4. the projected skill names and versions
5. whether the projection was read-only
6. any collision or projection failures
7. the exact activation summary delivered with the step

This makes it possible to answer both:

- what skill set was selected
- what skill view the runtime actually saw

---

## 19. Test requirements

Changes to skill injection should include tests at the real adapter or activity boundary.

Minimum coverage should include:

1. single-skill active projection at `.agents/skills`
2. multi-skill active projection at `.agents/skills`
3. read-only backing-store materialization
4. activation-summary injection into the runtime instruction payload
5. collision failure when `.agents` or `.agents/skills` is incompatible
6. exact-snapshot replay across retry or rerun
7. repo-skill input participation without in-place mutation

---

## 20. Deferred work

The following are intentionally deferred:

1. retrieval-based or MCP-based skill loading
2. multiple managed-runtime injection modes
3. custom visible paths other than `.agents/skills`
4. runtime-specific skill discovery conventions as a primary contract
5. partial per-skill leaf mounting as a canonical architecture mode

---

## 21. Summary

The desired-state MoonMind skill injection model is:

1. resolve skills centrally
2. materialize the full active bundle into a run-scoped MoonMind-owned backing store
3. expose that bundle at the expected visible path `.agents/skills`
4. include a compact activation summary in the step instructions
5. keep retrieval and alternate visible-path schemes out of the normal path

This preserves the reliability benefits of expected skill locations while still avoiding repo mutation, reducing collisions, and keeping the control-plane contract clean.
