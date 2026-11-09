import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { useState } from 'react';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { SchemaCapabilityFields, resolveSchemaCapabilityValues } from './workflow-start';
import { safeCapabilityDefault } from '../components/SchemaCapabilityFields';

const yaml = createRequire(import.meta.url)('js-yaml') as { load: (text: string) => { annotations: { inputSchema: Record<string, unknown>; uiSchema?: Record<string, unknown> } } };
afterEach(cleanup);

it.each([
  ['security-code-supply-chain', { scanner: 'trivy', outputDirectory: 'reports', inputPath: 'src', exclusions: ['vendor'], gitleaksOwnsSecrets: false, requireFreshFeeds: false }, 'Input path', 'selected/src', 'inputPath'],
  ['security-web-api', { assessmentRef: 'web-review', mode: 'baseline', origin: 'https://example.test', routes: [{ path: '/approved', methods: ['GET'] }, { path: '/login', methods: ['GET', 'POST'] }], startPaths: ['/approved'], exclusions: ['/logout'], egressPolicyRef: 'web', identity: { name: 'reader', method: 'form', credentialRef: 'vault://reader', verificationPath: '/approved', loggedInMarker: 'Fixture reader', loginPath: '/login', username: 'reader', usernameField: 'username', passwordField: 'password' } }, 'Origin', 'https://selected.test', 'origin'],
])('round-trips saved native %s defaults through selection and nested edits', (slug, assessment, label, selected, key) => {
  const annotations = yaml.load(readFileSync(`api_service/data/presets/${slug}.yaml`, 'utf8')).annotations;
  const schema = annotations.inputSchema;
  const detail = { ...annotations, defaults: { assessment } };
  function Draft() {
    const [values, setValues] = useState<Record<string, unknown>>({});
    const resolved = resolveSchemaCapabilityValues(detail, values);
    return <><SchemaCapabilityFields fields={Object.entries(schema.properties as Record<string, unknown>)} detail={detail} values={resolved} errors={{}}
      disabled={false} repositoryOptions={[]} branchOptions={[]} onChange={(name, value) => setValues({ ...values, [name]: value })} />
      <output data-testid="native-saved">{JSON.stringify(resolved.assessment)}</output></>;
  }
  const view = render(<Draft />);
  expect(view.getByText('Advanced Assessment options').closest('details')).toHaveProperty('open', false);
  expect(JSON.parse(view.getByTestId('native-saved').textContent!)).toMatchObject(assessment);
  fireEvent.change(view.getByRole('textbox', { name: label }), { target: { value: selected } });
  expect(JSON.parse(view.getByTestId('native-saved').textContent!)).toMatchObject({ ...assessment, [key]: selected });
  const saved = JSON.parse(view.getByTestId('native-saved').textContent!);
  expect(resolveSchemaCapabilityValues({ inputSchema: schema, defaults: { assessment: saved } }, {}).assessment).toEqual(saved);
});

it('rejects secret values and undeclared native default fields', () => {
  const schema = yaml.load(readFileSync('api_service/data/presets/security-code-supply-chain.yaml', 'utf8')).annotations.inputSchema;
  for (const assessment of [
    { scanner: 'gitleaks', outputDirectory: 'reports', password: 'raw' },
    { scanner: 'gitleaks', outputDirectory: 'reports', inputPath: 'token=raw-secret' },
  ]) expect(resolveSchemaCapabilityValues({ inputSchema: schema, defaults: { assessment } }, {}).assessment).not.toMatchObject(assessment);
  expect(safeCapabilityDefault({ unknown: { password: 'raw' } }, 'unknown')).toBeUndefined();
});

it('authors the exact controlled API-to-job request using the maintained Create form', () => {
  const expected = JSON.parse(readFileSync('tests/fixtures/security_review/create-code-preset.json', 'utf8'));
  const schema = yaml.load(readFileSync('api_service/data/presets/security-code-supply-chain.yaml', 'utf8')).annotations.inputSchema;
  function Draft() {
    const [values, setValues] = useState<Record<string, unknown>>({});
    const resolved = resolveSchemaCapabilityValues({ inputSchema: schema }, values);
    return <><SchemaCapabilityFields fields={Object.entries(schema.properties as Record<string, unknown>)} detail={{ inputSchema: schema }} values={resolved} errors={{}}
      disabled={false} repositoryOptions={[]} branchOptions={[]} onChange={(key, value) => setValues({ ...values, [key]: value })} />
      <output data-testid="create-payload">{JSON.stringify(resolved)}</output></>;
  }
  const view = render(<Draft />);
  fireEvent.change(view.getByLabelText('Goal'), { target: { value: expected.goal } });
  fireEvent.change(view.getByLabelText('Output directory'), { target: { value: expected.assessment.outputDirectory } });
  fireEvent.change(view.getByLabelText('Exclusions'), { target: { value: 'vendor' } });
  fireEvent.change(view.getByLabelText('Timeout seconds'), { target: { value: '45' } });
  fireEvent.click(view.getByLabelText('Gitleaks owns secrets'));
  fireEvent.click(view.getByLabelText('Require fresh feeds'));
  expect(JSON.parse(view.getByTestId('create-payload').textContent!)).toEqual(expected);
});

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

it.each([
  ['security-code-supply-chain', ['Scanner', 'Input path', 'Output directory', 'Exclusions', 'Require fresh feeds'], ['Max files', 'Database digest']],
  ['security-image-sbom', ['Operation', 'Target', 'Image reference', 'Input artifact ref', 'Egress policy ref', 'Target credential ref', 'Database cache ref', 'Database digest'], ['Max sbom bytes', 'Max input bytes']],
  ['security-aws-posture', ['Account', 'Credential ref', 'Regions', 'Egress policy ref'], ['Max retry attempts', 'Max report bytes']],
  ['security-web-api', ['Mode', 'Origin', 'Egress policy ref', 'Exclusions', 'Active strength', 'Format'], ['Max depth', 'Credential ref']],
])('keeps common %s inputs outside its advanced disclosure', (slug, commonLabels, advancedLabels) => {
  const annotations = yaml.load(readFileSync(`api_service/data/presets/${slug}.yaml`, 'utf8')).annotations;
  const schema = annotations.inputSchema;
  const view = render(<SchemaCapabilityFields fields={Object.entries(schema.properties as Record<string, unknown>)}
    detail={annotations} values={resolveSchemaCapabilityValues(annotations, {})} errors={{}}
    disabled={false} repositoryOptions={[]} branchOptions={[]} onChange={() => {}} />);
  const disclosure = view.getByText('Advanced Assessment options').closest('details')!;
  expect(disclosure.open).toBe(false);
  expect(disclosure.querySelectorAll('input, select, textarea').length).toBeGreaterThanOrEqual(4);
  for (const label of commonLabels) expect(view.getByLabelText(label).closest('details')).toBeNull();
  for (const label of advancedLabels) expect(view.getByLabelText(label).closest('details')).toBe(disclosure);
});
