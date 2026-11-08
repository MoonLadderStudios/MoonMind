import { useState } from 'react';
import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { SchemaCapabilityFields, resolveSchemaCapabilityValues, validateSchemaCapabilityValues } from './workflow-start';

afterEach(cleanup);

const fields: Array<[string, unknown]> = [['assessment', {
  type: 'object', title: 'Assessment', description: 'Review only the selected targets.',
  properties: {
    target: { type: 'string', title: 'Target' },
    regions: { type: 'array', title: 'Regions', items: { type: 'string' } },
    limit: { type: 'integer', title: 'Limit', default: 3 },
    optional: { type: 'object', title: 'Optional evidence', properties: { path: { type: 'string', title: 'Evidence path' } } },
  },
}]];

function Form() {
  const [values, setValues] = useState<Record<string, unknown>>({ assessment: { target: 'original', retained: 'keep' } });
  return <><SchemaCapabilityFields fields={fields} detail={{}} values={values}
    errors={{ 'assessment.target': 'Choose an admitted target.' }} disabled={false}
    repositoryOptions={[]} branchOptions={[]}
    onChange={(name, value) => setValues(current => ({ ...current, [name]: value }))} />
    <output data-testid="values">{JSON.stringify(values)}</output></>;
}

it('edits nested typed fields without JSON and preserves siblings and optional absence', () => {
  const view = render(<Form />);
  const target = view.getByRole('textbox', { name: 'Target' });
  expect(target.getAttribute('aria-invalid')).toBe('true');
  expect(view.getByRole('spinbutton', { name: 'Limit' })).toHaveProperty('value', '3');
  fireEvent.change(target, { target: { value: 'replacement' } });
  expect(JSON.parse(view.getByTestId('values').textContent!)).toEqual({ assessment: { target: 'replacement', retained: 'keep' } });
  fireEvent.change(view.getByRole('textbox', { name: 'Regions' }), { target: { value: 'us-east-1\n' } });
  expect(view.getByRole('textbox', { name: 'Regions' })).toHaveProperty('value', 'us-east-1\n');
  fireEvent.change(view.getByRole('textbox', { name: 'Regions' }), { target: { value: 'us-east-1\neu-west-1' } });
  expect(JSON.parse(view.getByTestId('values').textContent!).assessment.regions).toEqual(['us-east-1', 'eu-west-1']);
  fireEvent.change(view.getByRole('textbox', { name: 'Regions' }), { target: { value: ' literal value \nsecond' } });
  expect(JSON.parse(view.getByTestId('values').textContent!).assessment.regions).toEqual([' literal value ', 'second']);
  fireEvent.change(view.getByRole('textbox', { name: 'Evidence path' }), { target: { value: 'report.json' } });
  expect(JSON.parse(view.getByTestId('values').textContent!).assessment.optional).toEqual({ path: 'report.json' });
});

it('keeps explicit JSON widgets available and all nested controls disabled', () => {
  const view = render(<SchemaCapabilityFields fields={fields} detail={{ uiSchema: { assessment: { widget: 'json' } } }}
    values={{}} errors={{}} disabled repositoryOptions={[]} branchOptions={[]} onChange={() => {}} />);
  expect(view.getByRole('textbox', { name: 'Assessment' })).toHaveProperty('disabled', true);
  expect(view.queryByRole('textbox', { name: 'Target' })).toBeNull();
  view.rerender(<SchemaCapabilityFields fields={fields} detail={{}} values={{}} errors={{}} disabled
    repositoryOptions={[]} branchOptions={[]} onChange={() => {}} />);
  expect(view.getByRole('textbox', { name: 'Target' })).toHaveProperty('disabled', true);
});

