import { JSX, useId } from 'react';

import {
  ContextRetrievalAuthoring,
  DEFAULT_RETRIEVAL_CEILINGS,
  FollowUpRetrievalAuthoring,
  RetrievalBudgetPreset,
  RetrievalCeilings,
  applyBudgetPreset,
  explainRetrievalDenials,
} from '../lib/contextRetrievalAuthoring';

interface ContextRetrievalControlsProps {
  value: ContextRetrievalAuthoring;
  onChange: (next: ContextRetrievalAuthoring) => void;
  ceilings?: RetrievalCeilings;
  /** Optional heading/context copy tailored to the hosting surface. */
  description?: string;
  disabled?: boolean;
  /**
   * When false, hide the automatic initial-injection collection controls
   * (surfaces that only govern follow-up retrieval, e.g. a branch turn).
   */
  showInitialControls?: boolean;
  idPrefix?: string;
}

const BUDGET_PRESET_OPTIONS: Array<{ value: RetrievalBudgetPreset; label: string }> = [
  { value: 'conservative', label: 'Conservative' },
  { value: 'balanced', label: 'Balanced' },
  { value: 'generous', label: 'Generous' },
  { value: 'custom', label: 'Custom' },
];

/**
 * Coherent, policy-bounded RAG authoring controls shared by every MoonMind
 * authoring surface (GitHub issue MoonLadderStudios/MoonMind#3514, item 6).
 *
 * The controls only NARROW within the policy ceilings; the visible ceilings and
 * denial notices make that boundary explicit rather than failing silently.
 */
