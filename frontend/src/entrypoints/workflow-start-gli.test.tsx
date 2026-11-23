import { readinessPresetForm } from '../utils/readinessPresetForm';
import { useState } from 'react';
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { SchemaCapabilityFields } from './workflow-start';

afterEach(cleanup);

const preset = readinessPresetForm('security-gli-readiness');

it('uses existing Create fields for saved systems, per-control choices and evidence prompts', () => {
  const savedSystem = { entityName: 'Saved RGS', entityType: 'rgs', productType: 'interactive_gaming' };
  function Fields() {
    const [values, setValues] = useState<Record<string, unknown>>({ system_inputs: savedSystem });
    return <>
      <SchemaCapabilityFields
        fields={Object.entries(preset.inputSchema.properties)}
        detail={preset} values={values}
        errors={{}} disabled={false} repositoryOptions={[]} branchOptions={[]}
        onChange={(name, value) => setValues((old) => ({ ...old, [name]: value }))}
      />
      <output data-testid="saved-values">{JSON.stringify(values)}</output>
    </>;
  }
  const view = render(<Fields />);
  const system = within(view.getByRole('group', { name: 'Saved assessed business and system inputs' }));
  expect(system.getByLabelText('Entity Type')).toHaveProperty('value', 'rgs');
  const jsonEditor = system.getByLabelText('Saved assessed business and system inputs JSON') as HTMLTextAreaElement;
  expect(JSON.parse(jsonEditor.value)).toEqual(savedSystem);
  const imported = { ...savedSystem, entityName: 'Imported RGS' };
  fireEvent.change(jsonEditor, { target: { value: JSON.stringify(imported) } });
  fireEvent.change(system.getByLabelText('Entity Type'), { target: { value: 'operator' } });
  const choices = { 'GLI-GSF-2:B': { systems: ['casino'], period: { start: '2026-01-01', end: '2026-12-31' } } };
  fireEvent.change(view.getByLabelText('Requirement scope and period choices'), { target: { value: JSON.stringify(choices) } });
  fireEvent.change(view.getByLabelText('Evidence record artifacts'), { target: { value: 'art_audit_evidence' } });
  const result = JSON.parse(view.getByTestId('saved-values').textContent || '{}');
  expect(result.system_inputs).toEqual({ ...imported, entityType: 'operator' });
  expect(result.requirement_choices).toEqual(choices);
  expect(result.evidence_refs).toEqual(['art_audit_evidence']);
  expect(view.getByText(/Laboratory evaluation and regulator approval/)).toBeTruthy();
  expect(view.getByText(/Clean scans leave game, operational and laboratory gaps/)).toBeTruthy();
});
