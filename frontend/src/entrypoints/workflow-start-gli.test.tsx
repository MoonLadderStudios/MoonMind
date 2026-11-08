import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { useState } from 'react';
import { cleanup, fireEvent, render, within } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { SchemaCapabilityFields } from './workflow-start';

afterEach(cleanup);

const { load } = createRequire(import.meta.url)('js-yaml') as { load: (text: string) => unknown };
const preset = load(readFileSync('api_service/data/presets/security-gli-readiness.yaml', 'utf8')) as {
  annotations: {
    inputSchema: { properties: Record<string, unknown> };
    defaults: Record<string, unknown>;
  };
};

it('uses existing Create fields for saved systems, per-control choices and evidence prompts', () => {
  const savedSystem = { entityName: 'Saved RGS', entityType: 'rgs', productType: 'interactive_gaming' };
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
  const system = within(view.getByRole('group', { name: 'Saved assessed business and system inputs' }));
  expect(system.getByLabelText('entityType')).toHaveProperty('value', 'rgs');
  expect(JSON.parse((system.getByLabelText('Saved assessed business and system inputs JSON') as HTMLTextAreaElement).value)).toEqual(savedSystem);
  fireEvent.change(system.getByLabelText('entityType'), { target: { value: 'operator' } });
  const choices = { 'GLI-GSF-2:B': { systems: ['casino'], period: { start: '2026-01-01', end: '2026-12-31' } } };
  fireEvent.change(view.getByLabelText('Requirement scope and period choices'), { target: { value: JSON.stringify(choices) } });
  fireEvent.change(view.getByLabelText('Evidence record artifacts'), { target: { value: 'art_audit_evidence' } });
  const result = JSON.parse(view.getByTestId('saved-values').textContent || '{}');
  expect(result.system_inputs).toEqual({ ...savedSystem, entityType: 'operator' });
  expect(result.requirement_choices).toEqual(choices);
  expect(result.evidence_refs).toEqual(['art_audit_evidence']);
  expect(view.getByText(/Laboratory evaluation and regulator approval/)).toBeTruthy();
  expect(view.getByText(/Clean scans leave game, operational and laboratory gaps/)).toBeTruthy();
});
