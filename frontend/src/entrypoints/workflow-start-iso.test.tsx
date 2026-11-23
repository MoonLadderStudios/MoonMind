import { readinessPresetForm } from '../utils/readinessPresetForm';
import { useState } from 'react';
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { SchemaCapabilityFields } from './workflow-start';

afterEach(cleanup);

const preset = readinessPresetForm('security-iso-27001');

it('renders the ISMS profile as typed Create fields and keeps saved risks, decisions and evidence', () => {
  const savedSystem = {
    systems: ['isms'], scopeStatement: 'Saved ISMS', interestedParties: ['Customers'],
    risks: [{ id: 'R1', description: 'Saved risk', version: '1', controlIds: ['A.8.8'] }],
    controls: { 'A.8.8': { decision: 'include', rationale: 'Reduce R1', riskIds: ['R1'] } },
    // A saved request's documentary evidence moves to requirement choices at the request boundary.
    requirementEvidence: { '9.2': [{ id: 'audit', category: 'internal_audit', purpose: 'occurrence', description: 'Audit', owner: 'Assurance' }] },
  };
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
  const system = within(view.getByRole('group', { name: 'Saved ISMS scope, risks and applicability decisions' }));
  expect(system.queryByLabelText('Saved ISMS scope, risks and applicability decisions JSON')).toBeNull();
  expect(system.getByLabelText('Scope Statement')).toHaveProperty('value', 'Saved ISMS');
  fireEvent.change(system.getByLabelText('Scope Statement'), { target: { value: 'Revised ISMS' } });
  const choices = { '9.3': { systems: ['isms'] } };
  fireEvent.change(view.getByLabelText('Requirement scope and period choices'), { target: { value: JSON.stringify(choices) } });
  const result = JSON.parse(view.getByTestId('saved-values').textContent || '{}');
  expect(result.system_inputs).toEqual({ ...savedSystem, scopeStatement: 'Revised ISMS' });
  expect(result.requirement_choices).toEqual(choices);
  expect(view.getByText(/organisationalEvidence/)).toBeTruthy();
});
