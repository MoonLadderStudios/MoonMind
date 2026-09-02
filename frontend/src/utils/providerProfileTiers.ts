export interface ProviderModelEffortTier {
  label?: string | null;
  model?: string | null;
  effort?: string | null;
  parameters?: Record<string, unknown> | null;
  annotations?: Record<string, unknown> | null;
}

export interface ProviderProfileTierDraft {
  clientId: string;
  label: string;
  model: string | null;
  effort: string | null;
  parameters: Record<string, unknown>;
  annotations: Record<string, unknown>;
}

export interface TierNormalizationResult {
  tiers: ProviderProfileTierDraft[];
  defaultTierClientId: string | null;
  invalidSavedDefaultIndex: number | null;
  isRepair: boolean;
  repairReason: string | null;
}

let tierClientIdCounter = 0;

export function generateTierClientId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `tier-${crypto.randomUUID()}`;
  }
  tierClientIdCounter += 1;
  return `tier-${Date.now()}-${tierClientIdCounter}-${Math.random().toString(36).slice(2, 6)}`;
}

export function runtimeDefaultTierDraft(): ProviderProfileTierDraft {
  return {
    clientId: generateTierClientId(),
    label: '',
    model: null,
    effort: null,
    parameters: {},
    annotations: {},
  };
}

export function normalizeProviderProfileTiers(
  modelTiers: ProviderModelEffortTier[] | null | undefined,
  defaultModelTier: number | null | undefined,
): TierNormalizationResult {
  const raw = Array.isArray(modelTiers) ? modelTiers : null;
  if (!raw || raw.length === 0) {
    return {
      tiers: [],
      defaultTierClientId: null,
      invalidSavedDefaultIndex: defaultModelTier ?? null,
      isRepair: true,
      repairReason: raw === null ? 'missing_tier_data' : 'empty_tier_data',
    };
  }

  const tiers: ProviderProfileTierDraft[] = raw.map((tier) => ({
    clientId: generateTierClientId(),
    label: typeof tier.label === 'string' ? tier.label : tier.label == null ? '' : String(tier.label),
    model: tier.model ?? null,
    effort: tier.effort ?? null,
    parameters: tier.parameters && typeof tier.parameters === 'object' && !Array.isArray(tier.parameters) ? { ...tier.parameters } as Record<string, unknown> : {},
    annotations: tier.annotations && typeof tier.annotations === 'object' && !Array.isArray(tier.annotations) ? { ...tier.annotations } as Record<string, unknown> : {},
  }));

  let invalidSavedDefaultIndex: number | null = null;
  let defaultTierClientId: string | null;
  if (defaultModelTier != null) {
    if (typeof defaultModelTier !== 'number' || !Number.isInteger(defaultModelTier) || defaultModelTier < 1 || defaultModelTier > tiers.length) {
      invalidSavedDefaultIndex = defaultModelTier;
      defaultTierClientId = tiers[0]?.clientId ?? null;
    } else {
      defaultTierClientId = tiers[defaultModelTier - 1]!.clientId;
    }
  } else {
    defaultTierClientId = tiers[0]?.clientId ?? null;
    invalidSavedDefaultIndex = null;
  }

  return {
    tiers,
    defaultTierClientId,
    invalidSavedDefaultIndex,
    isRepair: false,
    repairReason: null,
  };
}

export function buildProviderProfileTierPayload(
  tiers: ProviderProfileTierDraft[],
  defaultTierClientId: string | null,
): { model_tiers: ProviderModelEffortTier[]; default_model_tier: number } {
  if (tiers.length === 0) {
    throw new Error('At least one tier is required.');
  }
  const defaultIndex = defaultTierClientId ? tiers.findIndex((t) => t.clientId === defaultTierClientId) : -1;
  const effectiveDefaultIndex = defaultIndex >= 0 ? defaultIndex : 0;
  const model_tiers: ProviderModelEffortTier[] = tiers.map((tier) => ({
    label: tier.label.trim() ? tier.label.trim() : null,
    model: tier.model,
    effort: tier.effort,
    parameters: tier.parameters,
    annotations: tier.annotations,
  }));
  return {
    model_tiers,
    default_model_tier: effectiveDefaultIndex + 1,
  };
}

export function computeTierRenumberingImpact(
  tiers: ProviderProfileTierDraft[],
  removeIndex: number,
): Array<{ from: number; to: number; label: string }> {
  const impacts: Array<{ from: number; to: number; label: string }> = [];
  for (let idx = removeIndex + 1; idx < tiers.length; idx += 1) {
    const tier = tiers[idx]!;
    impacts.push({
      from: idx + 1,
      to: idx,
      label: tier.label.trim() ? tier.label.trim() : `Tier ${idx + 1}`,
    });
  }
  return impacts;
}

export function tierDisplayModel(model: string | null | undefined): string {
  const trimmed = typeof model === 'string' ? model.trim() : '';
  return trimmed ? trimmed : 'Runtime default';
}

export function tierDisplayEffort(effort: string | null | undefined): string {
  const trimmed = typeof effort === 'string' ? effort.trim() : '';
  return trimmed ? trimmed : 'Runtime default';
}

export function duplicateTierDraft(source: ProviderProfileTierDraft): ProviderProfileTierDraft {
  return {
    clientId: generateTierClientId(),
    label: source.label ? `${source.label} copy` : '',
    model: source.model,
    effort: source.effort,
    parameters: { ...source.parameters },
    annotations: { ...source.annotations },
  };
}

export function mapTierApiErrorsToClientIds(
  errors: Array<{ path: string; message: string }>,
  tiers: ProviderProfileTierDraft[],
): Map<string, string> {
  const map = new Map<string, string>();
  for (const err of errors) {
    const match = err.path.match(/^model_tiers\.(\d+)\.(model|effort|label|parameters|annotations)$/);
    if (match) {
      const tierIndex = parseInt(match[1]!, 10);
      const field = match[2]!;
      const tier = tiers[tierIndex];
      if (tier) {
        map.set(`${tier.clientId}.${field}`, err.message);
      }
    } else if (err.path === 'default_model_tier') {
      map.set('default_model_tier', err.message);
    } else if (err.path === 'model_tiers') {
      map.set('model_tiers', err.message);
    }
  }
  return map;
}
