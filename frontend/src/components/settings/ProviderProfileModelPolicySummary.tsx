import { useState } from 'react';
import type { ProviderModelEffortTier } from './ProviderProfilesManager';

export function ProviderProfileModelPolicySummary({
  profileId,
  modelTiers,
  defaultModelTier,
  compact = false,
}: {
  profileId: string;
  modelTiers?: ProviderModelEffortTier[] | null;
  defaultModelTier?: number | null;
  compact?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const tiers = Array.isArray(modelTiers) ? modelTiers : null;
  if (!tiers || tiers.length === 0) {
    return (
      <div className="text-xs text-amber-700 dark:text-amber-300" aria-label={`${profileId} tier policy unavailable`}>
        Tier policy unavailable · needs repair
      </div>
    );
  }
  const count = tiers.length;
  const defTier = defaultModelTier ?? 1;
  const invalidDefault = defTier < 1 || defTier > count;
  const defLabel = invalidDefault ? `Invalid default Tier ${defTier}` : `Default: Tier ${defTier}`;

  const renderTierLine = (tier: ProviderModelEffortTier, idx: number) => {
    const tierNum = idx + 1;
    const label = tier.label?.trim() ? tier.label.trim() : `Tier ${tierNum}`;
    const model = tier.model?.trim() ? tier.model.trim() : 'Runtime default';
    const effort = tier.effort?.trim() ? tier.effort.trim() : 'Runtime default';
    const isDefault = tierNum === defTier && !invalidDefault;
    return (
      <div key={`${profileId}-tier-${tierNum}`} className="flex items-center gap-2 font-mono text-xs">
        <span className={isDefault ? 'font-semibold text-violet-700 dark:text-violet-300' : 'text-slate-600 dark:text-slate-300'}>
          Tier {tierNum}{isDefault ? ' default' : ''}
        </span>
        {' · '}
        <span className="text-slate-500 dark:text-slate-400">{label}</span>
        {' · '}
        <span className="text-slate-600 dark:text-slate-300">{model} · {effort}</span>
        {isDefault ? <span className="ml-2 rounded bg-violet-100 dark:bg-violet-900/30 px-1.5 py-0.5 text-[10px] font-semibold text-violet-700 dark:text-violet-300">Default</span> : null}
      </div>
    );
  };

  if (compact) {
    // narrow-screen card layout – same compact logic
    return (
      <div className="space-y-1" aria-label={`${profileId} model tier mapping`}>
        <div className="text-xs font-medium text-slate-700 dark:text-slate-300">
          {count} {count === 1 ? 'model tier' : 'model tiers'} · {defLabel}
          {invalidDefault ? <span className="ml-2 text-amber-700 dark:text-amber-300">needs repair</span> : null}
        </div>
        {!expanded ? (
          <>
            <div className="space-y-1">
              {tiers.slice(0, 2).map((t, i) => renderTierLine(t, i))}
            </div>
            {count > 2 ? <div className="text-xs text-slate-500 dark:text-slate-400">+{count - 2} more</div> : null}
            {count > 2 ? (
              <button
                type="button"
                className="text-xs font-medium text-violet-700 dark:text-violet-300 underline"
                onClick={() => setExpanded(true)}
                aria-expanded={expanded}
                aria-controls={`${profileId}-tier-details`}
              >
                Show tier mapping
              </button>
            ) : null}
          </>
        ) : (
          <>
            <div id={`${profileId}-tier-details`} className="space-y-1">
              {tiers.map((t, i) => renderTierLine(t, i))}
            </div>
            <button
              type="button"
              className="text-xs font-medium text-violet-700 dark:text-violet-300 underline"
              onClick={() => setExpanded(false)}
              aria-expanded={expanded}
            >
              Hide tier mapping
            </button>
          </>
        )}
      </div>
    );
  }

  // Desktop summary – up to 2 compact mappings in collapsed row, expandable full mapping
  return (
    <div className="space-y-1" aria-label={`${profileId} model tier mapping`}>
      <div className="text-xs font-medium text-slate-700 dark:text-slate-300">
        {count} {count === 1 ? 'tier' : 'tiers'} · {defLabel}
        {invalidDefault ? <span className="ml-2 rounded bg-amber-100 dark:bg-amber-900/30 px-1.5 py-0.5 text-[10px] text-amber-700 dark:text-amber-300">Invalid default index</span> : null}
      </div>
      {!expanded ? (
        <>
          <div className="space-y-1">
            {tiers.slice(0, 2).map((t, i) => renderTierLine(t, i))}
          </div>
          {count > 2 ? <div className="text-xs text-slate-500 dark:text-slate-400">+{count - 2} more</div> : null}
          {count > 0 ? (
            <button
              type="button"
              className="text-xs font-medium text-violet-700 dark:text-violet-300 underline"
              onClick={() => setExpanded(true)}
              aria-expanded={expanded}
              aria-controls={`${profileId}-tier-details-desktop`}
            >
              Show all tiers
            </button>
          ) : null}
        </>
      ) : (
        <>
          <details open className="space-y-1" id={`${profileId}-tier-details-desktop`}>
            <summary className="cursor-pointer text-xs font-medium text-slate-500 dark:text-slate-400">Tier mapping</summary>
            <div className="space-y-1 pt-1">
              {tiers.map((t, i) => renderTierLine(t, i))}
            </div>
          </details>
          <button
            type="button"
            className="text-xs font-medium text-violet-700 dark:text-violet-300 underline"
            onClick={() => setExpanded(false)}
            aria-expanded={expanded}
          >
            Hide
          </button>
        </>
      )}
    </div>
  );
}
