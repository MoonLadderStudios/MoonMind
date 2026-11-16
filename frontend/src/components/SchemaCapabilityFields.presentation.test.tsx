import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SchemaCapabilityFields } from './SchemaCapabilityFields';
import '../styles/dashboard.css';

afterEach(cleanup);

const fields: Array<[string, unknown]> = [
  ['verify', { type: 'boolean', title: 'Run verification', description: 'Adds a separate verification pass.' }],
  ['search', { type: 'string', title: 'GitHub issue search', description: 'Leave blank to select the next eligible issue.' }],
  ['constraints', { type: 'string', title: 'Constraints' }],
];
const detail = {
  uiSchema: {
    search: { order: 0, fullWidth: true },
    verify: { order: 1, helpDisplay: 'tooltip' },
    constraints: { order: 2, advanced: true },
  },
  defaults: { verify: false },
};

it('orders fields and keeps optional help out of the form while preserving field semantics', () => {
  const onChange = vi.fn();
  const { container } = render(<SchemaCapabilityFields fields={fields} detail={detail}
    values={{}} errors={{}} disabled={false} repositoryOptions={[]} branchOptions={[]} onChange={onChange} />);
  const controls = container.querySelectorAll('input');
  expect(controls[0]!.getAttribute('aria-label')).toBe('GitHub issue search');
  expect(controls[0]!.closest('.schema-capability-field')?.classList.contains('schema-capability-field--full-width')).toBe(true);
  const verify = screen.getByRole('checkbox', { name: 'Run verification' });
  expect((verify as HTMLInputElement).checked).toBe(false);
  expect(verify.closest('label')?.classList.contains('schema-capability-checkbox')).toBe(true);
  expect(screen.queryByRole('tooltip')).toBeNull();
  const help = screen.getByRole('button', { name: 'Help for Run verification' });
  fireEvent.focus(help);
  expect(screen.getByRole('tooltip').textContent).toBe('Adds a separate verification pass.');
  fireEvent.keyDown(help, { key: 'Escape' });
  expect(screen.queryByRole('tooltip')).toBeNull();
  fireEvent.click(help);
  expect(screen.getByRole('tooltip')).toBeTruthy();
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(help);
  expect(screen.queryByRole('tooltip')).toBeNull();
  fireEvent.click(verify);
  expect(onChange).toHaveBeenCalledWith('verify', true);
  expect(screen.getByText('Leave blank to select the next eligible issue.')).toBeTruthy();
});

it('does not nest an Advanced disclosure when the caller already controls visibility', () => {
  const { container } = render(<SchemaCapabilityFields fields={fields} detail={detail}
    advancedDisclosure={false} values={{}} errors={{}} disabled={false}
    repositoryOptions={[]} branchOptions={[]} onChange={vi.fn()} />);
  expect(container.querySelector('details')).toBeNull();
  expect(screen.getByRole('textbox', { name: 'Constraints' })).toBeTruthy();
});

it('preserves inline descriptions and error associations by default', () => {
  render(<SchemaCapabilityFields fields={fields.slice(0, 1)} detail={{ defaults: { verify: true } }}
    values={{ verify: false }} errors={{ verify: 'Choose a value' }} disabled={false}
    repositoryOptions={[]} branchOptions={[]} onChange={vi.fn()} />);
  const verify = screen.getByRole('checkbox', { name: 'Run verification' });
  expect((verify as HTMLInputElement).checked).toBe(false);
  expect(verify.getAttribute('aria-describedby')?.split(' ')).toHaveLength(2);
  expect(screen.queryByRole('button', { name: 'Help for Run verification' })).toBeNull();
});

it.each([false, true])('preserves full-width grid ownership for structured groups with tooltip=%s', (tooltip) => {
  const onChange = vi.fn();
  const groupedFields: Array<[string, unknown]> = ['Questions', 'Allowed Followups'].map(title => [title, {
    title, description: `Configure ${title}.`, type: 'array', items: { type: 'object', properties: { query: { type: 'string', title: 'Query' } } },
  }]);
  render(<SchemaCapabilityFields fields={groupedFields} detail={{ uiSchema: Object.fromEntries(
    ['Questions', 'Allowed Followups'].map(title => [title, { helpDisplay: tooltip ? 'tooltip' : 'inline' }]),
  ) }}
    values={{}} errors={{}} disabled={false} repositoryOptions={[]} branchOptions={[]} onChange={onChange} />);
  for (const title of ['Questions', 'Allowed Followups']) {
    const group = screen.getByRole('group', { name: title });
    // col-span-full only governs layout when the fieldset remains a grid item.
    expect(group.classList.contains('col-span-full')).toBe(true);
    const gridItem = tooltip ? group.parentElement! : group;
    if (tooltip) expect(gridItem.classList.contains('schema-capability-field--full-width')).toBe(true);
    expect(gridItem.parentElement?.classList.contains('grid-2')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: `Add ${title} item` }));
    expect(onChange).toHaveBeenCalledWith(title, [{}]);
  }
});
