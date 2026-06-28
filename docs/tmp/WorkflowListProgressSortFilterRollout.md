# Workflows List Progress Sort and Filter Rollout

Status: Tracking note  
Owners: MoonMind Engineering  
Last updated: 2026-06-28  
Canonical contract: `docs/UI/WorkflowsListPage.md`

This is a temporary rollout and implementation note. It is not the canonical product contract. The enduring desired state and test contract live in `docs/UI/WorkflowsListPage.md`.

---

## 1. Scope

Implement Progress sorting and filtering for the Workflows List page without changing the page's workflow-oriented product stance.

Progress behavior must stay backed by bounded execution progress summary data. The list page must not fetch per-row step ledgers or full step details to compute Progress display, sort, or filter behavior.

---

## 2. Visibility guardrail

Do not index `progress.currentStepTitle`, current-step-title search text, or current-step-title tokens as Temporal Visibility Search Attributes.

Current step titles can come from workflow plans or user input and may change frequently. They are high-cardinality display prose and must stay in projection storage or another bounded query model that is not operator-visible Temporal metadata.

Only numeric or categorical derived Progress fields may be considered for Temporal Visibility indexing, and only after an explicit Visibility registry update approves the specific field.

---

## 3. Implementation sequence

1. Extend the frontend filter model with a structured Progress filter:
   - completion range;
   - bucket include/exclude;
   - signal include/exclude;
   - current-step title text;
   - blank include/exclude.
2. Add Progress to the desktop header filter map, advanced drawer fields, mobile filter sheet, active filter chips, URL parser, URL serializer, reset logic, and filter summaries.
3. Add current-page Progress sorting as an interim behavior only if the UI keeps the current-page-only notice.
4. Add server-side Progress query parameters and validation.
5. Materialize or otherwise serve derived Progress fields so Progress sorting and filtering are deterministic across pages.
6. Keep current-step-title search in projection or bounded query-model storage, not Temporal Visibility.
7. Promote Progress sorting to server-authoritative URL/API state only after the backend can apply `sort=progressPct` before pagination.

---

## 4. Validation checklist

Add tests that cover:

- Progress header filter visibility;
- Progress current-page sort with blanks last;
- Progress URL/API serialization;
- active Progress chips;
- mobile filter drawer Progress controls;
- no list-page step-detail fetches;
- backend validation for contradictory Progress include/exclude params;
- no Temporal Visibility indexing for current-step title prose or tokens.
