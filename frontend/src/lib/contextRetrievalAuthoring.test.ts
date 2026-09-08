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
  hasRetiredRetrievalParameters,
  parseContextRetrievalParameters,
  retrievalCeilingsFromRuntimeConfig,
} from './contextRetrievalAuthoring';

const NARROW_CEILINGS: RetrievalCeilings = {
  ...DEFAULT_RETRIEVAL_CEILINGS,
  collections: ['repo'],
  topK: { min: 1, max: 10, default: 8 },
  allowStaleOverlay: false,
  allowFallback: false,
};

describe('contextRetrievalAuthoring (retired #4105)', () => {
  it('uses deployment-provided collection and budget ceilings', () => {
    const ceilings = retrievalCeilingsFromRuntimeConfig({
      collections: ['knowledge'],
      maxQueries: 3,
      latencyMs: 1200,
    });
    expect(ceilings.collections).toEqual(['knowledge']);
    expect(ceilings.maxQueries.max).toBe(3);
    expect(ceilings.latencyMs.max).toBe(1200);
  });
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
    expect(DEFAULT_RETRIEVAL_CEILINGS.maxQueries.max).toBe(100);
  });

  it('compiles to no vector fields for new writes (retired)', () => {
    const value = defaultContextRetrievalAuthoring();
    value.initial.collections = ['repo', 'docs'];
    value.initial.allowStale = true;
    value.followUp.enabled = true;
    value.followUp.collections = ['repo'];
    value.followUp.topK = 6;
    expect(compileContextRetrievalParameters(value)).toEqual({});
    expect(hasAuthoredContextRetrieval(value)).toBe(false);
  });

  it('does not emit followUpRetrieval when disabled', () => {
    const value = defaultContextRetrievalAuthoring();
    value.initial.required = true;
    const compiled = compileContextRetrievalParameters(value);
    expect(compiled.followUpRetrieval).toBeUndefined();
    expect(compiled.rag).toBeUndefined();
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

  it('keeps historical payloads readable via parse', () => {
    const restored = parseContextRetrievalParameters({
      rag: { collections: ['docs'] },
      followUpRetrieval: { enabled: true, collections: ['repo'] },
    });
    expect(restored.initial.collections).toEqual(['docs']);
    expect(restored.followUp.enabled).toBe(true);
    expect(restored.followUp.collections).toEqual(['repo']);
  });

  it('detects retired parameters on historical payloads', () => {
    expect(hasRetiredRetrievalParameters(null)).toBe(false);
    expect(hasRetiredRetrievalParameters({})).toBe(false);
    expect(
      hasRetiredRetrievalParameters({ followUpRetrieval: { enabled: false } }),
    ).toBe(false);
    expect(
      hasRetiredRetrievalParameters({ rag: { collections: ['docs'] } }),
    ).toBe(true);
    expect(
      hasRetiredRetrievalParameters({
        followUpRetrieval: { enabled: true, collections: ['repo'] },
      }),
    ).toBe(true);
  });
});
