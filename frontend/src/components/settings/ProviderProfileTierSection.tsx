import { useEffect, useRef, useState } from 'react';
import type { ProviderProfileTierCapabilities } from '../../lib/providerProfileTierCapabilities';
import {
  type ProviderProfileTierDraft,
  type ProviderProfileTierEditorDraft,
  computeTierRenumberingImpact,
  createRuntimeDefaultTier,
  duplicateTierAsLast,
} from '../../lib/providerProfileTierDraft';

type Props = {
  draft: ProviderProfileTierEditorDraft | null;
  setDraft: (updater: (prev: ProviderProfileTierEditorDraft | null) => ProviderProfileTierEditorDraft | null) => void;
  capabilities: ProviderProfileTierCapabilities | null;
  capabilitiesLoading: boolean;
  capabilitiesError: string | null;
  readOnly?: boolean;
  fieldErrors?: Map<string, string>;
  onRepair?: () => void;
  repairNeeded?: boolean;
  invalidDefaultIndex?: boolean;
  tierSectionRef?: React.RefObject<HTMLDivElement | null>;
};

export function ProviderProfileTierSection({
  draft,
  setDraft,
  capabilities,
  capabilitiesLoading,
  capabilitiesError,
  readOnly = false,
  fieldErrors,
  onRepair,
  repairNeeded = false,
  invalidDefaultIndex = false,
}: Props) {
  const [pendingRemoval, setPendingRemoval] = useState<null | {
    index: number;
    tier: ProviderProfileTierDraft;
    impacts: Array<{ from: number; to: number }>;
    requiresDefaultChoice: boolean;
    candidateDefaultClientId: string | null;
  }>(null);
  const [advancedOpen, setAdvancedOpen] = useState<Record<string, boolean>>({});
  const [liveMessage, setLiveMessage] = useState('');
  const newTierFocusRef = useRef<string | null>(null);

  const tierCount = draft?.tiers.length ?? 0;
  const defaultTierNumber = draft ? draft.tiers.findIndex((t) => t.clientId === draft.defaultTierClientId) + 1 : 0;

  // Focus new tier after append
  useEffect(() => {
    if (newTierFocusRef.current) {
      const id = newTierFocusRef.current;
      const el = document.getElementById(`tier-label-${id}`);
      if (el) (el as HTMLInputElement).focus();
      newTierFocusRef.current = null;
    }
  }, [tierCount]);

  // Handle middle/default confirmation
  const confirmRemoval = () => {
    if (!pendingRemoval || !draft) return;
    const idx = pendingRemoval.index;
    const wasDefault = draft.defaultTierClientId === pendingRemoval.tier.clientId;
    let newDefault = draft.defaultTierClientId;
    if (wasDefault) {
      if (!pendingRemoval.candidateDefaultClientId) return;
      newDefault = pendingRemoval.candidateDefaultClientId;
    }
    setDraft((prev) => {
      if (!prev) return prev;
      const newTiers = prev.tiers.filter((_, i) => i !== idx);
      // compute clientId mapping after removal for default
      let nextDefault = newDefault;
      if (wasDefault && !newTiers.some((t) => t.clientId === nextDefault)) {
        nextDefault = newTiers[0]?.clientId ?? '';
      }
      // if removed was not default but default remains, keep
      if (!wasDefault && !newTiers.some((t) => t.clientId === nextDefault)) {
        nextDefault = newTiers[0]?.clientId ?? '';
      }
      return {
        ...prev,
        tiers: newTiers,
        defaultTierClientId: nextDefault,
        structuralChanges: [...prev.structuralChanges, { type: 'remove', clientId: pendingRemoval.tier.clientId, previousIndex: idx }],
        lastRemoved: null,
      };
    });
    setLiveMessage(`Tier ${idx + 1} removed${pendingRemoval.impacts.length ? ', renumbering applied' : ''}`);
    setPendingRemoval(null);
  };

  const requestRemove = (index: number) => {
    if (!draft) return;
    const tier = draft.tiers[index];
    const isOnly = draft.tiers.length === 1;
    if (isOnly) return;
    const isLast = index === draft.tiers.length - 1;
    const isDefault = draft.defaultTierClientId === tier.clientId;
    // Safe removal of last non-default with Undo: do immediately without confirmation
    if (isLast && !isDefault) {
      const wasDefault = false;
      setDraft((prev) => {
        if (!prev) return prev;
        const newTiers = prev.tiers.filter((_, i) => i !== index);
        return {
          ...prev,
          tiers: newTiers,
          structuralChanges: [...prev.structuralChanges, { type: 'remove', clientId: tier.clientId, previousIndex: index }],
          lastRemoved: { tier, index, wasDefault },
        };
      });
      setLiveMessage(`Tier ${index + 1} removed`);
      return;
    }
    // Otherwise require confirmation
    const impacts = computeTierRenumberingImpact(draft.tiers.length, index);
    let requiresDefaultChoice = false;
    let candidateDefaultClientId: string | null = null;
    if (isDefault) {
      requiresDefaultChoice = true;
      // preselect nearest surviving tier: will occupy removed ordinal after removal, else previous
      if (index < draft.tiers.length - 1) {
        candidateDefaultClientId = draft.tiers[index + 1].clientId;
      } else if (draft.tiers.length > 1) {
        candidateDefaultClientId = draft.tiers[index - 1].clientId;
      }
    }
    setPendingRemoval({ index, tier, impacts, requiresDefaultChoice, candidateDefaultClientId });
  };

  const undoLastRemoval = () => {
    setDraft((prev) => {
      if (!prev || !prev.lastRemoved) return prev;
      const { tier, index } = prev.lastRemoved;
      const newTiers = [...prev.tiers];
      newTiers.splice(index, 0, tier);
      const defaultId = prev.defaultTierClientId;
      // if restored was default? but we only allow undo for non-default last
      return {
        ...prev,
        tiers: newTiers,
        defaultTierClientId: defaultId,
        structuralChanges: [...prev.structuralChanges, { type: 'restore', clientId: tier.clientId, index }],
        lastRemoved: null,
      };
    });
    setLiveMessage('Tier restored');
  };

  const addTier = () => {
    const newTier = createRuntimeDefaultTier();
    setDraft((prev) => {
      if (!prev) {
        return {
          tiers: [newTier],
          defaultTierClientId: newTier.clientId,
          sourceProfileUpdatedAt: null,
          optionCatalogVersion: capabilities?.version ?? null,
          structuralChanges: [{ type: 'append', clientId: newTier.clientId }],
          lastRemoved: null,
        };
      }
      // respect max tier constraint
      const max = capabilities?.tier_constraints?.max_count;
      if (max != null && prev.tiers.length >= max) return prev;
      return {
        ...prev,
        tiers: [...prev.tiers, newTier],
        structuralChanges: [...prev.structuralChanges, { type: 'append', clientId: newTier.clientId }],
      };
    });
    newTierFocusRef.current = newTier.clientId;
    setLiveMessage(`Tier ${tierCount + 1} added`);
  };

  const duplicateTier = (tier: ProviderProfileTierDraft) => {
    const copy = duplicateTierAsLast(tier);
    setDraft((prev) => {
      if (!prev) return prev;
      const max = capabilities?.tier_constraints?.max_count;
      if (max != null && prev.tiers.length >= max) return prev;
      return {
        ...prev,
        tiers: [...prev.tiers, copy],
        structuralChanges: [...prev.structuralChanges, { type: 'append', clientId: copy.clientId }],
      };
    });
    newTierFocusRef.current = copy.clientId;
    setLiveMessage(`Tier duplicated as Tier ${tierCount + 1}`);
  };

  const updateTier = (clientId: string, patch: Partial<ProviderProfileTierDraft>) => {
    setDraft((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        tiers: prev.tiers.map((t) => (t.clientId === clientId ? { ...t, ...patch } : t)),
      };
    });
  };

  const updateAdvancedJson = (clientId: string, field: 'parameters' | 'annotations', text: string) => {
    try {
      const parsed = text.trim() === '' ? {} : JSON.parse(text);
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error('Must be JSON object');
      setDraft((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          tiers: prev.tiers.map((t) => (t.clientId === clientId ? { ...t, [field]: parsed } : t)),
        };
      });
    } catch {
      // keep error locally via fieldErrors? For now store raw? Keep parsed error inline
      // We'll store as-is and let validation surface; don't update draft on parse error, but show error
    }
  };

  if (repairNeeded) {
    return (
      <fieldset className="rounded-2xl border border-amber-300 dark:border-amber-800 bg-amber-50/50 dark:bg-amber-950/20 p-5 space-y-3">
        <legend className="px-2 text-sm font-semibold text-amber-800 dark:text-amber-300">Model &amp; effort tiers</legend>
        <p className="text-sm text-slate-700 dark:text-slate-300">This profile has no model tiers and cannot be saved in this state.</p>
        {readOnly ? (
          <p className="text-xs text-slate-500 dark:text-slate-400">Read-only: tier repair requires write permission.</p>
        ) : (
          <button type="button" className="rounded-lg bg-slate-900 dark:bg-slate-100 px-4 py-2 text-sm font-semibold text-white dark:text-slate-900" onClick={onRepair}>
            Create runtime-default Tier 1
          </button>
        )}
      </fieldset>
    );
  }

  if (!draft) {
    return (
      <fieldset className="rounded-2xl border border-slate-200 dark:border-slate-700 p-5">
        <legend className="px-2 text-sm font-semibold text-slate-700 dark:text-slate-300">Model &amp; effort tiers</legend>
        <div className="text-sm text-slate-500 dark:text-slate-400">Loading tier policy…</div>
      </fieldset>
    );
  }

  const maxCount = capabilities?.tier_constraints?.max_count;
  const addDisabled = maxCount != null && draft.tiers.length >= maxCount;

  return (
    <fieldset className="rounded-2xl border border-violet-200/60 dark:border-violet-900/40 bg-slate-50/50 dark:bg-slate-800/30 p-5 space-y-4">
      <legend className="px-2 text-sm font-semibold text-slate-800 dark:text-slate-200">Model &amp; effort tiers</legend>
      <p className="text-xs text-slate-600 dark:text-slate-400">Map workflow tier requests to a model and effort for this profile. Future launches use the saved policy. Historical runs keep their record.</p>
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
        <div className="font-medium text-slate-700 dark:text-slate-300">
          {draft.tiers.length} {draft.tiers.length === 1 ? 'tier' : 'tiers'} · Default: Tier {defaultTierNumber}
          {invalidDefaultIndex ? <span className="ml-2 rounded bg-amber-100 dark:bg-amber-900/30 px-1.5 py-0.5 text-amber-700 dark:text-amber-300">Invalid saved default index</span> : null}
        </div>
        <div className="flex items-center gap-2">
          {capabilitiesLoading ? <span className="text-slate-500 dark:text-slate-400">Loading model choices…</span> : null}
          {capabilitiesError ? <span className="rounded bg-amber-100 dark:bg-amber-900/30 px-2 py-1 text-amber-700 dark:text-amber-300">Model choices could not be refreshed. Existing values are preserved. Server validation remains authoritative.</span> : null}
          {capabilities?.diagnostics?.map((d) => (
            <span key={d.code} className="rounded bg-sky-100 dark:bg-sky-900/30 px-2 py-1 text-sky-700 dark:text-sky-300">{d.message}</span>
          ))}
        </div>
        {!readOnly ? (
          <button type="button" className="rounded-lg bg-violet-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50" onClick={addTier} disabled={addDisabled} title={addDisabled ? `Maximum ${maxCount} tiers` : undefined}>
            + Add tier
          </button>
        ) : null}
      </div>

      <div aria-live="polite" className="sr-only">{liveMessage}</div>

      {draft.lastRemoved ? (
        <div className="flex items-center gap-3 rounded-lg border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 text-sm">
          <span>Tier {draft.lastRemoved.index + 1} removed.</span>
          <button type="button" className="rounded border border-slate-300 dark:border-slate-700 px-3 py-1 text-xs font-semibold" onClick={undoLastRemoval}>Undo</button>
        </div>
      ) : null}

      <ol className="space-y-4 list-none p-0">
        {draft.tiers.map((tier, index) => {
          const tierNumber = index + 1;
          const isDefault = tier.clientId === draft.defaultTierClientId;
          const modelError = fieldErrors?.get(`${tier.clientId}.model`);
          const effortError = fieldErrors?.get(`${tier.clientId}.effort`);
          const paramsError = fieldErrors?.get(`${tier.clientId}.parameters`);
          const annotationsError = fieldErrors?.get(`${tier.clientId}.annotations`);
          const effortSupported = capabilities?.effort?.supported ?? true;
          const modelOptions = capabilities?.model?.options ?? [];
          const effortOptions = capabilities?.effort?.options ?? [];
          const modelAllowCustom = capabilities?.model?.allow_custom ?? true;
          const effortAllowCustom = capabilities?.effort?.allow_custom ?? false;
          // Determine if existing model value is unknown
          const modelKnownValues = new Set(modelOptions.map((o) => o.value));
          const effortKnownValues = new Set(effortOptions.map((o) => o.value));
          const modelUnknown = tier.model != null && !modelKnownValues.has(tier.model);
          const effortUnknown = tier.effort != null && !effortKnownValues.has(tier.effort);
          const runtimeDefaultModel = capabilities?.model?.runtime_default;
          const runtimeDefaultEffort = capabilities?.effort?.runtime_default;

          return (
            <li key={tier.clientId}>
              <fieldset className={`rounded-xl border bg-white dark:bg-slate-900 p-4 space-y-3 ${isDefault ? 'border-violet-400 dark:border-violet-700 shadow-sm' : 'border-slate-200 dark:border-slate-700'}`}>
                <legend className="px-1 text-sm font-semibold text-slate-800 dark:text-slate-200">Tier {tierNumber} {isDefault ? <span className="ml-2 rounded bg-violet-100 dark:bg-violet-900/30 px-1.5 py-0.5 text-xs font-semibold text-violet-700 dark:text-violet-300">Default</span> : null}</legend>
                {readOnly ? (
                  <div className="space-y-2 text-sm">
                    <div><span className="font-medium">Label:</span> {tier.label || `Tier ${tierNumber}`}</div>
                    <div><span className="font-medium">Model:</span> <span className="font-mono">{tier.model ?? 'Runtime default'}</span> {tier.model == null && runtimeDefaultModel ? <span className="text-xs text-slate-500">({runtimeDefaultModel})</span> : null}</div>
                    <div><span className="font-medium">Effort:</span> <span className="font-mono">{tier.effort ?? 'Runtime default'}</span> {tier.effort == null && runtimeDefaultEffort ? <span className="text-xs text-slate-500">({runtimeDefaultEffort})</span> : null}</div>
                    <div className="text-xs text-slate-500 dark:text-slate-400">Resolves to {tier.model ?? runtimeDefaultModel ?? 'Runtime default'} · {tier.effort ?? runtimeDefaultEffort ?? (effortSupported ? 'Runtime default' : 'Not supported')}</div>
                  </div>
                ) : (
                  <>
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <label className="flex items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                        <input
                          type="radio"
                          name="defaultModelTier"
                          checked={isDefault}
                          onChange={() => setDraft((prev) => (prev ? { ...prev, defaultTierClientId: tier.clientId } : prev))}
                          aria-label={isDefault ? 'Default tier' : `Use Tier ${tierNumber} as default`}
                        />
                        {isDefault ? 'Default tier' : `Use Tier ${tierNumber} as default`}
                      </label>
                      <div className="flex gap-2">
                        <button type="button" className="rounded border border-slate-300 dark:border-slate-700 px-3 py-1 text-xs font-medium disabled:opacity-50" onClick={() => duplicateTier(tier)} disabled={addDisabled}>Duplicate as new last tier</button>
                        <button
                          type="button"
                          className="rounded border border-rose-300 dark:border-rose-700 px-3 py-1 text-xs font-medium text-rose-700 dark:text-rose-300 disabled:opacity-50"
                          onClick={() => requestRemove(index)}
                          disabled={draft.tiers.length === 1}
                          aria-label={`Remove Tier ${tierNumber}`}
                          title={draft.tiers.length === 1 ? 'At least one tier is required' : undefined}
                        >
                          Remove tier
                        </button>
                      </div>
                    </div>

                    <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                      <span>Label (optional)</span>
                      <input id={`tier-label-${tier.clientId}`} className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm" value={tier.label} onChange={(e) => updateTier(tier.clientId, { label: e.target.value })} placeholder={`Tier ${tierNumber}`} />
                    </label>

                    <div className="grid gap-4 md:grid-cols-2">
                      <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                        <span>Model</span>
                        {capabilitiesLoading ? (
                          <div className="rounded-xl border border-slate-300 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 px-3 py-2 text-sm text-slate-500">Loading…</div>
                        ) : (
                          <select
                            className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm"
                            value={tier.model ?? '__runtime_default__'}
                            onChange={(e) => {
                              const v = e.target.value;
                              if (v === '__runtime_default__') updateTier(tier.clientId, { model: null });
                              else if (v === '__custom__') {
                                const custom = window.prompt('Enter custom model value');
                                if (custom != null && custom.trim() !== '') updateTier(tier.clientId, { model: custom.trim() });
                              } else updateTier(tier.clientId, { model: v });
                            }}
                            aria-label={`Tier ${tierNumber} model`}
                          >
                            <option value="__runtime_default__">Runtime default{runtimeDefaultModel ? `: ${runtimeDefaultModel}` : ''}</option>
                            {modelOptions.map((opt) => (
                              <option key={opt.value} value={opt.value}>
                                {opt.label} {opt.value !== opt.label ? `(${opt.value})` : ''} {opt.status === 'deprecated' ? '· Deprecated' : ''} {opt.recommended ? '· Recommended' : ''}
                              </option>
                            ))}
                            {modelUnknown ? <option value={tier.model ?? ''}>Custom or unavailable: {tier.model} </option> : null}
                            {modelAllowCustom ? <option value="__custom__">Custom value…</option> : null}
                          </select>
                        )}
                        {modelUnknown ? <span className="text-xs text-amber-700 dark:text-amber-300">Custom or unavailable value preserved: {tier.model}</span> : null}
                        {modelError ? <span className="text-xs text-rose-600 dark:text-rose-400">{modelError}</span> : null}
                      </label>

                      <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                        <span>Effort level</span>
                        {!effortSupported ? (
                          <input disabled className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-slate-100 dark:bg-slate-800 px-3 py-2 text-sm" value="Not supported by this runtime" />
                        ) : capabilitiesLoading ? (
                          <div className="rounded-xl border border-slate-300 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 px-3 py-2 text-sm text-slate-500">Loading…</div>
                        ) : (
                          <select
                            className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm"
                            value={tier.effort ?? '__runtime_default__'}
                            onChange={(e) => {
                              const v = e.target.value;
                              if (v === '__runtime_default__') updateTier(tier.clientId, { effort: null });
                              else if (v === '__custom__') {
                                const custom = window.prompt('Enter custom effort value');
                                if (custom != null && custom.trim() !== '') updateTier(tier.clientId, { effort: custom.trim() });
                              } else updateTier(tier.clientId, { effort: v });
                            }}
                            aria-label={`Tier ${tierNumber} effort level`}
                          >
                            <option value="__runtime_default__">Runtime default{runtimeDefaultEffort ? `: ${runtimeDefaultEffort}` : ''}</option>
                            {effortOptions.map((opt) => (
                              <option key={opt.value} value={opt.value}>
                                {opt.label} {opt.status === 'deprecated' ? '· Deprecated' : ''} {opt.compatible_models ? `· compatible: ${opt.compatible_models.join(',')}` : ''}
                              </option>
                            ))}
                            {effortUnknown ? <option value={tier.effort ?? ''}>Custom or unavailable: {tier.effort}</option> : null}
                            {effortAllowCustom ? <option value="__custom__">Custom value…</option> : null}
                          </select>
                        )}
                        {effortUnknown ? <span className="text-xs text-amber-700 dark:text-amber-300">Custom or unavailable value preserved: {tier.effort}</span> : null}
                        {effortError ? <span className="text-xs text-rose-600 dark:text-rose-400">{effortError}</span> : null}
                      </label>
                    </div>

                    <div className="text-xs text-slate-500 dark:text-slate-400">
                      Resolves to {tier.model ?? runtimeDefaultModel ?? 'Runtime default'} · {tier.effort ?? runtimeDefaultEffort ?? (effortSupported ? 'Runtime default' : 'Not supported')}
                      {capabilities?.effort?.application === 'metadata_only' ? ' (Metadata only)' : null}
                      {capabilities?.effort?.application === 'unsupported' ? ' (Unsupported)' : null}
                    </div>

                    <details className="rounded-lg border border-slate-200 dark:border-slate-700 p-3">
                      <summary className="cursor-pointer text-sm font-medium text-slate-700 dark:text-slate-300" onClick={() => setAdvancedOpen((s) => ({ ...s, [tier.clientId]: !s[tier.clientId] }))}>Advanced tier options</summary>
                      <div className="mt-3 space-y-3">
                        <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                          <span>Parameters (JSON object)</span>
                          <textarea
                            rows={3}
                            className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 font-mono text-xs"
                            defaultValue={JSON.stringify(tier.parameters, null, 2)}
                            onBlur={(e) => updateAdvancedJson(tier.clientId, 'parameters', e.target.value)}
                            placeholder="{}"
                          />
                          {paramsError ? <span className="text-xs text-rose-600 dark:text-rose-400">{paramsError}</span> : null}
                        </label>
                        <label className="flex flex-col gap-1.5 text-sm font-medium text-slate-700 dark:text-slate-300">
                          <span>Annotations (JSON object)</span>
                          <textarea
                            rows={3}
                            className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 font-mono text-xs"
                            defaultValue={JSON.stringify(tier.annotations, null, 2)}
                            onBlur={(e) => updateAdvancedJson(tier.clientId, 'annotations', e.target.value)}
                            placeholder="{}"
                          />
                          {annotationsError ? <span className="text-xs text-rose-600 dark:text-rose-400">{annotationsError}</span> : null}
                        </label>
                      </div>
                    </details>
                  </>
                )}
              </fieldset>
            </li>
          );
        })}
      </ol>

      {!readOnly ? (
        <button type="button" className="w-full rounded-lg border border-violet-300 dark:border-violet-700 px-4 py-2 text-sm font-semibold text-violet-700 dark:text-violet-300 disabled:opacity-50" onClick={addTier} disabled={addDisabled}>
          + Add tier
        </button>
      ) : null}
      <p className="text-xs text-slate-500 dark:text-slate-400">Future launches use the saved policy. Historical runs keep their record.</p>

      {pendingRemoval ? (
        <div role="dialog" aria-modal="true" aria-labelledby="remove-tier-title" className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4">
          <div className="w-full max-w-lg rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-5 shadow-xl">
            <h3 id="remove-tier-title" className="text-base font-semibold text-slate-900 dark:text-white">Remove Tier {pendingRemoval.index + 1}?</h3>
            {pendingRemoval.impacts.length > 0 ? (
              <div className="mt-3 space-y-1 text-sm text-slate-700 dark:text-slate-300">
                <p>This changes future tier-number resolution for this profile:</p>
                {pendingRemoval.impacts.map((imp) => (
                  <div key={`${imp.from}-${imp.to}`} className="font-mono text-xs">Tier {imp.from} {"->"} becomes Tier {imp.to}</div>
                ))}
                <p className="text-xs text-slate-500 dark:text-slate-400">Existing historical runs do not change.</p>
              </div>
            ) : (
              <p className="mt-3 text-sm text-slate-600 dark:text-slate-400">This tier will be removed. Future launches that resolve this profile will use the updated mapping. Historical runs do not change.</p>
            )}
            {pendingRemoval.requiresDefaultChoice ? (
              <div className="mt-4 space-y-2">
                <p className="text-sm font-medium text-slate-700 dark:text-slate-300">Removing the default requires a replacement default:</p>
                <fieldset className="space-y-2">
                  <legend className="sr-only">New default tier</legend>
                  {draft!.tiers.filter((_, i) => i !== pendingRemoval.index).map((t, idx) => {
                    const originalIdx = draft!.tiers.findIndex((x) => x.clientId === t.clientId);
                    const tierNumAfter = draft!.tiers.filter((_, i) => i !== pendingRemoval.index).indexOf(t) + 1;
                    return (
                      <label key={t.clientId} className="flex items-center gap-2 text-sm">
                        <input
                          type="radio"
                          name="newDefaultTier"
                          checked={pendingRemoval.candidateDefaultClientId === t.clientId}
                          onChange={() => setPendingRemoval((p) => (p ? { ...p, candidateDefaultClientId: t.clientId } : p))}
                        />
                        Tier {tierNumAfter}: {t.label || `Tier ${originalIdx + 1}`} · {t.model ?? 'Runtime default'} / {t.effort ?? 'Runtime default'}
                      </label>
                    );
                  })}
                </fieldset>
              </div>
            ) : null}
            <div className="mt-5 flex justify-end gap-3">
              <button type="button" className="rounded-lg border border-slate-300 dark:border-slate-700 px-4 py-2 text-sm font-semibold" onClick={() => setPendingRemoval(null)}>Cancel</button>
              <button type="button" className="rounded-lg bg-rose-600 px-4 py-2 text-sm font-semibold text-white" onClick={confirmRemoval}>Remove and renumber</button>
            </div>
          </div>
        </div>
      ) : null}
    </fieldset>
  );
}
