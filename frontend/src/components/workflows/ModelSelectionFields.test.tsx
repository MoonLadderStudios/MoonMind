import { useState } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { ModelSelectionFields } from './ModelSelectionFields';
import type { ModelSelection, ModelSelectionProfile } from '../../lib/modelSelection';

const profile = { default_model_tier: 2, model_tiers: [{ model: 'plan', effort: 'low' }, { model: 'implement', effort: 'high' }] };
function Form({ initial = {}, policy = profile, inherited }: { initial?: ModelSelection; policy?: ModelSelectionProfile; inherited?: ModelSelection | undefined }) {
  const [value, setValue] = useState(initial);
  return <><ModelSelectionFields scope="Workflow" value={value} profile={policy} inherited={inherited} onChange={setValue} /><output aria-label="payload">{JSON.stringify(value)}</output></>;
}
const payload = () => JSON.parse(screen.getByLabelText('payload').textContent || '{}');
afterEach(cleanup);
describe('#4636 shared production selector', () => {
  it('leaves default display and inherited previews unauthored', () => {
    render(<Form inherited={{ model: null, effort: 'max' }} />);
    expect((screen.getByLabelText('Workflow Tier') as HTMLSelectElement).value).toBe('custom');
    expect((screen.getByLabelText('Workflow Model') as HTMLInputElement).value).toBe('');
    expect(payload()).toEqual({});
    expect(screen.getByText('Inherited from workflow')).toBeTruthy();
  });
  it('selects Custom directly without changing either displayed value', () => {
    render(<Form />);
    fireEvent.change(screen.getByLabelText('Workflow Tier'), { target: { value: 'custom' } });
    expect(payload()).toEqual({ model: 'implement', effort: 'high' });
  });
  it.each(['Model', 'Effort'])('editing and clearing %s author the companion and nullable pair', (field) => {
    render(<Form />);
    fireEvent.change(screen.getByLabelText(`Workflow ${field}`), { target: { value: '' } });
    expect(payload()).toEqual(field === 'Model' ? { model: null, effort: 'high' } : { model: 'implement', effort: null });
    expect((screen.getByLabelText('Workflow Tier') as HTMLSelectElement).value).toBe('custom');
  });
  it('matching tier values stays Custom, then an explicit tier removes old metadata', () => {
    render(<Form initial={{ model: 'other', effort: 'high', tierPreview: { model: 'stale' }, parameters: { temperature: 0 } }} />);
    fireEvent.change(screen.getByLabelText('Workflow Model'), { target: { value: 'implement' } });
    expect((screen.getByLabelText('Workflow Tier') as HTMLSelectElement).value).toBe('custom');
    fireEvent.change(screen.getByLabelText('Workflow Tier'), { target: { value: '1' } });
    expect(payload()).toEqual({ modelTier: 1, parameters: { temperature: 0 } });
    expect((screen.getByLabelText('Workflow Effort') as HTMLInputElement).value).toBe('low');
  });
  it('direct Custom retains runtime-default nulls', () => {
    render(<Form policy={{ default_model_tier: 1, model_tiers: [{ model: null, effort: null }] }} />);
    fireEvent.change(screen.getByLabelText('Workflow Tier'), { target: { value: 'custom' } });
    expect(payload()).toEqual({ model: null, effort: null });
  });
  it('preserves saved mixed intent and lets the effective tier replace an unavailable ordinal', () => {
    const initial = { modelTier: 7, effort: 'max' };
    render(<Form initial={initial} />);
    expect(payload()).toEqual(initial);
    expect(screen.getByRole('option', { name: 'Requested Tier 7 (unavailable)' }).hasAttribute('disabled')).toBe(true);
    fireEvent.change(screen.getByLabelText('Workflow Tier'), { target: { value: '2' } });
    expect(payload()).toEqual({ modelTier: 2 });
  });
  it('retains strict intent without claiming a lower effective tier', () => {
    render(<Form initial={{ modelTier: 7, tierFallback: 'strict' }} />);
    expect(screen.getByText(/No configured tier satisfies/)).toBeTruthy();
    expect(screen.queryByText(/Using Tier/)).toBeNull();
    expect(payload()).toEqual({ modelTier: 7, tierFallback: 'strict' });
    fireEvent.change(screen.getByLabelText('Workflow Tier'), { target: { value: '1' } });
    expect(payload()).toEqual({ modelTier: 1 });
  });
  it('preserves missing/null fields in Saved selection through unrelated renders', () => {
    const view = render(<Form initial={{ modelTier: 2, effort: null }} />);
    expect((screen.getByLabelText('Workflow Tier') as HTMLSelectElement).value).toBe('saved');
    view.rerender(<Form initial={{ modelTier: 2, effort: null }} policy={{ ...profile, default_model_tier: 1 }} />);
    expect(payload()).toEqual({ modelTier: 2, effort: null });
  });
  it('keeps input DOM, focus and selection when editing enters Custom and previews refresh', () => {
    const view = render(<Form />);
    const input = screen.getByLabelText('Workflow Model') as HTMLInputElement;
    input.focus(); input.setSelectionRange(2, 5);
    view.rerender(<Form policy={{ ...profile }} />);
    expect(document.activeElement).toBe(input);
    expect(input.selectionStart).toBe(2);
    fireEvent.change(input, { target: { value: 'typed-model' } });
    view.rerender(<Form policy={{ default_model_tier: 1, model_tiers: [{ model: 'late', effort: 'low' }] }} />);
    expect(screen.getByLabelText('Workflow Model')).toBe(input);
    expect(document.activeElement).toBe(input);
    expect(input.value).toBe('typed-model');
    expect(payload()).toEqual({ model: 'typed-model', effort: 'high' });
  });
  it('uses workflow settings to restore omission without deleting parameters', () => {
    render(<Form initial={{ modelTier: 1, parameters: { output_format: 'json' } }} inherited={{ modelTier: 2 }} />);
    fireEvent.click(screen.getByRole('button', { name: 'Use workflow settings' }));
    expect(payload()).toEqual({ parameters: { output_format: 'json' } });
    expect((screen.getByLabelText('Workflow Tier') as HTMLSelectElement).value).toBe('2');
  });
  it('rebuilds actual one/many tier choices and preserves Custom through missing policy', () => {
    const view = render(<Form initial={{ model: 'manual', effort: 'max' }} policy={{ default_model_tier: 5, model_tiers: Array.from({ length: 5 }, () => ({ model: null, effort: null })) }} />);
    expect(screen.getAllByRole('option').map((option) => option.textContent)).toEqual(['1', '2', '3', '4', '5', 'Custom']);
    view.rerender(<Form policy={{ model_tiers: [] }} />);
    expect(payload()).toEqual({ model: 'manual', effort: 'max' });
    expect(screen.getByRole('alert').textContent).toContain('empty tier policy');
  });
});


