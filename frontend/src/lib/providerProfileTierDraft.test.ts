import { describe, it, expect } from 'vitest';
import {
  buildProviderProfileTierPayload,
  computeTierRenumberingImpact,
  createRuntimeDefaultTier,
  duplicateTierAsLast,
  mapTierApiErrorsToClientIds,
  normalizeProviderProfileTiers,
} from './providerProfileTierDraft';

describe('normalizeProviderProfileTiers', () => {
  it('normalizes canonical three-tier profile preserving order and default', () => {
    const { draft, repairNeeded, invalidDefaultIndex } = normalizeProviderProfileTiers({
      model_tiers: [
        { label: 'Plan', model: 'gpt-5.5', effort: 'medium' },
        { label: 'Implement', model: 'gpt-5.5', effort: 'xhigh' },
        { label: 'Docs', model: 'gpt-5.3', effort: 'xhigh' },
      ],
      default_model_tier: 2,
    });
    expect(repairNeeded).toBe(false);
    expect(invalidDefaultIndex).toBe(false);
    expect(draft?.tiers.length).toBe(3);
    expect(draft?.tiers[1].label).toBe('Implement');
    expect(draft?.defaultTierClientId).toBe(draft?.tiers[1].clientId);
  });

  it('treats missing or empty tiers as repair', () => {
    const miss = normalizeProviderProfileTiers({ model_tiers: null, default_model_tier: 1 });
    expect(miss.repairNeeded).toBe(true);
    expect(miss.draft).toBeNull();
    const empty = normalizeProviderProfileTiers({ model_tiers: [], default_model_tier: 1 });
    expect(empty.repairNeeded).toBe(true);
  });

  it('preserves null values as runtime default', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [{ label: null, model: null, effort: null }],
      default_model_tier: 1,
    });
    expect(draft?.tiers[0].model).toBeNull();
    expect(draft?.tiers[0].effort).toBeNull();
  });

  it('preserves parameters and annotations through normalize', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [{ label: 'Test', model: 'gpt-5.5', effort: 'medium', parameters: { temp: 0 }, annotations: { cost: 'high' } }],
      default_model_tier: 1,
    });
    expect(draft?.tiers[0].parameters).toEqual({ temp: 0 });
    expect(draft?.tiers[0].annotations).toEqual({ cost: 'high' });
  });

  it('surfaces invalid default index', () => {
    const { invalidDefaultIndex, repairReason } = normalizeProviderProfileTiers({
      model_tiers: [{ label: 'Only', model: 'gpt-5.5', effort: 'medium' }],
      default_model_tier: 5,
    });
    expect(invalidDefaultIndex).toBe(true);
    expect(repairReason).toContain('Invalid saved default index');
  });
});

describe('buildProviderProfileTierPayload', () => {
  it('card order becomes model_tiers order', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [
        { label: 'A', model: 'm1', effort: 'low' },
        { label: 'B', model: 'm2', effort: 'high' },
      ],
      default_model_tier: 1,
    });
    const payload = buildProviderProfileTierPayload(draft!);
    expect(payload.model_tiers[0].label).toBe('A');
    expect(payload.model_tiers[1].label).toBe('B');
  });

  it('selected clientId becomes one-based default', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [{ label: 'A', model: 'm1', effort: 'low' }, { label: 'B', model: 'm2', effort: 'high' }],
      default_model_tier: 1,
    });
    // change default to second tier
    const secondId = draft!.tiers[1].clientId;
    draft!.defaultTierClientId = secondId;
    const payload = buildProviderProfileTierPayload(draft!);
    expect(payload.default_model_tier).toBe(2);
  });

  it('draft clientIds do not enter payload', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [{ label: 'A', model: 'm1', effort: 'low' }],
      default_model_tier: 1,
    });
    const payload = buildProviderProfileTierPayload(draft!);
    expect((payload.model_tiers[0] as Record<string, unknown>)).not.toHaveProperty('clientId');
    expect(payload).not.toHaveProperty('clientId');
  });

  it('preserves advanced objects', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [{ label: 'A', model: 'm1', effort: 'low', parameters: { foo: 'bar' }, annotations: { ann: 1 } }],
      default_model_tier: 1,
    });
    const payload = buildProviderProfileTierPayload(draft!);
    expect(payload.model_tiers[0].parameters).toEqual({ foo: 'bar' });
    expect(payload.model_tiers[0].annotations).toEqual({ ann: 1 });
  });
});

describe('add/duplicate behavior', () => {
  it('duplicate appends copy and does not duplicate default', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [{ label: 'A', model: 'm1', effort: 'low' }],
      default_model_tier: 1,
    });
    const copy = duplicateTierAsLast(draft!.tiers[0]);
    expect(copy.label).toContain('copy');
    expect(copy.model).toBe('m1');
    expect(copy.clientId).not.toBe(draft!.tiers[0].clientId);
  });

  it('create runtime-default tier', () => {
    const tier = createRuntimeDefaultTier();
    expect(tier.model).toBeNull();
    expect(tier.effort).toBeNull();
    expect(tier.parameters).toEqual({});
    expect(tier.annotations).toEqual({});
  });
});

describe('computeTierRenumberingImpact', () => {
  it('previews all ordinal changes for middle-tier removal', () => {
    const impacts = computeTierRenumberingImpact(4, 1); // remove Tier 2 (index1)
    expect(impacts).toEqual([
      { from: 3, to: 2, label: 'Tier 3 -> becomes Tier 2' },
      { from: 4, to: 3, label: 'Tier 4 -> becomes Tier 3' },
    ]);
  });
});

describe('mapTierApiErrorsToClientIds', () => {
  it('maps backend path model_tiers.1.effort to correct card', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [{ label: 'A', model: 'm1', effort: 'low' }, { label: 'B', model: 'm2', effort: 'high' }],
      default_model_tier: 1,
    });
    const fieldMap = mapTierApiErrorsToClientIds({ 'model_tiers.1.effort': 'invalid effort' }, draft!);
    const secondId = draft!.tiers[1].clientId;
    expect(fieldMap.get(`${secondId}.effort`)).toBe('invalid effort');
  });
});
