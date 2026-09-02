import { describe, it, expect } from 'vitest';
import {
  buildProviderProfileTierPayload,
  computeTierRenumberingImpact,
  duplicateTierDraft,
  normalizeProviderProfileTiers,
  runtimeDefaultTierDraft,
  tierDisplayEffort,
  tierDisplayModel,
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
