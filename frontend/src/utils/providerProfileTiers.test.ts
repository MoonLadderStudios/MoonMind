import { describe, it, expect } from 'vitest';
import {
  buildProviderProfileTierPayload,
  computeTierRenumberingImpact,
  duplicateTierDraft,
  normalizeProviderProfileTiers,
  runtimeDefaultTierDraft,
  selectTierModel,
  tierDisplayEffort,
  tierDisplayModel,
  tierModeLabel,
  tierSummaryModel,
} from './providerProfileTiers';

describe('normalizeProviderProfileTiers', () => {
  it('preserves order and default Tier 2', () => {
    const result = normalizeProviderProfileTiers(
      [
        { label: 'Plan', model: 'gpt-5.5', effort: 'medium' },
        { label: 'Impl', model: 'gpt-5.5', effort: 'xhigh' },
        { label: 'Docs', model: 'gpt-5.3', effort: 'xhigh' },
      ],
      2,
    );
    expect(result.isRepair).toBe(false);
    expect(result.tiers).toHaveLength(3);
    expect(result.tiers[1]!.label).toBe('Impl');
    expect(result.defaultTierClientId).toBe(result.tiers[1]!.clientId);
    expect(result.invalidSavedDefaultIndex).toBeNull();
  });

  it('loads repair state for missing tier data', () => {
    const result = normalizeProviderProfileTiers(null, null);
    expect(result.isRepair).toBe(true);
    expect(result.tiers).toHaveLength(0);
  });

  it('loads repair state for empty tier data', () => {
    const result = normalizeProviderProfileTiers([], 1);
    expect(result.isRepair).toBe(true);
  });

  it('shows explicit runtime defaults for null values', () => {
    const result = normalizeProviderProfileTiers([{ label: null, model: null, effort: null }], 1);
    expect(tierDisplayModel(result.tiers[0]!.model)).toBe('Runtime default');
    expect(tierDisplayEffort(result.tiers[0]!.effort)).toBe('Runtime default');
    expect(result.tiers[0]!.model).toBeNull();
  });

  it('preserves parameters and annotations through normalization', () => {
    const result = normalizeProviderProfileTiers(
      [{ label: 'a', model: 'm', effort: 'e', parameters: { temp: 0 }, annotations: { cost: 'high' } }],
      1,
    );
    expect(result.tiers[0]!.parameters).toEqual({ temp: 0 });
    expect(result.tiers[0]!.annotations).toEqual({ cost: 'high' });
  });

  it('surfaces invalid saved default index rather than clamping', () => {
    const result = normalizeProviderProfileTiers([{ label: 'a', model: 'm', effort: 'e' }], 5);
    expect(result.invalidSavedDefaultIndex).toBe(5);
    expect(result.defaultTierClientId).toBe(result.tiers[0]!.clientId);
  });
});

describe('buildProviderProfileTierPayload', () => {
  it('card order becomes model_tiers order and selected clientId becomes default_model_tier', () => {
    const t1 = runtimeDefaultTierDraft();
    t1.label = 'Plan';
    t1.model = 'gpt-5.5';
    t1.effort = 'medium';
    const t2 = runtimeDefaultTierDraft();
    t2.label = 'Impl';
    t2.model = 'gpt-5.5';
    t2.effort = 'xhigh';
    const payload = buildProviderProfileTierPayload([t1, t2], t2.clientId);
    expect(payload.model_tiers[0]!.label).toBe('Plan');
    expect(payload.model_tiers[1]!.label).toBe('Impl');
    expect(payload.default_model_tier).toBe(2);
  });

  it('does not persist draft client IDs', () => {
    const t = runtimeDefaultTierDraft();
    const payload = buildProviderProfileTierPayload([t], t.clientId);
    expect((payload.model_tiers[0] as unknown as Record<string, unknown>).clientId).toBeUndefined();
    expect((payload as unknown as Record<string, unknown>).defaultTierClientId).toBeUndefined();
  });

  it('preserves advanced objects', () => {
    const t = runtimeDefaultTierDraft();
    t.parameters = { a: 1 };
    t.annotations = { b: 2 };
    const payload = buildProviderProfileTierPayload([t], t.clientId);
    expect(payload.model_tiers[0]!.parameters).toEqual({ a: 1 });
    expect(payload.model_tiers[0]!.annotations).toEqual({ b: 2 });
  });

  it('trims label whitespace', () => {
    const t = runtimeDefaultTierDraft();
    t.label = '  hello  ';
    const payload = buildProviderProfileTierPayload([t], t.clientId);
    expect(payload.model_tiers[0]!.label).toBe('hello');
  });

  it('carries tier policy only with no default model/effort mirror (MoonMind#4559)', () => {
    const t1 = runtimeDefaultTierDraft();
    t1.label = 'Plan';
    t1.model = 'gpt-5.5';
    t1.effort = 'medium';
    const t2 = runtimeDefaultTierDraft();
    t2.model = null;
    t2.effort = null;
    const payload = buildProviderProfileTierPayload([t1, t2], t1.clientId);
    expect(payload.default_model_tier).toBe(1);
    // Null tier values stay null so they resolve through the runtime default,
    // never through a profile-level compatibility mirror.
    expect(payload.model_tiers[1]!.model).toBeNull();
    expect(payload.model_tiers[1]!.effort).toBeNull();
    const keys = new Set([
      ...payload.model_tiers.flatMap((tier) => Object.keys(tier)),
      ...Object.keys(payload),
    ]);
    expect(keys.has('default_model')).toBe(false);
    expect(keys.has('default_effort')).toBe(false);
    expect(keys.has('clientId')).toBe(false);
    expect(keys.has('defaultTierClientId')).toBe(false);
  });
});

