import { useState } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { cleanup, render, screen } from '@testing-library/react';
import { ModelSelectionFields } from '../components/workflows/ModelSelectionFields';
import type { ModelSelection, ModelSelectionProfile } from '../lib/modelSelection';
import '../styles/dashboard.css';

const profile = { default_model_tier: 2, model_tiers: [{ model: 'plan', effort: 'low' }, { model: 'implement', effort: 'max' }] };
function SelectorForm({ policy = profile }: { policy?: ModelSelectionProfile }) {
  const [workflow, setWorkflow] = useState<ModelSelection>({});
  const [step, setStep] = useState<ModelSelection>({});
  return <div className="dashboard-content"><form className="stack">
    <ModelSelectionFields scope="Workflow" value={workflow} profile={policy} onChange={setWorkflow} />
    <ModelSelectionFields scope="Step 1" value={step} inherited={workflow} profile={policy} onChange={setStep} />
    <output aria-label="selection payload">{JSON.stringify({ workflow, step })}</output>
  </form></div>;
}
afterEach(async () => { cleanup(); await page.viewport(1280, 800); });

describe('MoonLadderStudios/MoonMind#4636 production model selector', () => {
  it('keeps keyboard focus and caret through Custom edits and late profile refresh', async () => {
    const view = render(<SelectorForm />);
    const model = screen.getByLabelText('Workflow Model') as HTMLInputElement;
    model.focus();
    model.setSelectionRange(2, 5);
    await userEvent.keyboard('X');
    expect(model.value).toBe('imXment');
    expect(document.activeElement).toBe(model);
    expect(model.selectionStart).toBe(3);
    expect((screen.getByLabelText('Workflow Tier') as HTMLSelectElement).value).toBe('custom');
    view.rerender(<SelectorForm policy={{ default_model_tier: 1, model_tiers: [{ model: 'new-profile-model', effort: 'low' }] }} />);
    expect(screen.getByLabelText('Workflow Model')).toBe(model);
    expect(document.activeElement).toBe(model);
    expect(model.value).toBe('imXment');
    expect(model.selectionStart).toBe(3);
    model.setSelectionRange(0, model.value.length);
    await userEvent.keyboard('{Backspace}');
    expect(JSON.parse(screen.getByLabelText('selection payload').textContent || '{}').workflow).toEqual({ model: null, effort: 'max' });
    expect((screen.getByLabelText('Step 1 Model') as HTMLInputElement).value).toBe('');
  });
  it('fits workflow and step inputs at narrow widths with unavailable saved requests', async () => {
    const longValue = 'model-with-a-long-unbroken-identifier-'.repeat(6);
    function MobileForm() {
      const [value, setValue] = useState<ModelSelection>({ modelTier: 9, tierFallback: 'strict', effort: longValue });
      return <div className="dashboard-content"><ModelSelectionFields scope="Workflow" value={value} profile={profile} onChange={setValue} error={longValue} /><ModelSelectionFields scope="Step 1" value={{}} inherited={value} profile={profile} onChange={() => {}} /></div>;
    }
    render(<MobileForm />);
    for (const width of [320, 390, 768]) {
      await page.viewport(width, 844);
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + 2);
      for (const field of document.querySelectorAll('input, select, button')) {
        const rectangle = field.getBoundingClientRect();
        expect(rectangle.left).toBeGreaterThanOrEqual(-1);
        expect(rectangle.right).toBeLessThanOrEqual(window.innerWidth + 1);
      }
    }
    expect(screen.getAllByRole('textbox')).toHaveLength(4);
    expect(screen.getAllByRole('combobox')).toHaveLength(2);
    expect(screen.queryByText('Tier fallback')).toBeNull();
    expect(screen.queryByText(/Hard override/)).toBeNull();
  });
});