it('resolves defaults only inside authored objects and reports nested validation paths', () => {
  const schema = { inputSchema: { type: 'object', properties: { assessment: {
    type: 'object', required: ['target'], properties: {
      target: { type: 'string', title: 'Target' }, limit: { type: 'integer', minimum: 1, maximum: 5, default: 3 },
      optional: { type: 'object', required: ['path'], properties: { path: { type: 'string' }, mode: { type: 'string', default: 'read' } } },
    },
  } } } };
  expect(resolveSchemaCapabilityValues(schema, {})).toEqual({});
  const values = resolveSchemaCapabilityValues(schema, { assessment: { target: 'repo', retained: 'keep' } });
  expect(values).toEqual({ assessment: { target: 'repo', retained: 'keep', limit: 3 } });
  expect(validateSchemaCapabilityValues(schema, values)).toEqual({});
  expect(validateSchemaCapabilityValues(schema, { assessment: { target: '', limit: 6, optional: {} } })).toEqual({
    'assessment.target': 'Target is required.', 'assessment.limit': 'limit must be at most 5.', 'assessment.optional.path': 'path is required.',
  });
});

it('adds and removes typed object-list entries without JSON and resolves row defaults', () => {
  const routeSchema = { type: 'object', required: ['path'], properties: {
    path: { type: 'string', title: 'Path' }, methods: { type: 'array', items: { type: 'string', enum: ['GET', 'POST'] }, default: ['GET'] },
  } };
  const schema = { type: 'array', title: 'Routes', items: routeSchema };
  function Routes() {
    const [values, setValues] = useState<Record<string, unknown>>({});
    return <><SchemaCapabilityFields fields={[["routes", schema]]} detail={{}} values={values} errors={{}} disabled={false}
      repositoryOptions={[]} branchOptions={[]} onChange={(key, value) => setValues({ [key]: value })} />
      <output data-testid="routes">{JSON.stringify(values)}</output></>;
  }
  const view = render(<Routes />);
  fireEvent.click(view.getByRole('button', { name: 'Add Routes item' }));
  fireEvent.change(view.getByRole('textbox', { name: 'Path' }), { target: { value: '/approved' } });
  const detail = { inputSchema: { type: 'object', properties: { routes: schema } } };
  expect(resolveSchemaCapabilityValues(detail, JSON.parse(view.getByTestId('routes').textContent!))).toEqual({ routes: [{ path: '/approved', methods: ['GET'] }] });
  fireEvent.click(view.getByRole('button', { name: 'Add Routes item' }));
  expect(validateSchemaCapabilityValues(detail, JSON.parse(view.getByTestId('routes').textContent!))).toEqual({ 'routes.1.path': 'Path is required.' });
  fireEvent.click(view.getByRole('button', { name: 'Remove Routes item 1' }));
  expect(view.getAllByRole('textbox', { name: 'Path' })).toHaveLength(1);
  expect(JSON.parse(view.getByTestId('routes').textContent!)).toEqual({ routes: [{}] });
});

it('does not display an unauthored enum choice as selected', () => {
  const view = render(<SchemaCapabilityFields fields={[["mode", { type: 'string', title: 'Mode', enum: ['baseline', 'active'] }]]}
    detail={{}} values={{}} errors={{}} disabled={false} repositoryOptions={[]} branchOptions={[]} onChange={() => {}} />);
  expect(view.getByRole('combobox', { name: 'Mode' })).toHaveProperty('value', '');
});

