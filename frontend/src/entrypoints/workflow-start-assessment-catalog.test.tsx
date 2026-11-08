import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { useState } from 'react';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { SchemaCapabilityFields, resolveSchemaCapabilityValues } from './workflow-start';

const yaml = createRequire(import.meta.url)('js-yaml') as { load: (text: string) => { annotations: { inputSchema: Record<string, unknown> } } };
afterEach(cleanup);

it.each([
  ['security-code-supply-chain', 'Input path'],
  ['security-image-sbom', 'Image reference'],
  ['security-aws-posture', 'Account'],
  ['security-web-api', 'Origin'],
])('renders the actual %s catalog input contract without a JSON scope editor', (slug, targetLabel) => {
  const preset = yaml.load(readFileSync(`api_service/data/presets/${slug}.yaml`, 'utf8'));
  const schema = preset.annotations.inputSchema;
  const properties = schema.properties as Record<string, unknown>;
  const detail = { inputSchema: schema };
  const values = resolveSchemaCapabilityValues(detail, {});
  const view = render(<SchemaCapabilityFields fields={Object.entries(properties)} detail={{}} values={values} errors={{}}
    disabled={false} repositoryOptions={[]} branchOptions={[]} onChange={() => {}} />);
  expect(view.getByRole('textbox', { name: targetLabel })).toBeTruthy();
  expect(view.queryByRole('textbox', { name: 'Assessment inputs' })).toBeNull();
  if (slug === 'security-web-api') {
    expect(view.getByRole('textbox', { name: 'Path' })).toHaveProperty('value', '/');
    expect(view.getByRole('button', { name: 'Add Routes item' })).toBeTruthy();
    expect(view.getByRole('textbox', { name: 'Credential ref' })).toHaveProperty('value', '');
    expect((values.assessment as Record<string, unknown> | undefined)?.identity).toBeUndefined();
  }
});

it.each([
  ['security-web-api', { mode: 'active', activeStrength: 'low' }, 'Mode', 'baseline', ['Active strength'], ['activeStrength']],
  ['security-image-sbom', { target: 'registry', imageReference: 'registry.example/app:stable', targetCredentialRef: 'vault:registry' }, 'Target', 'archive', ['Image reference', 'Target credential ref'], ['imageReference', 'targetCredentialRef']],
])('restores omission after switching the actual %s native input shape', (slug, initial, switchLabel, next, omittedLabels, omittedKeys) => {
  const schema = yaml.load(readFileSync(`api_service/data/presets/${slug}.yaml`, 'utf8')).annotations.inputSchema;
  function Draft() {
    const [values, setValues] = useState<Record<string, unknown>>({ assessment: initial });
    const resolved = resolveSchemaCapabilityValues({ inputSchema: schema }, values);
    return <><SchemaCapabilityFields fields={Object.entries(schema.properties as Record<string, unknown>)} detail={{ inputSchema: schema }} values={resolved}
      errors={{}} disabled={false} repositoryOptions={[]} branchOptions={[]} onChange={(key, value) => setValues({ ...values, [key]: value })} />
      <output data-testid="switched-native-inputs">{JSON.stringify(resolved.assessment)}</output></>;
  }
  const view = render(<Draft />);
  fireEvent.change(view.getByRole('combobox', { name: switchLabel }), { target: { value: next } });
  for (const label of omittedLabels) fireEvent.click(view.getByRole('button', { name: `Omit ${label}` }));
  view.rerender(<Draft />);
  const resolved = JSON.parse(view.getByTestId('switched-native-inputs').textContent!);
  for (const key of omittedKeys) expect(Object.hasOwn(resolved, key)).toBe(false);
  expect(resolved[switchLabel.toLowerCase()]).toBe(next);
});
