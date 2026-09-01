import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ProviderProfileTierSection } from './ProviderProfileTierSection';
import { normalizeProviderProfileTiers, createRuntimeDefaultTier } from '../../lib/providerProfileTierDraft';
import type { ProviderProfileTierCapabilities } from '../../lib/providerProfileTierCapabilities';

function mockCapabilities(): ProviderProfileTierCapabilities {
  return {
    version: 'test-v1',
    profile_id: 'p1',
    runtime_id: 'codex_cli',
    provider_id: 'openai',
    evidence: { source: 'profile_catalog_evidence', credential_generation: 1, image_ref: null, observed_at: null, stale: false },
    tier_constraints: { min_count: 1, max_count: null },
    model: {
      runtime_default: 'gpt-5.5',
      allow_custom: true,
      options: [
        { value: 'gpt-5.5', label: 'GPT-5.5', status: 'available', recommended: true },
        { value: 'gpt-5.3', label: 'GPT-5.3', status: 'available' },
      ],
    },
    effort: {
      supported: true,
      runtime_default: 'medium',
      allow_custom: false,
      application: 'native',
      options: [
        { value: 'low', label: 'Low', status: 'available' },
        { value: 'medium', label: 'Medium', status: 'available' },
        { value: 'high', label: 'High', status: 'available' },
        { value: 'xhigh', label: 'Extra high', status: 'available' },
      ],
    },
    diagnostics: [],
  };
}