export function ContextRetrievalControls({
  value,
  onChange,
  ceilings = DEFAULT_RETRIEVAL_CEILINGS,
  description,
  disabled = false,
  showInitialControls = true,
  idPrefix,
}: ContextRetrievalControlsProps): JSX.Element {
  const generatedId = useId();
  const prefix = idPrefix ?? generatedId;
  const denials = explainRetrievalDenials(value, ceilings);
  const followUp = value.followUp;

  const setFollowUp = (patch: Partial<FollowUpRetrievalAuthoring>): void => {
    onChange({ ...value, followUp: { ...followUp, ...patch } });
  };

  const toggleCollection = (
    scope: 'initial' | 'followUp',
    collection: string,
  ): void => {
    if (scope === 'initial') {
      const has = value.initial.collections.includes(collection);
      const collections = has
        ? value.initial.collections.filter((item) => item !== collection)
        : [...value.initial.collections, collection];
      onChange({ ...value, initial: { ...value.initial, collections } });
      return;
    }
    const has = followUp.collections.includes(collection);
    const collections = has
      ? followUp.collections.filter((item) => item !== collection)
      : [...followUp.collections, collection];
    setFollowUp({ collections });
  };

  const onPresetChange = (preset: RetrievalBudgetPreset): void => {
    onChange({ ...value, followUp: applyBudgetPreset(followUp, preset) });
  };

  const onCustomNumber = (
    field: 'topK' | 'maxContextTokens' | 'maxQueries' | 'latencyMs' | 'maxLifetimeSeconds',
    raw: string,
  ): void => {
    const parsed = Number(raw);
    setFollowUp({
      budgetPreset: 'custom',
      [field]: Number.isFinite(parsed) ? parsed : followUp[field],
    } as Partial<FollowUpRetrievalAuthoring>);
  };

  const budgetsDisabled = disabled || !followUp.enabled;

  return (
    <div className="context-retrieval-controls stack" data-testid="context-retrieval-controls">
      {description ? <p className="small">{description}</p> : null}

      {showInitialControls ? (
        <fieldset className="context-retrieval-section" disabled={disabled}>
          <legend>Initial context injection</legend>
          <p className="small">
            The initial ContextPack is injected automatically. Choose which
            collections it may draw from and whether a stale overlay is allowed.
          </p>
          <CollectionChips
            name={`${prefix}-initial-collections`}
            allowed={ceilings.collections}
            selected={value.initial.collections}
            onToggle={(collection) => toggleCollection('initial', collection)}
            disabled={disabled}
          />
          <label className="checkbox">
            <input
              type="checkbox"
              checked={value.initial.allowStale}
              disabled={disabled}
              onChange={(event) =>
                onChange({
                  ...value,
                  initial: { ...value.initial, allowStale: event.target.checked },
                })
              }
            />
            Allow a stale overlay for initial injection
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={value.initial.required}
              disabled={disabled}
              onChange={(event) =>
                onChange({
                  ...value,
                  initial: { ...value.initial, required: event.target.checked },
                })
              }
            />
            Require initial context (fail the step if unavailable)
          </label>
        </fieldset>
      ) : null}

      <fieldset className="context-retrieval-section" disabled={disabled}>
        <legend>In-session follow-up retrieval</legend>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={followUp.enabled}
            disabled={disabled}
            onChange={(event) => setFollowUp({ enabled: event.target.checked })}
          />
          Allow the session to request additional context during the run
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={followUp.required}
            disabled={budgetsDisabled}
            onChange={(event) => setFollowUp({ required: event.target.checked })}
          />
          Follow-up retrieval is required (not optional)
        </label>

        <div className="context-retrieval-field">
          <span className="context-retrieval-label">Collections the session may query</span>
          <CollectionChips
            name={`${prefix}-followup-collections`}
            allowed={ceilings.collections}
            selected={followUp.collections}
            onToggle={(collection) => toggleCollection('followUp', collection)}
            disabled={budgetsDisabled}
          />
        </div>

        <label className="context-retrieval-field">
          <span className="context-retrieval-label">Budget preset</span>
          <select
            value={followUp.budgetPreset}
            disabled={budgetsDisabled}
            onChange={(event) => onPresetChange(event.target.value as RetrievalBudgetPreset)}
          >
            {BUDGET_PRESET_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <div className="grid-2">
          <NumberField
            label="top_k"
            value={followUp.topK}
            ceilingMax={ceilings.topK.max}
            min={ceilings.topK.min}
            disabled={budgetsDisabled}
            onChange={(raw) => onCustomNumber('topK', raw)}
          />
          <NumberField
            label="Max context tokens"
            value={followUp.maxContextTokens}
            ceilingMax={ceilings.maxContextTokens.max}
            min={ceilings.maxContextTokens.min}
            disabled={budgetsDisabled}
            onChange={(raw) => onCustomNumber('maxContextTokens', raw)}
          />
          <NumberField
            label="Max queries per run"
            value={followUp.maxQueries}
            ceilingMax={ceilings.maxQueries.max}
            min={ceilings.maxQueries.min}
            disabled={budgetsDisabled}
            onChange={(raw) => onCustomNumber('maxQueries', raw)}
          />
          <NumberField
            label="Latency budget (ms)"
            value={followUp.latencyMs}
            ceilingMax={ceilings.latencyMs.max}
            min={ceilings.latencyMs.min}
            disabled={budgetsDisabled}
            onChange={(raw) => onCustomNumber('latencyMs', raw)}
          />
          <NumberField
            label="Capability lifetime (s)"
            value={followUp.maxLifetimeSeconds}
            ceilingMax={ceilings.maxLifetimeSeconds.max}
            min={ceilings.maxLifetimeSeconds.min}
            disabled={budgetsDisabled}
            onChange={(raw) => onCustomNumber('maxLifetimeSeconds', raw)}
          />
        </div>

        <label className="context-retrieval-field">
          <span className="context-retrieval-label">Overlay policy</span>
          <select
            value={followUp.overlayPolicy}
            disabled={budgetsDisabled}
            onChange={(event) =>
              setFollowUp({ overlayPolicy: event.target.value === 'skip' ? 'skip' : 'include' })
            }
          >
            <option value="include">Include workspace overlay</option>
            <option value="skip">Skip overlay (indexed content only)</option>
          </select>
        </label>

        <label className="checkbox">
          <input
            type="checkbox"
            checked={followUp.staleOverlayAllowed}
            disabled={budgetsDisabled || !ceilings.allowStaleOverlay}
            onChange={(event) => setFollowUp({ staleOverlayAllowed: event.target.checked })}
          />
          Allow a stale overlay
          {!ceilings.allowStaleOverlay ? ' (blocked by policy)' : ''}
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={followUp.fallbackAllowed}
            disabled={budgetsDisabled || !ceilings.allowFallback}
            onChange={(event) => setFollowUp({ fallbackAllowed: event.target.checked })}
          />
          Allow local-search fallback
          {!ceilings.allowFallback ? ' (blocked by policy)' : ''}
        </label>
      </fieldset>

      <p className="small context-retrieval-ceilings">
        Policy ceilings — collections: {ceilings.collections.join(', ') || 'none'}; top_k ≤{' '}
        {ceilings.topK.max}; context tokens ≤ {ceilings.maxContextTokens.max}; queries ≤{' '}
        {ceilings.maxQueries.max}; latency ≤ {ceilings.latencyMs.max}ms; lifetime ≤{' '}
        {ceilings.maxLifetimeSeconds.max}s. Values above a ceiling are clamped server-side.
      </p>

      {denials.length > 0 ? (
        <div className="notice error context-retrieval-denials" role="note">
          <p className="small">The current selection will be adjusted or denied by policy:</p>
          <ul className="small">
            {denials.map((denial) => (
              <li key={denial}>{denial}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

interface CollectionChipsProps {
  name: string;
  allowed: readonly string[];
  selected: string[];
  onToggle: (collection: string) => void;
  disabled?: boolean;
}

function CollectionChips({
  name,
  allowed,
  selected,
  onToggle,
  disabled,
}: CollectionChipsProps): JSX.Element {
  if (allowed.length === 0) {
    return <p className="small">No collections are available in this deployment.</p>;
  }
  return (
    <div className="context-retrieval-collections">
      {allowed.map((collection) => (
        <label key={collection} className="checkbox context-retrieval-collection">
          <input
            type="checkbox"
            name={name}
            value={collection}
            checked={selected.includes(collection)}
            disabled={disabled}
            onChange={() => onToggle(collection)}
          />
          {collection}
        </label>
      ))}
    </div>
  );
}

interface NumberFieldProps {
  label: string;
  value: number;
  min: number;
  ceilingMax: number;
  disabled?: boolean;
  onChange: (raw: string) => void;
}

function NumberField({
  label,
  value,
  min,
  ceilingMax,
  disabled,
  onChange,
}: NumberFieldProps): JSX.Element {
  return (
    <label className="context-retrieval-field">
      <span className="context-retrieval-label">
        {label} <span className="context-retrieval-ceiling-hint">(≤ {ceilingMax})</span>
      </span>
      <input
        type="number"
        min={min}
        max={ceilingMax}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}
