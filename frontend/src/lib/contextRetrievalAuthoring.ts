// Shared authoring model for MoonMind context retrieval (RAG) controls.
//
// Covers GitHub issue MoonLadderStudios/MoonMind#3514 required work item 6:
// coherent, policy-bounded RAG controls reused across every authoring surface
// (workflow create/edit/rerun, recurring schedules, Omnigent agent profiles,
// checkpoint branch turn creation, and remediation authoring).
//
// The authored values are compiled into the run's `initial_parameters` as:
//   - `rag`               → initial ContextPack injection overrides (#3513)
//   - `followUpRetrieval`  → in-session follow-up retrieval policy (#3514)
// The server compiles these into an immutable budget snapshot; the retrieval
// gateway and deployment ceilings clamp any host request. These controls can
// only NARROW within policy — never broaden it — which the UI makes explicit.

export type OverlayPolicy = 'include' | 'skip';

export type RetrievalBudgetPreset =
  | 'conservative'
  | 'balanced'
  | 'generous'
  | 'custom';

export interface InitialRetrievalAuthoring {
  /** Collections searched for the automatic initial ContextPack. */
  collections: string[];
  /** Permit a stale overlay for the initial injection. */
  allowStale: boolean;
  /** Fail the step if the initial ContextPack cannot be produced. */
  required: boolean;
}

export interface FollowUpRetrievalAuthoring {
  /** Grant the active session an authorized follow-up retrieval capability. */
  enabled: boolean;
  /** Treat follow-up retrieval availability as required rather than optional. */
  required: boolean;
  /** Collections the session may query (subset of the allowed set). */
  collections: string[];
  budgetPreset: RetrievalBudgetPreset;
  topK: number;
  maxContextTokens: number;
  maxQueries: number;
  latencyMs: number;
  maxLifetimeSeconds: number;
  overlayPolicy: OverlayPolicy;
  staleOverlayAllowed: boolean;
  fallbackAllowed: boolean;
}

export interface ContextRetrievalAuthoring {
  initial: InitialRetrievalAuthoring;
  followUp: FollowUpRetrievalAuthoring;
}

export interface NumericCeiling {
  readonly min: number;
  readonly max: number;
  readonly default: number;
}

export interface RetrievalCeilings {
  /** Collections an operator may select from. */
  readonly collections: readonly string[];
  readonly topK: NumericCeiling;
  readonly maxContextTokens: NumericCeiling;
  readonly maxQueries: NumericCeiling;
  readonly latencyMs: NumericCeiling;
  readonly maxLifetimeSeconds: NumericCeiling;
  /** Whether requesting a stale overlay is permitted by policy. */
  readonly allowStaleOverlay: boolean;
  /** Whether requesting local-search fallback is permitted by policy. */
  readonly allowFallback: boolean;
}

// Contract ceilings mirror the backend request models
// (`BridgeRetrievalCapabilityIssue` / `RetrievalCapabilityIssue`). The server
// clamps further against deployment env ceilings; these are the widest values
// an authoring surface may request.
export const DEFAULT_RETRIEVAL_CEILINGS: RetrievalCeilings = {
  collections: ['repo', 'docs'],
  topK: { min: 1, max: 50, default: 8 },
  maxContextTokens: { min: 64, max: 65536, default: 8192 },
  maxQueries: { min: 1, max: 120, default: 12 },
  latencyMs: { min: 100, max: 30000, default: 5000 },
  maxLifetimeSeconds: { min: 30, max: 3600, default: 900 },
  allowStaleOverlay: true,
  allowFallback: true,
};

interface BudgetPresetValues {
  topK: number;
  maxContextTokens: number;
  maxQueries: number;
  latencyMs: number;
}

export const RETRIEVAL_BUDGET_PRESETS: Record<
  Exclude<RetrievalBudgetPreset, 'custom'>,
  BudgetPresetValues
> = {
  conservative: { topK: 4, maxContextTokens: 2048, maxQueries: 4, latencyMs: 3000 },
  balanced: { topK: 8, maxContextTokens: 8192, maxQueries: 12, latencyMs: 5000 },
  generous: { topK: 16, maxContextTokens: 16384, maxQueries: 24, latencyMs: 8000 },
};

function clampNumber(value: number, ceiling: NumericCeiling): number {
  if (!Number.isFinite(value)) {
    return ceiling.default;
  }
  return Math.min(Math.max(Math.round(value), ceiling.min), ceiling.max);
}