describe('ProviderProfileTierSection', () => {
  it('renders ordered tier fieldsets with number, label, model, effort, default', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [
        { label: 'Plan', model: 'gpt-5.5', effort: 'medium' },
        { label: 'Implement', model: 'gpt-5.5', effort: 'xhigh' },
      ],
      default_model_tier: 2,
    });
    const setDraft = vi.fn();
    render(<ProviderProfileTierSection draft={draft!} setDraft={setDraft} capabilities={mockCapabilities()} capabilitiesLoading={false} capabilitiesError={null} />);
    expect(screen.getByText('Tier 1')).toBeTruthy();
    expect(screen.getByText('Tier 2')).toBeTruthy();
    expect(screen.getByDisplayValue('Plan')).toBeTruthy();
    expect(screen.getByDisplayValue('Implement')).toBeTruthy();
    // Default badge
    expect(screen.getByText('Default')).toBeTruthy();
    // Model selectors exist
    expect(screen.getByLabelText('Tier 1 model')).toBeTruthy();
    expect(screen.getByLabelText('Tier 2 effort level')).toBeTruthy();
    // Radio group
    expect(screen.getByLabelText('Use Tier 1 as default')).toBeTruthy();
    expect(screen.getByLabelText('Default tier')).toBeTruthy();
  });

  it('null values appear as explicit runtime defaults', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [{ label: null, model: null, effort: null }],
      default_model_tier: 1,
    });
    const setDraft = vi.fn();
    render(<ProviderProfileTierSection draft={draft!} setDraft={setDraft} capabilities={mockCapabilities()} capabilitiesLoading={false} capabilitiesError={null} />);
    // Model select should show Runtime default
    const modelSelect = screen.getByLabelText('Tier 1 model') as HTMLSelectElement;
    expect(modelSelect.value).toBe('__runtime_default__');
    const effortSelect = screen.getByLabelText('Tier 1 effort level') as HTMLSelectElement;
    expect(effortSelect.value).toBe('__runtime_default__');
    expect(screen.getByText(/Resolves to gpt-5.5 · medium/)).toBeTruthy();
  });

  it('users can append and duplicate tiers without renumbering existing', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [{ label: 'A', model: 'gpt-5.5', effort: 'medium' }],
      default_model_tier: 1,
    });
    let current = draft!;
    const setDraft = vi.fn((updater: (prev: typeof draft) => typeof draft) => {
      current = updater(current)!;
    });
    const { rerender } = render(<ProviderProfileTierSection draft={current} setDraft={setDraft} capabilities={mockCapabilities()} capabilitiesLoading={false} capabilitiesError={null} />);
    fireEvent.click(screen.getAllByRole('button', { name: '+ Add tier' })[0]);
    // After add, draft should have more tiers, original ordinal preserved
    expect(setDraft).toHaveBeenCalled();
    const updated = (setDraft.mock.calls[0][0] as (prev: typeof draft) => typeof draft)(current);
    expect(updated!.tiers.length).toBeGreaterThan(1);
    expect(updated?.tiers[0].label).toBe('A');
    // Duplicate
    setDraft.mockClear();
    rerender(<ProviderProfileTierSection draft={updated!} setDraft={setDraft} capabilities={mockCapabilities()} capabilitiesLoading={false} capabilitiesError={null} />);
    fireEvent.click(screen.getAllByRole('button', { name: 'Duplicate as new last tier' })[0]);
    const dupUpdated = (setDraft.mock.calls[0][0] as (prev: typeof draft) => typeof draft)(updated!);
    expect(dupUpdated!.tiers.length).toBeGreaterThan(updated!.tiers.length);
    expect(dupUpdated?.tiers[dupUpdated!.tiers.length - 1].model).toBe('gpt-5.5');
    // Duplicate does not duplicate default
    expect(dupUpdated?.defaultTierClientId).toBe(updated?.defaultTierClientId);
  });

  it('only tier cannot be removed', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [{ label: 'Only', model: 'gpt-5.5', effort: 'medium' }],
      default_model_tier: 1,
    });
    const setDraft = vi.fn();
    render(<ProviderProfileTierSection draft={draft!} setDraft={setDraft} capabilities={mockCapabilities()} capabilitiesLoading={false} capabilitiesError={null} />);
    const removeBtn = screen.getByLabelText('Remove Tier 1') as HTMLButtonElement;
    expect(removeBtn.disabled).toBe(true);
  });

  it('middle-tier removal previews all ordinal changes', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [
        { label: 'A', model: 'm1', effort: 'low' },
        { label: 'B', model: 'm2', effort: 'medium' },
        { label: 'C', model: 'm3', effort: 'high' },
      ],
      default_model_tier: 1,
    });
    const setDraft = vi.fn();
    render(<ProviderProfileTierSection draft={draft!} setDraft={setDraft} capabilities={mockCapabilities()} capabilitiesLoading={false} capabilitiesError={null} />);
    fireEvent.click(screen.getByLabelText('Remove Tier 2'));
    expect(screen.getByText('Remove Tier 2?')).toBeTruthy();
    expect(screen.getByText('Tier 3 -> becomes Tier 2')).toBeTruthy();
  });

  it('removing the default requires reviewed replacement default', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [
        { label: 'A', model: 'm1', effort: 'low' },
        { label: 'B', model: 'm2', effort: 'medium' },
      ],
      default_model_tier: 1,
    });
    const setDraft = vi.fn();
    render(<ProviderProfileTierSection draft={draft!} setDraft={setDraft} capabilities={mockCapabilities()} capabilitiesLoading={false} capabilitiesError={null} />);
    fireEvent.click(screen.getByLabelText('Remove Tier 1'));
    expect(screen.getByText('Removing the default requires a replacement default:')).toBeTruthy();
    // Should have radio for new default
    expect(screen.getByText(/Tier 1:/)).toBeTruthy();
  });

  it('shows repair state for missing tiers', () => {
    const setDraft = vi.fn();
    render(<ProviderProfileTierSection draft={null} setDraft={setDraft} capabilities={mockCapabilities()} capabilitiesLoading={false} capabilitiesError={null} repairNeeded={true} onRepair={vi.fn()} />);
    expect(screen.getByText('This profile has no model tiers and cannot be saved in this state.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Create runtime-default Tier 1' })).toBeTruthy();
  });

  it('read-only users see values not disabled controls', () => {
    const { draft } = normalizeProviderProfileTiers({
      model_tiers: [{ label: 'A', model: 'gpt-5.5', effort: 'medium' }],
      default_model_tier: 1,
    });
    const setDraft = vi.fn();
    render(<ProviderProfileTierSection draft={draft!} setDraft={setDraft} capabilities={mockCapabilities()} capabilitiesLoading={false} capabilitiesError={null} readOnly={true} />);
    expect(screen.getByText('Label:')).toBeTruthy();
    expect(screen.queryByLabelText('Tier 1 model')).toBeNull();
    expect(screen.getByText('gpt-5.5')).toBeTruthy();
  });
});