it('explicitly omits saved optional objects and lists without erasing accepted empty or false siblings', () => {
  const inputSchema = { type: 'object', required: ['assessment'], properties: { assessment: {
    type: 'object', required: ['mode'], properties: {
      mode: { type: 'string', enum: ['api', 'baseline'] },
      apiDefinition: { type: 'object', title: 'API definition', required: ['format', 'artifact'], properties: {
        format: { type: 'string', enum: ['openapi'] }, artifact: { type: 'string' },
      } },
      identity: { type: 'object', title: 'Identity', required: ['credential'], properties: {
        credential: { type: 'string' }, usernameField: { type: 'string', default: 'username' },
      } },
      rules: { type: 'array', title: 'Rules', items: { type: 'object', required: ['path'], properties: { path: { type: 'string', title: 'Rule path' } } } },
      discovery: { type: 'array', items: { type: 'string', enum: ['spider'] } },
      enabled: { type: 'boolean' }, note: { type: 'string', title: 'Note' },
    },
  } } };
  const saved = { assessment: { mode: 'api', apiDefinition: { format: 'openapi', artifact: 'art_spec' }, identity: { credential: 'vault:reader' }, rules: [{ path: '/bundled/custom' }], discovery: [], enabled: false, note: 'old' } };
  function SavedDraft() {
    const [values, setValues] = useState<Record<string, unknown>>(saved);
    const resolved = resolveSchemaCapabilityValues({ inputSchema }, values);
    return <><SchemaCapabilityFields fields={Object.entries(inputSchema.properties)} detail={{ inputSchema }} values={resolved} errors={{}}
      disabled={false} repositoryOptions={[]} branchOptions={[]} onChange={(key, value) => setValues({ ...values, [key]: value })} />
      <output data-testid="saved-draft">{JSON.stringify(resolved)}</output></>;
  }
  const view = render(<SavedDraft />);
  fireEvent.click(view.getByRole('button', { name: 'Omit API definition' }));
  fireEvent.click(view.getByRole('button', { name: 'Omit Identity' }));
  fireEvent.click(view.getByRole('button', { name: 'Remove Rules item 1' }));
  expect(JSON.parse(view.getByTestId('saved-draft').textContent!).assessment.rules).toEqual([]);
  fireEvent.click(view.getByRole('button', { name: 'Omit Rules' }));
  fireEvent.change(view.getByRole('combobox', { name: 'mode' }), { target: { value: 'baseline' } });
  fireEvent.change(view.getByRole('textbox', { name: 'Note' }), { target: { value: '' } });
  view.rerender(<SavedDraft />);
  expect(JSON.parse(view.getByTestId('saved-draft').textContent!)).toEqual({ assessment: { mode: 'baseline', discovery: [], enabled: false, note: '' } });
  expect(view.queryByRole('button', { name: /Omit assessment/i })).toBeNull();
  fireEvent.click(view.getByRole('button', { name: 'Omit discovery' }));
  expect(JSON.parse(view.getByTestId('saved-draft').textContent!)).toEqual({ assessment: { mode: 'baseline', enabled: false, note: '' } });
});

it.each([
  ['text', { type: 'string' }, ''],
  ['enum', { type: 'string', enum: ['', 'yes'] }, ''],
  ['number', { type: 'number' }, 0],
  ['boolean', { type: 'boolean' }, false],
  ['object', { type: 'object', properties: { child: { type: 'string' } } }, {}],
  ['string list', { type: 'array', items: { type: 'string' } }, []],
  ['object list', { type: 'array', items: { type: 'object', properties: { child: { type: 'string' } } } }, []],
  ['enum list', { type: 'array', items: { type: 'string', enum: ['yes'] } }, []],
])('keeps an explicit empty/zero/false %s until the optional value is omitted', (_kind, field, initial) => {
  const inputSchema = { type: 'object', properties: { option: { ...field, title: 'Option' } } };
  function Draft({ required = false }: { required?: boolean }) {
    const [values, setValues] = useState<Record<string, unknown>>({ option: initial });
    const detail = { inputSchema: { ...inputSchema, required: required ? ['option'] : [] } };
    return <><SchemaCapabilityFields fields={Object.entries(inputSchema.properties)} detail={detail} values={values} errors={{}}
      disabled={false} repositoryOptions={[]} branchOptions={[]} onChange={(key, value) => setValues({ [key]: value })} />
      <output data-testid="optional-value">{JSON.stringify(resolveSchemaCapabilityValues(detail, values))}</output></>;
  }
  const view = render(<Draft required />);
  expect(view.queryByRole('button', { name: 'Omit Option' })).toBeNull();
  expect(JSON.parse(view.getByTestId('optional-value').textContent!)).toEqual({ option: initial });
  view.rerender(<Draft />);
  fireEvent.click(view.getByRole('button', { name: 'Omit Option' }));
  expect(JSON.parse(view.getByTestId('optional-value').textContent!)).toEqual({});
});

