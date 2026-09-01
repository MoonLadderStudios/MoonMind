/** Provider Profile tier draft helpers – pure logic, no JSX. */

export type ProviderModelEffortTier = {
  label?: string | null;
  model?: string | null;
  effort?: string | null;
  parameters?: Record<string, unknown> | null;
  annotations?: Record<string, unknown> | null;
};

export type ProviderProfileTierDraft = {
  clientId: string;
  label: string;
  model: string | null;
  effort: string | null;
  parameters: Record<string, unknown>;
  annotations: Record<string, unknown>;
};

export type ProviderProfileTierEditorDraft = {
  tiers: ProviderProfileTierDraft[];
  defaultTierClientId: string;
  sourceProfileUpdatedAt: string | null;
  optionCatalogVersion: string | null;
  structuralChanges: TierStructuralChange[];
  /** Holds pending removal for undo of last non-default tier */
  lastRemoved?: {
    tier: ProviderProfileTierDraft;
    index: number;
    wasDefault: boolean;
  } | null;
};

export type TierStructuralChange =
  | { type: 'append'; clientId: string }
  | { type: 'remove'; clientId: string; previousIndex: number }
  | { type: 'restore'; clientId: string; index: number };

let _clientIdCounter = 0;
export function generateClientId(): string {
  _clientIdCounter += 1;
  return `tier-draft-${Date.now()}-${_clientIdCounter}-${Math.random().toString(36).slice(2, 6)}`;
}
export function resetClientIdCounterForTests() {
  _clientIdCounter = 0;
}

export function normalizeProviderProfileTiers(input: {
  model_tiers?: ProviderModelEffortTier[] | null;
  default_model_tier?: number | null;
  updatedAt?: string | null;
  optionCatalogVersion?: string | null;
}): {
  draft: ProviderProfileTierEditorDraft | null;
  repairNeeded: boolean;
  invalidDefaultIndex: boolean;
  repairReason: string | null;
} {
  const modelTiers = input.model_tiers;
  const defaultTier = input.default_model_tier;
  if (!Array.isArray(modelTiers) || modelTiers.length === 0) {
    return {
      draft: null,
      repairNeeded: true,
      invalidDefaultIndex: false,
      repairReason: 'Tier policy unavailable · needs repair',
    };
  }
  const tiers: ProviderProfileTierDraft[] = modelTiers.map((t) => ({
    clientId: generateClientId(),
    label: (t.label ?? '').toString(),
    model: t.model ?? null,
    effort: t.effort ?? null,
    parameters: (t.parameters as Record<string, unknown>) ?? {},
    annotations: (t.annotations as Record<string, unknown>) ?? {},
  }));
  let invalidDefaultIndex = false;
  let defaultClientId = tiers[0]?.clientId ?? '';
  if (typeof defaultTier === 'number' && defaultTier >= 1 && defaultTier <= tiers.length) {
    defaultClientId = tiers[defaultTier - 1].clientId;
  } else if (typeof defaultTier === 'number') {
    invalidDefaultIndex = true;
    // Keep Tier 1 selected for usability but flag invalid
    defaultClientId = tiers[0].clientId;
  }
  return {
    draft: {
      tiers,
      defaultTierClientId: defaultClientId,
      sourceProfileUpdatedAt: input.updatedAt ?? null,
      optionCatalogVersion: input.optionCatalogVersion ?? null,
      structuralChanges: [],
      lastRemoved: null,
    },
    repairNeeded: false,
    invalidDefaultIndex,
    repairReason: invalidDefaultIndex ? `Invalid saved default index ${defaultTier}` : null,
  };
}

export function buildProviderProfileTierPayload(draft: ProviderProfileTierEditorDraft): {
  model_tiers: ProviderModelEffortTier[];
  default_model_tier: number;
} {
  const model_tiers: ProviderModelEffortTier[] = draft.tiers.map((t) => ({
    label: t.label.trim() === '' ? null : t.label.trim(),
    model: t.model,
    effort: t.effort,
    parameters: t.parameters ?? {},
    annotations: t.annotations ?? {},
  }));
  const idx = draft.tiers.findIndex((t) => t.clientId === draft.defaultTierClientId);
  const default_model_tier = idx >= 0 ? idx + 1 : 1;
  return { model_tiers, default_model_tier };
}

export function validateProviderProfileTierDraft(draft: ProviderProfileTierEditorDraft): string | null {
  if (draft.tiers.length < 1) return 'At least one tier is required.';
  const hasDefault = draft.tiers.some((t) => t.clientId === draft.defaultTierClientId);
  if (!hasDefault) return 'Exactly one default tier must be selected.';
  return null;
}

export function computeTierRenumberingImpact(beforeCount: number, removedIndex: number): Array<{ from: number; to: number; label: string }> {
  // removedIndex is 0-based
  const impacts: Array<{ from: number; to: number; label: string }> = [];
  for (let i = removedIndex + 1; i < beforeCount; i += 1) {
    impacts.push({ from: i + 1, to: i, label: `Tier ${i + 1} -> becomes Tier ${i}` });
  }
  return impacts;
}

export function mapTierApiErrorsToClientIds(
  errorPaths: Record<string, string>,
  draft: ProviderProfileTierEditorDraft,
): Map<string, string> {
  // errorPaths: e.g. {"model_tiers.1.effort": "invalid effort"}
  const map = new Map<string, string>();
  for (const [path, message] of Object.entries(errorPaths)) {
    const match = path.match(/^model_tiers\.(\d+)\.(model|effort|label|parameters|annotations)$/);
    if (match) {
      const tierIndex = Number(match[1]);
      const field = match[2];
      const clientId = draft.tiers[tierIndex]?.clientId;
      if (clientId) map.set(`${clientId}.${field}`, message);
      continue;
    }
    if (path === 'model_tiers') {
      map.set('section', message);
      continue;
    }
    if (path === 'default_model_tier') {
      map.set('default', message);
      continue;
    }
    map.set(path, message);
  }
  return map;
}

export function createRuntimeDefaultTier(): ProviderProfileTierDraft {
  return {
    clientId: generateClientId(),
    label: '',
    model: null,
    effort: null,
    parameters: {},
    annotations: {},
  };
}

export function duplicateTierAsLast(tier: ProviderProfileTierDraft): ProviderProfileTierDraft {
  const label = tier.label ? `${tier.label} copy` : '';
  return {
    clientId: generateClientId(),
    label,
    model: tier.model,
    effort: tier.effort,
    parameters: tier.parameters ? JSON.parse(JSON.stringify(tier.parameters)) : {},
    annotations: tier.annotations ? JSON.parse(JSON.stringify(tier.annotations)) : {},
  };
}