export function defaultFollowUpRetrievalAuthoring(): FollowUpRetrievalAuthoring {
  const preset = RETRIEVAL_BUDGET_PRESETS.balanced;
  return {
    enabled: false,
    required: false,
    collections: [],
    budgetPreset: 'balanced',
    topK: preset.topK,
    maxContextTokens: preset.maxContextTokens,
    maxQueries: preset.maxQueries,
    latencyMs: preset.latencyMs,
    maxLifetimeSeconds: DEFAULT_RETRIEVAL_CEILINGS.maxLifetimeSeconds.default,
    overlayPolicy: 'include',
    staleOverlayAllowed: false,
    fallbackAllowed: false,
  };
}

export function defaultContextRetrievalAuthoring(): ContextRetrievalAuthoring {
  return {
    initial: { collections: [], allowStale: false, required: false },
    followUp: defaultFollowUpRetrievalAuthoring(),
  };
}

/** Apply a budget preset, leaving `custom` untouched. */
export function applyBudgetPreset(
  value: FollowUpRetrievalAuthoring,
  preset: RetrievalBudgetPreset,
): FollowUpRetrievalAuthoring {
  if (preset === 'custom') {
    return { ...value, budgetPreset: 'custom' };
  }
  const values = RETRIEVAL_BUDGET_PRESETS[preset];
  return { ...value, budgetPreset: preset, ...values };
}

/** Clamp authored numeric budgets and collections within the policy ceilings. */
export function clampFollowUpRetrieval(
  value: FollowUpRetrievalAuthoring,
  ceilings: RetrievalCeilings = DEFAULT_RETRIEVAL_CEILINGS,
): FollowUpRetrievalAuthoring {
  const allowed = new Set(ceilings.collections);
  return {
    ...value,
    collections: uniqueStrings(value.collections).filter((c) => allowed.has(c)),
    topK: clampNumber(value.topK, ceilings.topK),
    maxContextTokens: clampNumber(value.maxContextTokens, ceilings.maxContextTokens),
    maxQueries: clampNumber(value.maxQueries, ceilings.maxQueries),
    latencyMs: clampNumber(value.latencyMs, ceilings.latencyMs),
    maxLifetimeSeconds: clampNumber(
      value.maxLifetimeSeconds,
      ceilings.maxLifetimeSeconds,
    ),
    staleOverlayAllowed: value.staleOverlayAllowed && ceilings.allowStaleOverlay,
    fallbackAllowed: value.fallbackAllowed && ceilings.allowFallback,
  };
}

/**
 * Explain authored combinations the policy will reject or silently narrow, so
 * the UI can surface actionable adaptation paths instead of a silent failure.
 */
export function explainRetrievalDenials(
  value: ContextRetrievalAuthoring,
  ceilings: RetrievalCeilings = DEFAULT_RETRIEVAL_CEILINGS,
): string[] {
  const denials: string[] = [];
  const allowed = new Set(ceilings.collections);

  for (const collection of value.initial.collections) {
    if (!allowed.has(collection)) {
      denials.push(
        `Initial collection “${collection}” is not in the allowed set (${
          [...allowed].join(', ') || 'none'
        }).`,
      );
    }
  }

  const followUp = value.followUp;
  if (!followUp.enabled) {
    if (followUp.required) {
      denials.push(
        'Follow-up retrieval is marked required but disabled; enable it or clear “required”.',
      );
    }
    return denials;
  }

  if (followUp.collections.length === 0) {
    denials.push(
      'Follow-up retrieval is enabled but no collections are selected; the session cannot query.',
    );
  }
  for (const collection of followUp.collections) {
    if (!allowed.has(collection)) {
      denials.push(
        `Follow-up collection “${collection}” is not in the allowed set (${
          [...allowed].join(', ') || 'none'
        }).`,
      );
    }
  }
  const numericChecks: Array<[string, number, NumericCeiling]> = [
    ['top_k', followUp.topK, ceilings.topK],
    ['max context tokens', followUp.maxContextTokens, ceilings.maxContextTokens],
    ['max queries', followUp.maxQueries, ceilings.maxQueries],
    ['latency budget (ms)', followUp.latencyMs, ceilings.latencyMs],
    ['capability lifetime (s)', followUp.maxLifetimeSeconds, ceilings.maxLifetimeSeconds],
  ];
  for (const [label, current, ceiling] of numericChecks) {
    if (current > ceiling.max) {
      denials.push(
        `Follow-up ${label} ${current} exceeds the policy ceiling ${ceiling.max}; it will be clamped.`,
      );
    }
  }
  if (followUp.staleOverlayAllowed && !ceilings.allowStaleOverlay) {
    denials.push(
      'Stale overlay is not permitted by policy; the request will use a fresh overlay.',
    );
  }
  if (followUp.fallbackAllowed && !ceilings.allowFallback) {
    denials.push(
      'Local-search fallback is not permitted by policy; a fallback request will be denied.',
    );
  }
  return denials;
}