describe('MoonMind#4636 saved partial inheritance and capabilities', () => {
  it('shows a saved effort override with the inherited tier model', () => {
    render(<ModelSelectionFields scope="Step 1" value={{ effort: 'max' }} inherited={{ modelTier: 1 }} profile={profile} onChange={() => {}} />);
    expect((screen.getByLabelText('Step 1 Model') as HTMLInputElement).value).toBe('plan');
    expect((screen.getByLabelText('Step 1 Effort') as HTMLInputElement).value).toBe('max');
    expect(screen.getByRole('option', { name: 'Saved selection' }).hasAttribute('disabled')).toBe(true);
  });
});


describe('MoonMind#4636 capability diagnostics preserve manual values', () => {
  it('retains a known incompatible effort and shows its profile diagnostic', () => {
    const onChange = () => {};
    const capabilities = {
      model: { runtime_default: null, allow_custom: true, options: [] },
      effort: { supported: true, runtime_default: null, allow_custom: true, application: 'cli_flag', options: [{ value: 'max', label: 'Maximum', description: null, status: 'available', compatible_models: ['another-model'] }] },
      diagnostics: [],
    };
    render(<ModelSelectionFields scope="Workflow" value={{ model: 'manual-model', effort: 'max' }} profile={profile} capabilities={capabilities} onChange={onChange} />);
    expect((screen.getByLabelText('Workflow Effort') as HTMLInputElement).value).toBe('max');
    expect(screen.getByText(/Effort max is unavailable for model manual-model/)).toBeTruthy();
  });
  it('reports metadata_only without claiming that effort is applied', () => {
    render(<ModelSelectionFields scope="Workflow" value={{ model: null, effort: 'high' }} profile={profile} capabilities={{ model: { runtime_default: null, allow_custom: true, options: [] }, effort: { supported: false, runtime_default: null, allow_custom: true, application: 'metadata_only', options: [] }, diagnostics: [] }} onChange={() => {}} />);
    expect(screen.getByText(/Effort application: metadata_only/)).toBeTruthy();
    expect((screen.getByLabelText('Workflow Effort') as HTMLInputElement).value).toBe('high');
  });
});


describe('MoonMind#4636 saved resolution uses current provenance', () => {
  it('never invents a model-only saved selection companion from a tier', () => {
    let submitted: ModelSelection | undefined;
    render(<ModelSelectionFields scope="Workflow" value={{ model: 'saved-model' }} profile={profile} onChange={(value) => { submitted = value; }} />);
    expect((screen.getByLabelText('Workflow Effort') as HTMLInputElement).value).toBe('');
    fireEvent.change(screen.getByLabelText('Workflow Model'), { target: { value: 'edited-model' } });
    expect(submitted).toEqual({ model: 'edited-model', effort: null });
  });
  it('shows a resolved legacy profile companion with its source and preserves it on explicit Custom', () => {
    let submitted: ModelSelection | undefined;
    render(<ModelSelectionFields scope="Workflow" value={{ model: 'saved-model' }} profile={profile} preview={{ model: 'saved-model', effort: 'legacy-effort', modelSource: 'task_override', effortSource: 'provider_profile_default' }} onChange={(value) => { submitted = value; }} />);
    expect((screen.getByLabelText('Workflow Effort') as HTMLInputElement).value).toBe('legacy-effort');
    expect(screen.getByText(/saved profile default/i)).toBeTruthy();
    expect(screen.queryByText(/saved selection with workflow inheritance/i)).toBeNull();
    fireEvent.change(screen.getByLabelText('Workflow Tier'), { target: { value: 'custom' } });
    expect(submitted).toEqual({ model: 'saved-model', effort: 'legacy-effort' });
  });
});
