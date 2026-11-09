import { useState } from 'react';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { SchemaCapabilityFields } from './SchemaCapabilityFields';

afterEach(cleanup);
const inputSchema = { type: 'object', required: ['assessment'], properties: { assessment: {
  type: 'object', title: 'Assessment', additionalProperties: false, required: ['target'], properties: {
    target: { type: 'string', title: 'Target' },
    enabled: { type: 'boolean', title: 'Enabled', default: true },
    identity: { type: 'object', title: 'Identity', additionalProperties: false, properties: { credentialRef: { type: 'string', title: 'Credential reference' } } },
  },
} } };
const detail = { inputSchema, uiSchema: { assessment: { target: { advanced: true }, enabled: { advanced: true }, identity: { advanced: true } } } };
function Form({ errors = {} }: { errors?: Record<string, string> }) {
  const [values, setValues] = useState({ assessment: { target: 'saved', enabled: false, identity: { credentialRef: 'vault:reader' } } });
  return <><SchemaCapabilityFields fields={Object.entries(inputSchema.properties)} detail={detail} values={values}
    errors={errors} disabled={false} repositoryOptions={[]} branchOptions={[]}
    onChange={(name, value) => setValues(current => ({ ...current, [name]: value }))} />
    <output data-testid="values">{JSON.stringify(values)}</output></>;
}

it('discloses optional fields without expanding saved values or hiding required inputs', () => {
  const view = render(<Form />);
  expect(view.getByRole('textbox', { name: 'Target' })).toBeTruthy();
  const summary = view.getByText('Advanced Assessment options');
  const disclosure = summary.closest('details')!;
  expect(disclosure.open).toBe(false);
  expect(view.getByLabelText('Enabled').closest('details')).toBe(disclosure);
  expect(view.getByLabelText('Credential reference').closest('details')).toBe(disclosure);
  fireEvent.click(summary);
  fireEvent.change(view.getByLabelText('Target'), { target: { value: 'new target' } });
  expect(JSON.parse(view.getByTestId('values').textContent!)).toEqual({ assessment: { target: 'new target', enabled: false, identity: { credentialRef: 'vault:reader' } } });
  fireEvent.click(summary);
  expect(disclosure.open).toBe(false);
  expect(JSON.parse(view.getByTestId('values').textContent!).assessment.enabled).toBe(false);
});

it('opens the enclosing disclosure for nested validation errors', () => {
  const view = render(<Form />);
  view.rerender(<Form errors={{ 'assessment.identity.credentialRef': 'Choose a credential reference.' }} />);
  expect(view.getByText('Advanced Assessment options').closest('details')).toHaveProperty('open', true);
  expect(view.getByRole('textbox', { name: 'Credential reference' }).getAttribute('aria-invalid')).toBe('true');
  fireEvent.click(view.getByText('Advanced Assessment options'));
  expect(view.getByText('Advanced Assessment options').closest('details')).toHaveProperty('open', false);
  view.rerender(<Form errors={{ 'assessment.identity.credentialRef': 'Choose a credential reference.' }} />);
  expect(view.getByText('Advanced Assessment options').closest('details')).toHaveProperty('open', true);
});
