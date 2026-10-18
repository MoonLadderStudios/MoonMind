# MoonSpec Document Model

MoonSpec turns an outcome or design into bounded implementation work. Its documents preserve the requested behavior through breakdown, specification, planning, and execution. They do not establish a separate instruction hierarchy: the operator's current request and applicable root or scoped `AGENTS.md` files set requirements.

## Durable documentation and run artifacts

Durable documentation under `docs/` explains architecture, interfaces, behavior, operating procedures, and useful design rationale. It may describe current implementation or an explicitly proposed design; make that distinction clear when it matters. “Canonical” means the maintained explanation of a topic, not an independent source of task authority.

Run-scoped `spec.md`, `plan.md`, `tasks.md`, `research.md`, `contracts/`, story breakdowns, and handoffs live under gitignored `specs/` or `artifacts/` paths. They help execute a particular task and do not silently supersede its requested outcome or constraints. Preserve saved work needed to resume an active run even though it is not durable repository documentation.

One-time migration plans, rollout checklists, and progress trackers belong in issues, PRs, or gitignored run artifacts. Repeatable operating instructions can remain in `docs/`; using steps does not make an operating guide a temporary plan.

## Source packet labels

Existing specification and breakdown packets use these descriptive labels:

- **Canonical declarative document:** a maintained explanation or design describing behavior and constraints.
- **Temporary execution artifact:** a run-scoped spec, plan, or other derived working material.
- **Imperative working document:** input primarily organized as delivery steps, phases, or a progress checklist.
- **Original request:** the operator's request when there is no source document.

Classify mixed input by its primary framing. These labels describe the input for existing workflow consumers; they do not rank its authority or require a metadata header in the source document.

## Design input and checklist fallback

For breakdown, prefer input describing desired behavior and constraints when it is available. If only a checklist or implementation plan is available, extract the underlying user or operational outcomes rather than blocking solely because of the input's form. Record that the source was procedural input. Do not make checklist steps, phases, or status rows into story boundaries unless they independently deliver valuable, testable outcomes.

Specification produces a declarative, single-story spec: what the story should accomplish, its constraints, and how success can be checked. When input is procedural, translate it into desired-state requirements and retain assumptions and unresolved choices explicitly. Planning and task artifacts can then describe how to implement that story.

## Reconcile changes without losing intent

Compare a derived artifact with the source requirements actually adopted by the task. A spec, plan, or story breakdown cannot resolve a conflict merely by restating a different requirement. Use the current request, applicable `AGENTS.md` files, and evidence to correct stale documentation or derived artifacts; raise a genuine unresolved scope decision rather than silently changing acceptance criteria.

Keep affected documentation accurate in the implementation change. Code establishes what currently exists, not whether a regression is acceptable. Preserve authorized desired behavior when implementation is incomplete, and report the gap without presenting an unimplemented proposal as shipped behavior.

Read only the source and references needed for the task. Repository-specific organization and authoring advice belongs in the consumer repository; MoonSpec adds no mandatory documentation read order or taxonomy.
