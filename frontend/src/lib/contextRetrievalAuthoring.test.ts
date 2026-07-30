import { describe, expect, it } from 'vitest';

import {
  DEFAULT_RETRIEVAL_CEILINGS,
  RetrievalCeilings,
  applyBudgetPreset,
  clampFollowUpRetrieval,
  compileContextRetrievalParameters,
  defaultContextRetrievalAuthoring,
  explainRetrievalDenials,
  hasAuthoredContextRetrieval,
  parseContextRetrievalParameters,
} from './contextRetrievalAuthoring';

const NARROW_CEILINGS: RetrievalCeilings = {
  ...DEFAULT_RETRIEVAL_CEILINGS,
  collections: ['repo'],
  topK: { min: 1, max: 10, default: 8 },
  allowStaleOverlay: false,
  allowFallback: false,
};

describe('contextRetrievalAuthoring', () => {
  it('defaults follow-up retrieval to disabled (authority boundary opt-in)', () => {
    const value = defaultContextRetrievalAuthoring();
    expect(value.followUp.enabled).toBe(false);
    expect(hasAuthoredContextRetrieval(value)).toBe(false);
    expect(compileContextRetrievalParameters(value)).toEqual({});
  });

  it('applies budget presets and marks custom edits', () => {
    let followUp = defaultContextRetrievalAuthoring().followUp;
    followUp = applyBudgetPreset(followUp, 'generous');
    expect(followUp.budgetPreset).toBe('generous');
    expect(followUp.topK).toBe(16);
    followUp = applyBudgetPreset(followUp, 'custom');
    expect(followUp.budgetPreset).toBe('custom');
    // custom keeps the previous numbers
    expect(followUp.topK).toBe(16);
  });

  it('clamps authored budgets and collections within ceilings', () => {
    const value = defaultContextRetrievalAuthoring();
    value.followUp.enabled = true;
    value.followUp.collections = ['repo', 'secret', 'repo'];
    value.followUp.topK = 999;
    value.followUp.staleOverlayAllowed = true;
    value.followUp.fallbackAllowed = true;
    const clamped = clampFollowUpRetrieval(value.followUp, NARROW_CEILINGS);
    expect(clamped.collections).toEqual(['repo']);
    expect(clamped.topK).toBe(10);
    expect(clamped.staleOverlayAllowed).toBe(false);
    expect(clamped.fallbackAllowed).toBe(false);
  });

  it('caps maxQueries at the backend contract ceiling (100)', () => {
    // The backend `RetrievalCapabilityIssue.max_queries` is le=100; the UI
    // ceiling must match so 101-120 is not accepted only to fail issuance.
    expect(DEFAULT_RETRIEVAL_CEILINGS.maxQueries.max).toBe(100);
    const value = defaultContextRetrievalAuthoring();
    value.followUp.enabled = true;
    value.followUp.collections = ['repo'];
    value.followUp.maxQueries = 120;
    const compiled = compileContextRetrievalParameters(value);
    expect(compiled.followUpRetrieval).toMatchObject({ maxQueries: 100 });
  });

  it('compiles rag and followUpRetrieval fragments', () => {
    const value = defaultContextRetrievalAuthoring();
    value.initial.collections = ['repo', 'docs'];
    value.initial.allowStale = true;
    value.followUp.enabled = true;
    value.followUp.collections = ['repo'];
    value.followUp.topK = 6;
    const compiled = compileContextRetrievalParameters(value);
    expect(compiled.rag).toEqual({ collections: ['repo', 'docs'], allowStale: true });
    expect(compiled.followUpRetrieval).toMatchObject({
      enabled: true,
      collections: ['repo'],
      topK: 6,
    });
  });

  it('does not emit followUpRetrieval when disabled', () => {
    const value = defaultContextRetrievalAuthoring();
    value.initial.required = true;
    const compiled = compileContextRetrievalParameters(value);
    expect(compiled.followUpRetrieval).toBeUndefined();
    expect(compiled.rag).toEqual({ required: true });
  });

  it('explains denied combinations for the operator', () => {
    const value = defaultContextRetrievalAuthoring();
    value.followUp.enabled = true;
    value.followUp.collections = [];
    const denials = explainRetrievalDenials(value);
    expect(denials.some((d) => d.includes('no collections'))).toBe(true);

    value.followUp.collections = ['secret'];
    value.followUp.staleOverlayAllowed = true;
    const narrow = explainRetrievalDenials(value, NARROW_CEILINGS);
    expect(narrow.some((d) => d.includes('secret'))).toBe(true);
    expect(narrow.some((d) => d.toLowerCase().includes('stale overlay'))).toBe(true);
  });

  it('flags required-but-disabled follow-up retrieval', () => {
    const value = defaultContextRetrievalAuthoring();
    value.followUp.enabled = false;
    value.followUp.required = true;
    const denials = explainRetrievalDenials(value);
    expect(denials.some((d) => d.includes('required but disabled'))).toBe(true);
  });

  it('round-trips through parse/compile', () => {
    const value = defaultContextRetrievalAuthoring();
    value.initial.collections = ['docs'];
    value.followUp.enabled = true;
    value.followUp.collections = ['repo'];
    value.followUp.budgetPreset = 'generous';
    value.followUp.topK = 16;
    value.followUp.maxContextTokens = 16384;
    value.followUp.maxQueries = 24;
    value.followUp.latencyMs = 8000;
    const compiled = compileContextRetrievalParameters(value);
    const restored = parseContextRetrievalParameters(
      compiled as Record<string, unknown>,
    );
    expect(restored.initial.collections).toEqual(['docs']);
    expect(restored.followUp.enabled).toBe(true);
    expect(restored.followUp.collections).toEqual(['repo']);
    // preset detected from the numeric budget
    expect(restored.followUp.budgetPreset).toBe('generous');
  });
});
