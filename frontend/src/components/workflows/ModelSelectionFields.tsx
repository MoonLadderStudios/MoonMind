import { useId } from "react";
import { isTierEffortOptionAvailable, type ProviderProfileTierCapabilities } from "../../utils/providerProfileTiers";
import { displayModelSelection, editModelSelection, hasModelSelection, inheritModelSelection, isCustomSelection, replaceModelSelection } from "../../lib/modelSelection";
import type { ModelSelection, ModelSelectionProfile, ModelSelectionPreview } from "../../lib/modelSelection";

export function ModelSelectionFields({ value, inherited, profile, scope, onChange, loading, error, preview, capabilities, modelList, effortList }: {
  value: ModelSelection;
  inherited?: ModelSelection | undefined;
  profile?: ModelSelectionProfile | undefined;
  scope: string;
  onChange: (value: ModelSelection) => void;
  loading?: boolean;
  error?: string | null;
  preview?: ModelSelectionPreview | undefined;
  capabilities?: Pick<ProviderProfileTierCapabilities, "model" | "effort" | "diagnostics"> | undefined;
  modelList?: string;
  effortList?: string;
}) {
  const inputId = useId();
  const inheritedSelection = inherited != null && !hasModelSelection(value);
  const effective = inherited ? inheritModelSelection(inherited, value) : value;
  const display = displayModelSelection(effective, profile, preview);
  const partialSaved = hasModelSelection(value) && value.modelTier === undefined && !isCustomSelection(value);
  if (partialSaved && !display.unavailable) display.selected = "saved";
  const tiers = profile?.model_tiers || [];
  const selectedEffort = capabilities?.effort?.options?.find((option) => option.value === display.effort);
  const effortIncompatible = selectedEffort && !isTierEffortOptionAvailable(selectedEffort, display.model ?? capabilities?.model.runtime_default ?? null);
  const selectedModel = capabilities?.model?.options?.find((option) => option.value === display.model);
  const modelIncompatible = selectedModel?.status === "unavailable" || (display.model && capabilities?.model.allow_custom === false && !selectedModel);

  const source = inheritedSelection ? "Inherited from workflow" : !hasModelSelection(value) ? "Profile default" : partialSaved ? (inherited != null ? "Saved selection with workflow inheritance" : "Saved selection") : display.mixed ? `Tier ${display.requested ?? profile?.default_model_tier ?? "default"} with saved overrides` : display.selected === "custom" ? "Custom" : `Requested Tier ${display.requested}`;
  return <div className="model-selection" aria-label={`${scope} model selection`}>
    <div className="model-selection__fields">
      <label>Tier
        <select aria-label={`${scope} Tier`} name={scope === "Workflow" ? "modelTier" : undefined} value={display.selected}
          onChange={(event) => onChange(replaceModelSelection(value, event.target.value === "custom" ? { model: display.model, effort: display.effort } : { modelTier: Number(event.target.value) }))}>
          {display.selected === "pending" ? <option value="pending" disabled>{loading ? "Loading tiers…" : "Tiers unavailable"}</option> : null}
          {display.selected === "unavailable" ? <option value="unavailable" disabled>{`Requested Tier ${display.requested} (unavailable)`}</option> : null}
          {display.selected === "saved" ? <option value="saved" disabled>Saved selection</option> : null}
          {tiers.map((_tier, index) => <option key={index + 1} value={String(index + 1)}>{index + 1}</option>)}
          <option value="custom">Custom</option>
        </select>
      </label>
      <label>Model
        <input aria-label={`${scope} Model`} name={scope === "Workflow" ? "model" : undefined} list={capabilities ? `${inputId}-models` : modelList} value={display.model ?? ""} placeholder="Runtime default"
          onChange={(event) => onChange(editModelSelection(value, display, "model", event.target.value))} />
      </label>
      <label>Effort
        <input aria-label={`${scope} Effort`} name={scope === "Workflow" ? "effort" : undefined} list={capabilities ? `${inputId}-efforts` : effortList} value={display.effort ?? ""} placeholder="Runtime default"
          onChange={(event) => onChange(editModelSelection(value, display, "effort", event.target.value))} />
      </label>
    </div>
    {capabilities ? <>
      <datalist id={`${inputId}-models`}>{(capabilities.model?.options || []).filter((option) => option.status !== "unavailable").map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</datalist>
      <datalist id={`${inputId}-efforts`}>{(capabilities.effort?.options || []).filter((option) => isTierEffortOptionAvailable(option, display.model ?? capabilities.model.runtime_default)).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</datalist>
      {modelIncompatible ? <p className="small" role="alert">Model {display.model} is unavailable for this profile. The saved value is preserved.</p> : null}
      {effortIncompatible ? <p className="small" role="alert">Effort {display.effort} is unavailable for model {display.model ?? "Runtime default"}. The saved value is preserved.</p> : null}
      {["not_supported", "metadata_only"].includes(capabilities.effort?.application) ? <p className="small">Effort application: {capabilities.effort.application}. The value is retained.</p> : null}
      {(capabilities.diagnostics || []).map((diagnostic) => <p className="small" key={diagnostic.code}>{diagnostic.message}</p>)}
    </> : null}
    <p className="small" aria-live="polite">{source}{display.unavailable && !display.strictUnavailable && display.effective != null ? `. Tier ${display.requested} is not configured for this profile. Using Tier ${display.effective}.` : ""}
      {effective.tierFallback === "strict" ? ` This saved request requires Tier ${display.requested}. Choosing a tier or Custom replaces that requirement.${display.strictUnavailable ? " No configured tier satisfies this request." : ""}` : ""}
    </p>
    {!isCustomSelection(effective) && preview?.modelSource === "provider_profile_default" ? <p className="small">Model uses the saved profile default.</p> : null}
    {!isCustomSelection(effective) && preview?.effortSource === "provider_profile_default" ? <p className="small">Effort uses the saved profile default.</p> : null}
    {loading ? <p className="small" role="status">Loading profile tiers…</p> : error ? <p className="small" role="alert">{error}</p> : profile && tiers.length === 0 ? <p className="small" role="alert">This profile has an empty tier policy. Review Profile settings; your draft is preserved.</p> : null}
    {preview && (display.model === null || display.effort === null) ? <p className="small">{display.model === null && preview.model && preview.modelSource === "runtime_default" ? `Runtime default model: ${preview.model}. ` : ""}{display.effort === null && preview.effort && preview.effortSource === "runtime_default" ? `Runtime default effort: ${preview.effort}.` : ""}</p> : null}
    {inherited != null && hasModelSelection(value) ? <button type="button" onClick={() => onChange(value.parameters ? { parameters: value.parameters } : {})}>Use workflow settings</button> : null}
  </div>;
}
