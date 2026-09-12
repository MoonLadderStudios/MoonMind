# Review dimensions

Answer each dimension once, with concise evidence and disposition. Do not add a
parallel scoring framework. Scope deeper investigation to the requested documents.

| Dimension | Evidence and decision |
| --- | --- |
| Necessity | Is it current, useful, unique or obsolete? Recommend keep/update/merge/archive/delete while preserving useful unique content. |
| Implementation drift | Extract claims; distinguish accurate, stale factual text, unimplemented/partially implemented intent, missing coverage and ambiguous evidence. |
| Strategy and coherence | Compare README, constitution and owning contracts. Check conflicts, authority, metadata, embedded rationale, duplicate contracts, imperative leakage and unverifiable facts where local conventions require them. A proposed design is intent, not a false implementation claim. |
| Simplification | Name a simpler strategy and its tradeoffs; preserve required application functionality. |
| Engineering quality | Assess maintainability, testability, coupling, module boundaries and reuse of existing infrastructure. |
| Merge | Name overlap, owner, destination, unique sections to preserve and dependent references. |
| Split | Inspect natural topic/authority boundaries and maintenance value. A large line count alone never warrants splitting. |
| Location | Follow actual repository taxonomy/casing; name destination and inbound/relative reference repairs. |

Use `keep`, `update`, `merge`, `split`, `move`, `archive`, `delete`, or
`reference_repair` as dispositions. A keep decision may coexist with an
implementation-gap finding that needs code work, not a document rewrite.
