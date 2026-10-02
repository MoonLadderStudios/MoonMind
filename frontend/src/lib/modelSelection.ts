/** Authored presence and display transitions for MoonLadderStudios/MoonMind#4636. */
export type ModelSelection = {
  modelTier?: number;
  model?: string | null;
  effort?: string | null;
  tierFallback?: "clamp" | "strict";
  tierPreview?: unknown;
  hardOverrideAudit?: unknown;
  parameters?: Record<string, unknown>;
};
export type ModelTier = { model?: string | null; effort?: string | null; label?: string | null };
export type ModelSelectionProfile = { model_tiers?: ModelTier[] | null; default_model_tier?: number | null };
const selectionKeys = ["modelTier", "model", "effort", "tierFallback", "tierPreview", "hardOverrideAudit", "parameters"] as const;
const owns = (value: object, key: string) => Object.prototype.hasOwnProperty.call(value, key);

export function readModelSelection(runtime: object | null | undefined): ModelSelection {
  return Object.fromEntries(selectionKeys.filter((key) => runtime && owns(runtime, key)).map((key) => [key, (runtime as Record<string, unknown>)[key]])) as ModelSelection;
}
export function hasModelSelection(value: ModelSelection): boolean {
  return ["modelTier", "model", "effort", "tierFallback"].some((key) => owns(value, key));
}
export function isCustomSelection(value: ModelSelection): boolean {
  return !owns(value, "modelTier") && owns(value, "model") && owns(value, "effort");
}
export function inheritModelSelection(parent: ModelSelection, child: ModelSelection): ModelSelection {
  if (owns(child, "modelTier") || isCustomSelection(child)) return { ...child };
  return { ...parent, ...child };
}
export function replaceModelSelection(previous: ModelSelection, next: ModelSelection): ModelSelection {
  // Parameters here are explicitly authored. Tier parameters are never saved in form state.
  return { ...(previous.parameters ? { parameters: previous.parameters } : {}), ...next };
}
export type ModelSelectionPreview = {
  model?: string | null;
  effort?: string | null;
  effectiveTier?: number | null;
  modelSource?: string | null;
  effortSource?: string | null;
};

export function displayModelSelection(value: ModelSelection, profile?: ModelSelectionProfile, preview?: ModelSelectionPreview) {
  const tiers = profile?.model_tiers || [];
  const custom = isCustomSelection(value);
  const requested = value.modelTier ?? profile?.default_model_tier;
  const unavailable = !custom && requested != null && !tiers[requested - 1];
  const strictUnavailable = unavailable && value.tierFallback === "strict";
  const effective = requested != null && tiers.length > 0 && !strictUnavailable
    ? Math.max(1, Math.min(requested, tiers.length)) : undefined;
  const tier = effective != null ? tiers[effective - 1] : undefined;
  const mixed = !custom && (owns(value, "model") || owns(value, "effort"));
  // A legacy model override bypasses the tier. Only a current, sourced
  // preview can supply its unauthored companion; historical diagnostics cannot.
  const modelOverride = !custom && typeof value.model === "string" && Boolean(value.model.trim());
  const savedEffort = preview?.effortSource && !["runtime_default", "none"].includes(preview.effortSource) ? preview.effort ?? null : null;
  const selected = custom ? "custom" : unavailable && owns(value, "modelTier") ? "unavailable" : mixed || value.tierFallback === "strict" ? "saved" : effective != null ? String(effective) : "pending";
  return {
    selected, requested, effective, unavailable, strictUnavailable, mixed,
    model: custom ? value.model ?? null : value.model ?? tier?.model ?? null,
    effort: custom ? value.effort ?? null : value.effort ?? (modelOverride ? savedEffort : tier?.effort ?? null),
  };
}
export function editModelSelection(value: ModelSelection, display: { model: string | null; effort: string | null }, field: "model" | "effort", input: string): ModelSelection {
  return replaceModelSelection(value, { model: display.model, effort: display.effort, [field]: input.trim() ? input : null });
}
