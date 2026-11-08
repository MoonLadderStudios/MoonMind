import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { useState } from 'react';
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { SchemaCapabilityFields } from './workflow-start';

afterEach(cleanup);
const { load } = createRequire(import.meta.url)('js-yaml') as { load: (text: string) => unknown };
const preset = load(readFileSync('api_service/data/presets/security-nj-dge.yaml', 'utf8')) as {
  annotations: { inputSchema: { properties: Record<string, unknown> }; defaults: Record<string, unknown> };
};

it('keeps NJ entity, annual rule, requirement scope and evidence choices in shared Create fields (#5196)', () => {
  const saved = { entityName: 'Saved RGS', entityType: 'rgs_content_provider', providesContentToOtherOperator: true, systems: ['rgs'], annualRules: ['nj.rgs.annual'] };
  function Fields() {
    const [values, setValues] = useState<Record<string, unknown>>({ system_inputs: saved });
    return <>
      <SchemaCapabilityFields fields={Object.entries(preset.annotations.inputSchema.properties)}
        detail={{ defaults: preset.annotations.defaults }} values={values} errors={{}} disabled={false}
        repositoryOptions={[]} branchOptions={[]}
        onChange={(name, value) => setValues((old) => ({ ...old, [name]: value }))} />
      <output data-testid="saved-values">{JSON.stringify(values)}</output>
    </>;
  }
  const view = render(<Fields />);
  const system = within(view.getByRole('group', { name: 'Saved entity and system inputs' }));
  expect(system.getByLabelText('entityType')).toHaveProperty('value', 'rgs_content_provider');
  expect(JSON.parse((system.getByLabelText('Saved entity and system inputs JSON') as HTMLTextAreaElement).value)).toEqual(saved);
  fireEvent.change(system.getByLabelText('entityType'), { target: { value: 'casino_licensee' } });
  const choices = { 'nj.rgs.incidents': { systems: ['rgs'], period: { start: '2026-03-01', end: '2026-06-30' } } };
  fireEvent.change(view.getByLabelText('Requirement scope and period choices'), { target: { value: JSON.stringify(choices) } });
  fireEvent.change(view.getByLabelText('Evidence record artifacts'), { target: { value: 'art_incident_records' } });
  fireEvent.change(view.getByLabelText('Annual assessment records artifact'), { target: { value: 'art_annual_report' } });
  const result = JSON.parse(view.getByTestId('saved-values').textContent || '{}');
  expect(result.system_inputs).toEqual({ ...saved, entityType: 'casino_licensee' });
  expect(result.requirement_choices).toEqual(choices);
  expect(result.evidence_refs).toEqual(['art_incident_records']);
  expect(result.audit_ref).toEqual('art_annual_report');
  expect(view.getByText(/Missing assessor affiliation, independence/)).toBeTruthy();
  expect(view.getByText(/Clean scans cannot supply/)).toBeTruthy();
});