/** True when the authored config carries anything worth persisting. */
export function hasAuthoredContextRetrieval(
  value: ContextRetrievalAuthoring,
): boolean {
  return (
    value.followUp.enabled ||
    value.initial.collections.length > 0 ||
    value.initial.allowStale ||
    value.initial.required
  );
}

export interface CompiledContextRetrievalParameters {
  rag?: Record<string, unknown>;
  followUpRetrieval?: Record<string, unknown>;
}

/**
 * Compile authored controls into the `initial_parameters` fragment consumed by
 * the run. Numeric values are clamped to the policy ceilings so the persisted
 * request never broadens policy; the server re-clamps regardless.
 */
export function compileContextRetrievalParameters(
  value: ContextRetrievalAuthoring,
  ceilings: RetrievalCeilings = DEFAULT_RETRIEVAL_CEILINGS,
): CompiledContextRetrievalParameters {
  const compiled: CompiledContextRetrievalParameters = {};
  const allowed = new Set(ceilings.collections);

  const initialCollections = uniqueStrings(value.initial.collections).filter((c) =>
    allowed.has(c),
  );
  if (initialCollections.length > 0 || value.initial.allowStale || value.initial.required) {
    compiled.rag = {
      ...(initialCollections.length > 0 ? { collections: initialCollections } : {}),
      ...(value.initial.allowStale ? { allowStale: true } : {}),
      ...(value.initial.required ? { required: true } : {}),
    };
  }

  if (value.followUp.enabled) {
    const followUp = clampFollowUpRetrieval(value.followUp, ceilings);
    compiled.followUpRetrieval = {
      enabled: true,
      required: followUp.required,
      collections: followUp.collections,
      topK: followUp.topK,
      maxContextTokens: followUp.maxContextTokens,
      maxQueries: followUp.maxQueries,
      latencyMs: followUp.latencyMs,
      maxLifetimeSeconds: followUp.maxLifetimeSeconds,
      overlayPolicy: followUp.overlayPolicy,
      staleOverlayAllowed: followUp.staleOverlayAllowed,
      fallbackAllowed: followUp.fallbackAllowed,
    };
  }

  return compiled;
}

/** Hydrate authoring state from a previously compiled `initial_parameters`. */
export function parseContextRetrievalParameters(
  parameters: Record<string, unknown> | null | undefined,
): ContextRetrievalAuthoring {
  const result = defaultContextRetrievalAuthoring();
  if (!parameters || typeof parameters !== 'object') {
    return result;
  }

  const rag = asRecord(parameters.rag);
  if (rag) {
    result.initial.collections = uniqueStrings(asStringArray(rag.collections));
    result.initial.allowStale = rag.allowStale === true;
    result.initial.required = rag.required === true;
  }

  const followUp = asRecord(parameters.followUpRetrieval);
  if (followUp) {
    const base = defaultFollowUpRetrievalAuthoring();
    result.followUp = {
      ...base,
      enabled: followUp.enabled === true,
      required: followUp.required === true,
      collections: uniqueStrings(asStringArray(followUp.collections)),
      topK: asNumber(followUp.topK, base.topK),
      maxContextTokens: asNumber(followUp.maxContextTokens, base.maxContextTokens),
      maxQueries: asNumber(followUp.maxQueries, base.maxQueries),
      latencyMs: asNumber(followUp.latencyMs, base.latencyMs),
      maxLifetimeSeconds: asNumber(followUp.maxLifetimeSeconds, base.maxLifetimeSeconds),
      overlayPolicy: followUp.overlayPolicy === 'skip' ? 'skip' : 'include',
      staleOverlayAllowed: followUp.staleOverlayAllowed === true,
      fallbackAllowed: followUp.fallbackAllowed === true,
      budgetPreset: 'custom',
    };
    result.followUp.budgetPreset = detectBudgetPreset(result.followUp);
  }

  return result;
}

function detectBudgetPreset(value: FollowUpRetrievalAuthoring): RetrievalBudgetPreset {
  for (const [name, preset] of Object.entries(RETRIEVAL_BUDGET_PRESETS)) {
    if (
      value.topK === preset.topK &&
      value.maxContextTokens === preset.maxContextTokens &&
      value.maxQueries === preset.maxQueries &&
      value.latencyMs === preset.latencyMs
    ) {
      return name as RetrievalBudgetPreset;
    }
  }
  return 'custom';
}

function uniqueStrings(values: readonly string[]): string[] {
  const seen = new Set<string>();
  const output: string[] = [];
  for (const value of values) {
    const trimmed = String(value ?? '').trim();
    if (trimmed && !seen.has(trimmed)) {
      seen.add(trimmed);
      output.push(trimmed);
    }
  }
  return output;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

function asNumber(value: unknown, fallback: number): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}