describe('add/duplicate/remove helpers', () => {
  it('duplicate appends copy and does not duplicate default state', () => {
    const source = runtimeDefaultTierDraft();
    source.label = 'Original';
    source.model = 'gpt-5.5';
    source.effort = 'high';
    source.parameters = { x: 1 };
    const copy = duplicateTierDraft(source);
    expect(copy.clientId).not.toBe(source.clientId);
    expect(copy.label).toBe('Original copy');
    expect(copy.model).toBe('gpt-5.5');
    expect(copy.parameters).toEqual({ x: 1 });
    expect(copy.clientId).not.toEqual(source.clientId);
  });

  it('computeTierRenumberingImpact previews all ordinal changes', () => {
    const tiers = [runtimeDefaultTierDraft(), runtimeDefaultTierDraft(), runtimeDefaultTierDraft()];
    tiers[0]!.label = 'A';
    tiers[1]!.label = 'B';
    tiers[2]!.label = 'C';
    const impacts = computeTierRenumberingImpact(tiers, 1);
    expect(impacts).toEqual([{ from: 3, to: 2, label: 'C' }]);
    const impactsMiddle = computeTierRenumberingImpact(tiers, 0);
    expect(impactsMiddle).toEqual([
      { from: 2, to: 1, label: 'B' },
      { from: 3, to: 2, label: 'C' },
    ]);
  });
});

describe('capability degradation', () => {
  it('tierDisplay preserves unknown values and shows Runtime default for null', () => {
    expect(tierDisplayModel(null)).toBe('Runtime default');
    expect(tierDisplayEffort('')).toBe('Runtime default');
    expect(tierDisplayModel('custom-model-xyz')).toBe('custom-model-xyz');
  });
});

describe('model family tiers', () => {
  const saved = [
    { label: 'Opus', family: 'opus', model_mode: 'automatic' as const, model: null, effort: 'medium', parameters: {}, annotations: {} },
    { label: 'Exact', model: 'gpt-custom', effort: null, parameters: {}, annotations: {} },
  ];

  it('round-trips family policy and keeps the legacy shape for other tiers', () => {
    const normalized = normalizeProviderProfileTiers(saved, 1);
    const payload = buildProviderProfileTierPayload(normalized.tiers, normalized.defaultTierClientId);
    expect(payload.model_tiers).toEqual(saved);
  });

  it('pins on a concrete choice and restores automatic on null', () => {
    const [opus] = normalizeProviderProfileTiers(saved, 1).tiers;
    const pinned = selectTierModel(opus!, 'claude-opus-5');
    expect(pinned).toMatchObject({ family: 'opus', modelMode: 'pinned', model: 'claude-opus-5' });
    const restored = selectTierModel(pinned, null);
    expect(restored).toMatchObject({ family: 'opus', modelMode: 'automatic', model: null });
    expect(duplicateTierDraft(pinned)).toMatchObject({ family: 'opus', modelMode: 'pinned', model: 'claude-opus-5' });
  });

  it('keeps exact and Runtime default meaning on tiers without a family', () => {
    const exact = normalizeProviderProfileTiers(saved, 1).tiers[1]!;
    expect(selectTierModel(exact, null)).toMatchObject({ family: null, modelMode: null, model: null });
  });

  it('describes saved tiers without calling automatic tiers Runtime default', () => {
    expect(tierSummaryModel(saved[0]!)).toBe('Latest automatically');
    expect(tierSummaryModel({ ...saved[0]!, model_mode: 'pinned', model: 'claude-opus-5' })).toBe('Pinned to claude-opus-5');
    expect(tierSummaryModel(saved[1]!)).toBe('gpt-custom');
  });

  it('labels the automatic resolution and its unverified state', () => {
    const [opus] = normalizeProviderProfileTiers(saved, 1).tiers;
    const capabilities = {
      families: [{ value: 'opus', label: 'Opus', resolved_model: 'claude-opus-5-5', status: 'unverified', source: 'cold_start_placeholder', reason: null }],
      tiers: [],
    } as never;
    expect(tierModeLabel(opus!, 0, capabilities)).toBe('Opus — Latest automatically · currently claude-opus-5-5 (not yet observed)');
    expect(tierModeLabel(opus!, 0, null)).toBe('Opus — Latest automatically');
  });
});
