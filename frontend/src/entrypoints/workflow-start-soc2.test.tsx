import { readinessPresetForm } from '../utils/readinessPresetForm';
import { useState } from 'react';
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { SchemaCapabilityFields } from './workflow-start';

afterEach(cleanup);

const preset = readinessPresetForm('security-soc2-readiness');

it('renders the SOC 2 profile as typed Create fields and keeps saved samples and evidence', () => {
  const savedSystem = {
    entityName: 'Saved service', systems: ['api'], assessmentType: 'type2', categories: ['security'],
    requirementSamples: { 'CC6.1': { populationRequestId: 'occurrence', populationSize: 2, samplingRationale: 'Saved', selectedRecordIds: ['a', 'b'] } },
    requirementEvidence: { 'CC6.1': [{ id: 'design', category: 'access_review', purpose: 'design', description: 'Design', owner: 'Management' }] },
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
  const system = within(view.getByRole('group', { name: 'Saved service and evidence inputs' }));
  expect(system.queryByLabelText('Saved service and evidence inputs JSON')).toBeNull();
  expect(system.getByLabelText('Assessment Type')).toHaveProperty('value', 'type2');
  fireEvent.change(system.getByLabelText('Assessment Type'), { target: { value: 'type1' } });
  const result = JSON.parse(view.getByTestId('saved-values').textContent || '{}');
  expect(result.system_inputs).toEqual({ ...savedSystem, assessmentType: 'type1' });
});
