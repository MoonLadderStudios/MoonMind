import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SchemaCapabilityFields } from './workflow-start';

afterEach(cleanup);

it('gives repeated step schema fields unique ids and associates hints and errors', () => {
  const props = {
    fields: [['summary', { type: 'string', description: 'Describe the outcome' }], ['enabled', { type: 'boolean' }]] as Array<[string, unknown]>,
    detail: { defaults: {}, uiSchema: {} }, values: {},
    errors: { summary: 'Summary is required' }, disabled: false,
    repositoryOptions: [], branchOptions: [], onChange: vi.fn(),
  };
  const { container, getAllByRole } = render(<><SchemaCapabilityFields {...props} /><SchemaCapabilityFields {...props} /></>);
  const inputs = getAllByRole('textbox');
  expect(new Set([...container.querySelectorAll('[id]')].map((el) => el.id)).size).toBe(container.querySelectorAll('[id]').length);
  for (const input of inputs) {
    expect(input.getAttribute('aria-invalid')).toBe('true');
    const descriptions = (input.getAttribute('aria-describedby') ?? '').split(' ').map((id) => document.getElementById(id)?.textContent).join(' ');
    expect(descriptions).toContain('Summary is required');
    expect(descriptions).toContain('Describe the outcome');
    expect(container.querySelector(`label[for="${input.id}"]`)).toBeTruthy();
  }
});