it('honors explicit text presentation for timezone-bearing date-time values', () => {
  const view = render(<SchemaCapabilityFields fields={[["expiresAt", { type: 'string', format: 'date-time', 'x-moonmind-widget': 'text', title: 'Expiry' }]]}
    detail={{}} values={{ expiresAt: '2026-11-30T12:00:00+02:00' }} errors={{}} disabled={false} repositoryOptions={[]} branchOptions={[]} onChange={() => {}} />);
  expect(view.getByLabelText('Expiry')).toHaveProperty('type', 'text');
  expect(view.getByLabelText('Expiry')).toHaveProperty('value', '2026-11-30T12:00:00+02:00');
});

it('keeps advanced JSON authoring for open-ended typed objects without losing known-field edits', () => {
  function Draft() {
    const [values, setValues] = useState<Record<string, unknown>>({ system: { kind: 'saved', extra: { retained: true } } });
    return <><SchemaCapabilityFields fields={[["system", { type: 'object', title: 'System', properties: { kind: { type: 'string', title: 'Kind' } } }]]}
      detail={{}} values={values} errors={{}} disabled={false} repositoryOptions={[]} branchOptions={[]} onChange={(key, value) => setValues({ [key]: value })} />
      <output data-testid="open-object">{JSON.stringify(values)}</output></>;
  }
  const view = render(<Draft />);
  fireEvent.change(view.getByLabelText('System JSON'), { target: { value: '{"kind":"saved","extra":{"retained":true},"entityName":"New entity"}' } });
  fireEvent.change(view.getByLabelText('Kind'), { target: { value: 'edited' } });
  expect(JSON.parse(view.getByTestId('open-object').textContent!)).toEqual({ system: { kind: 'edited', extra: { retained: true }, entityName: 'New entity' } });
  expect(JSON.parse((view.getByLabelText('System JSON') as HTMLTextAreaElement).value).kind).toBe('edited');
});

it.each([false, true])('retains invalid advanced JSON until corrected, including object-list rows (%s)', (list) => {
  const item = { type: 'object', title: 'System', properties: { kind: { type: 'string', title: 'Kind' } } };
  function Draft() {
    const [values, setValues] = useState<Record<string, unknown>>({ system: list ? [{ kind: 'saved', extra: true }] : { kind: 'saved', extra: true } });
    return <><SchemaCapabilityFields fields={[["system", list ? { type: 'array', title: 'Systems', items: item } : item]]}
      detail={{}} values={values} errors={{}} disabled={false} repositoryOptions={[]} branchOptions={[]} onChange={(key, value) => setValues({ [key]: value })} />
      <output data-testid="invalid-object">{JSON.stringify(values)}</output></>;
  }
  const view = render(<Draft />);
  const raw = view.getByLabelText(list ? 'Systems item 1 JSON' : 'System JSON');
  fireEvent.change(raw, { target: { value: '{"kind":' } });
  expect(raw).toHaveProperty('value', '{"kind":');
  expect(view.getByLabelText('Kind')).toHaveProperty('disabled', true);
  fireEvent.change(view.getByLabelText('Kind'), { target: { value: 'must not replace invalid JSON' } });
  expect(JSON.parse(view.getByTestId('invalid-object').textContent!)).toEqual({ system: list ? ['{"kind":'] : '{"kind":' });
  fireEvent.change(raw, { target: { value: '{"kind":"corrected","extra":true}' } });
  expect(view.getByLabelText('Kind')).toHaveProperty('disabled', false);
  expect(view.getByLabelText('Kind')).toHaveProperty('value', 'corrected');
});
