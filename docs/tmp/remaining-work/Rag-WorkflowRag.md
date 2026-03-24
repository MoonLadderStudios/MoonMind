# Remaining work: `docs/Rag/WorkflowRag.md`

**Source:** [`docs/Rag/WorkflowRag.md`](../../Rag/WorkflowRag.md)  
**Last synced:** 2026-03-24

## Open items

### Implementation checklist (§)

Unchecked in source:

- [ ] Ship `moonmind rag` tools inside `temporal-worker-sandbox` image.
- [ ] `RetrieveAgentContextActivity` wrapping `ContextRetrievalService.retrieve()`.
- [ ] `PrepareWorkspaceActivity` validates Qdrant topology before agent container launch.
- [ ] Agent prompts advertise “RAG tools available.”
- [ ] Observability metrics for embed/search/upsert from sandbox tools.
- [ ] Document `ContextPack` schema (`moonmind/rag/context_pack.py`).
