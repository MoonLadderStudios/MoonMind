export interface ProviderProfileTierCapabilities {
  version: string;
  profile_id: string | null;
  runtime_id: string;
  provider_id: string;
  evidence: {
    source: string;
    credential_generation: number | null;
    image_ref: string | null;
    observed_at: string | null;
    stale: boolean;
  };
  tier_constraints: { min_count: number; max_count: number | null };
  model: {
    runtime_default: string | null;
    allow_custom: boolean;
    options: Array<{ value: string; label: string; description: string | null; status: string; recommended?: boolean; family?: string | null }>;
  };
  effort: {
    supported: boolean;
    runtime_default: string | null;
    allow_custom: boolean;
    application: string;
    options: Array<{ value: string; label: string; description: string | null; status: string; compatible_models: string[] | null }>;
  };
  /** Families this route can follow, with their current resolution. */
  families?: ProviderProfileFamilyOption[];
  /** Per saved tier availability, separate from credential readiness. */
  tiers?: ProviderProfileTierResolution[];
  /** Backend-owned initial tier policy for a new profile on this route. */
  recommended_policy?: { model_tiers: ProviderModelEffortTier[]; default_model_tier: number } | null;
  diagnostics: Array<{ code: string; level: string; message: string }>;
}

export type TierModelMode = 'automatic' | 'pinned';

export interface ProviderProfileFamilyOption {
  value: string;
  label: string;
  resolved_model: string | null;
  status: string;
  source: string | null;
  reason: string | null;
}

export interface ProviderProfileTierResolution {
  index: number;
  family: string | null;
  model_mode: TierModelMode | null;
  model: string | null;
  status: string | null;
  source: string | null;
  reason: string | null;
}

export type ProviderProfileTierEffortOption = ProviderProfileTierCapabilities['effort']['options'][number];

export function isTierEffortOptionAvailable(option: ProviderProfileTierEffortOption, model: string | null): boolean {
  return option.status !== 'unavailable' && (
    !option.compatible_models || option.compatible_models.includes(model ?? '')
  );
}

// Offered only while backend capabilities are unavailable, ordered by
// increasing effort; the backend catalog owns the list once it loads.
export const FALLBACK_TIER_EFFORT_OPTIONS: ProviderProfileTierEffortOption[] = ['low', 'medium', 'high', 'xhigh', 'max'].map(
  (value) => ({ value, label: value, description: null, status: 'available', compatible_models: null }),
);

export interface ProviderModelEffortTier {
  label?: string | null;
  family?: string | null;
  model_mode?: TierModelMode | null;
  model?: string | null;
  effort?: string | null;
  parameters?: Record<string, unknown> | null;
  annotations?: Record<string, unknown> | null;
}

export interface ProviderProfileTierDraft {
  clientId: string;
  label: string;
  /** Machine-readable model family; null for an exact-model or Runtime default tier. */
  family: string | null;
  modelMode: TierModelMode | null;
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
    family: null,
    modelMode: null,
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
    family: tier.family ?? null,
    modelMode: tier.family ? tier.model_mode ?? null : null,
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
    // Family policy is serialized only for family tiers, so other tiers keep
    // their established shape. An automatic tier never carries a model.
    ...(tier.family ? { family: tier.family, model_mode: tier.modelMode } : {}),
    model: tier.family && tier.modelMode === 'automatic' ? null : tier.model,
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

/**
 * Apply a model choice to a tier. On a family tier, a concrete model pins the
 * tier (even when it equals today's resolution) and `null` restores automatic
 * family tracking. Other tiers keep their exact/Runtime default meaning.
 */
export function selectTierModel(tier: ProviderProfileTierDraft, model: string | null): ProviderProfileTierDraft {
  if (!tier.family) {
    return { ...tier, model };
  }
  return model
    ? { ...tier, modelMode: 'pinned', model }
    : { ...tier, modelMode: 'automatic', model: null };
}

/** The current route resolution for a family tier draft, if the backend reported one. */
export function tierFamilyResolution(
  tier: ProviderProfileTierDraft,
  index: number,
  capabilities: ProviderProfileTierCapabilities | null,
): { model: string | null; status: string | null; reason: string | null } | null {
  if (!tier.family || !capabilities) return null;
  const saved = capabilities.tiers?.find((entry) => entry.index === index + 1);
  if (saved && saved.family === tier.family && saved.model_mode === tier.modelMode && (tier.modelMode === 'automatic' || saved.model === tier.model)) {
    return { model: saved.model, status: saved.status, reason: saved.reason };
  }
  if (tier.modelMode !== 'automatic') return null;
  const family = capabilities.families?.find((entry) => entry.value === tier.family);
  return family ? { model: family.resolved_model, status: family.status, reason: family.reason } : null;
}

function familyDisplayName(tier: ProviderProfileTierDraft, capabilities: ProviderProfileTierCapabilities | null): string {
  const option = capabilities?.families?.find((entry) => entry.value === tier.family);
  if (option?.label) return option.label;
  const family = tier.family ?? '';
  return family ? family.charAt(0).toUpperCase() + family.slice(1) : '';
}

/** Accessible row summary such as "Opus — Latest automatically · currently claude-opus-5-5". */
export function tierModeLabel(
  tier: ProviderProfileTierDraft,
  index: number,
  capabilities: ProviderProfileTierCapabilities | null,
): string | null {
  if (!tier.family) return null;
  const name = familyDisplayName(tier, capabilities);
  const resolution = tierFamilyResolution(tier, index, capabilities);
  if (tier.modelMode === 'pinned') {
    const status = resolution?.status && !['available', 'pinned'].includes(resolution.status) ? ` · ${resolution.status}` : '';
    return `${name} — Pinned to ${tier.model ?? ''}${status}`;
  }
  if (!resolution) return `${name} — Latest automatically`;
  if (resolution.status === 'unavailable' || !resolution.model) {
    return `${name} — Latest automatically · unavailable for this profile`;
  }
  const unverified = resolution.status === 'unverified' ? ' (not yet observed)' : '';
  return `${name} — Latest automatically · currently ${resolution.model}${unverified}`;
}

/** Build tier drafts from a backend-supplied tier policy. */
export function tierDraftsFromPolicy(policy: { model_tiers: ProviderModelEffortTier[]; default_model_tier: number }): {
  tiers: ProviderProfileTierDraft[];
  defaultTierClientId: string | null;
} {
  const normalized = normalizeProviderProfileTiers(policy.model_tiers, policy.default_model_tier);
  return { tiers: normalized.tiers, defaultTierClientId: normalized.defaultTierClientId };
}

export function tierDisplayModel(model: string | null | undefined): string {
  const trimmed = typeof model === 'string' ? model.trim() : '';
  return trimmed ? trimmed : 'Runtime default';
}

/** Saved-tier model summary that never presents an automatic family tier as Runtime default. */
export function tierSummaryModel(tier: ProviderModelEffortTier): string {
  if (tier.family && tier.model_mode === 'automatic') return 'Latest automatically';
  if (tier.family && tier.model_mode === 'pinned') return `Pinned to ${tier.model ?? ''}`;
  return tierDisplayModel(tier.model);
}

export function tierDisplayEffort(effort: string | null | undefined): string {
  const trimmed = typeof effort === 'string' ? effort.trim() : '';
  return trimmed ? trimmed : 'Runtime default';
}

export function duplicateTierDraft(source: ProviderProfileTierDraft): ProviderProfileTierDraft {
  return {
    clientId: generateTierClientId(),
    label: source.label ? `${source.label} copy` : '',
    family: source.family,
    modelMode: source.modelMode,
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
