import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { useState } from 'react';
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { SchemaCapabilityFields } from './workflow-start';

afterEach(cleanup);

const { load } = createRequire(import.meta.url)('js-yaml') as { load: (text: string) => unknown };
const preset = load(readFileSync('api_service/data/presets/security-ukgc-rts.yaml', 'utf8')) as {
  annotations: {
    inputSchema: { properties: Record<string, unknown> };
    defaults: Record<string, unknown>;
  };
};

it('uses existing Create fields for saved systems, per-control choices and evidence prompts', () => {
  const savedSystem = { operatorName: 'Saved casino', licenceClass: 'remote_casino' };
  function Fields() {
    const [values, setValues] = useState<Record<string, unknown>>({ system_inputs: savedSystem });
    return <>
      <SchemaCapabilityFields
        fields={Object.entries(preset.annotations.inputSchema.properties)}
        detail={{ defaults: preset.annotations.defaults }} values={values}
        errors={{}} disabled={false} repositoryOptions={[]} branchOptions={[]}
        onChange={(name, value) => setValues((old) => ({ ...old, [name]: value }))}
      />
      <output data-testid="saved-values">{JSON.stringify(values)}</output>
    </>;
  }
  const view = render(<Fields />);
  const system = within(view.getByRole('group', { name: 'Saved system and licence inputs' }));
  expect(system.getByLabelText('licenceClass')).toHaveProperty('value', 'remote_casino');
  expect(JSON.parse((system.getByLabelText('Saved system and licence inputs JSON') as HTMLTextAreaElement).value)).toEqual(savedSystem);
  fireEvent.change(system.getByLabelText('licenceClass'), { target: { value: 'remote_bingo' } });
  const choices = { '8.29': { systems: ['casino'], period: { start: '2026-01-01', end: '2026-12-31' } } };
  fireEvent.change(view.getByLabelText('Requirement scope and period choices'), { target: { value: JSON.stringify(choices) } });
  fireEvent.change(view.getByLabelText('Evidence record artifacts'), { target: { value: 'art_audit_evidence' } });
  const result = JSON.parse(view.getByTestId('saved-values').textContent || '{}');
  expect(result.system_inputs).toEqual({ ...savedSystem, licenceClass: 'remote_bingo' });
  expect(result.requirement_choices).toEqual(choices);
  expect(result.evidence_refs).toEqual(['art_audit_evidence']);
  expect(view.getByText(/Missing independence or observation/)).toBeTruthy();
  expect(view.getByText(/Certificates and clean scans leave missing RTS/)).toBeTruthy();
});
