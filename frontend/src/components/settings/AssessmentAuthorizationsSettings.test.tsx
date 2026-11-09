import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithClient, screen, waitFor } from '../../utils/test-utils';
import { AssessmentAuthorizationsSettings } from './AssessmentAuthorizationsSettings';
import { InstanceSettingsPage } from '../../entrypoints/settings';
import type { BootPayload } from '../../boot/parseBootPayload';

const key = 'security.assessment_authorizations';
const authorization = { ref: 'payments-review', name: 'Payments', enabled: true, generation: 'generation-7', authorityKind: 'client_engagement', authoritySource: 'Signed scope reference', validFrom: '2026-01-01T00:00:00Z', validUntil: '2026-12-01T00:00:00Z', maxTotalJobSeconds: 120, scope: { assessmentRef: 'payments', profileRefs: ['scanner@1'], actionClasses: ['passive'], targets: [{ workspacePaths: ['.'] }], maxJobs: 2, maxJobSeconds: 60, evidenceIntent: 'Retain report' } };
const inputSchema = { type: 'object', required: ['assessmentRef', 'profileRefs', 'actionClasses', 'targets', 'maxJobs', 'maxJobSeconds', 'evidenceIntent'], properties: {
  assessmentRef: { type: 'string', title: 'Assessment reference' },
  profileRefs: { type: 'array', title: 'Profiles', items: { type: 'string' } },
  actionClasses: { type: 'array', title: 'Actions', items: { type: 'string' } },
  targets: { type: 'array', title: 'Targets', items: { type: 'object', properties: {
    cloudAccount: { type: 'string', title: 'AWS account' }, cloudProvider: { type: 'string', title: 'Cloud provider', enum: ['aws'] },
    cloudRegions: { type: 'array', title: 'AWS regions', items: { type: 'string' } },
    webOrigin: { type: 'string', title: 'Web origin' }, webRoutes: { type: 'array', title: 'Web routes', items: { type: 'object', properties: {
      path: { type: 'string', title: 'Route path' }, methods: { type: 'array', title: 'Route methods', items: { type: 'string', enum: ['GET', 'POST'] } },
    } } },
  } } },
  grants: { type: 'array', title: 'Credential grants', items: { type: 'object', properties: {
    ref: { type: 'string', title: 'Secret reference' }, purpose: { type: 'string', title: 'Grant purpose', enum: ['cloud_inventory', 'web_auth'] },
    account: { type: 'string', title: 'Grant account' }, roleArn: { type: 'string', title: 'Role ARN' },
    expiresAt: { type: 'string', format: 'date-time', 'x-moonmind-widget': 'text', title: 'Grant expiry (ISO with timezone)' }, profileRefs: { type: 'array', title: 'Grant profiles', items: { type: 'string' } },
  } } },
  maxJobs: { type: 'integer', title: 'Maximum jobs' }, maxJobSeconds: { type: 'integer', title: 'Maximum job seconds' },
  maxCpuMillis: { type: 'integer', title: 'CPU ceiling' }, maxMemoryMiB: { type: 'integer', title: 'Memory ceiling' },
  maxRequestsPerSecond: { type: 'integer', title: 'Request rate ceiling' }, evidenceIntent: { type: 'string', title: 'Evidence intent' },
} };
function setup(status = 200, readOnly = false, records: typeof authorization[] = [authorization]) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    if (init?.method === 'PATCH') return new Response(JSON.stringify({ detail: 'Concurrent update' }), { status });
    if (String(url).includes('/audit?')) return new Response(JSON.stringify({ items: [] }));
    return new Response(JSON.stringify({ categories: { Security: [{ key, title: 'Assessment authorizations', type: 'object', ui: 'key_value', category: 'Security', input_schema: inputSchema, effective_value: { authorizations: records }, value_version: 9, read_only: readOnly, audit: { store_old_value: true, store_new_value: true, redact: false }, diagnostics: [], applies_to: [], order: 1 }] } }));
  });
}
afterEach(() => vi.restoreAllMocks());
describe('assessment authorization Settings', () => {
  it('preserves server generation and saves edited records with the original setting version', async () => {
    const fetch = setup(); renderWithClient(<MemoryRouter><InstanceSettingsPage payload={{} as BootPayload} /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText('Authorization name'), { target: { value: 'Payments updated' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment authorizations' }));
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true));
    const body = JSON.parse(String(fetch.mock.calls.find(([, init]) => init?.method === 'PATCH')![1]!.body));
    expect(body.expected_versions).toEqual({ [key]: 9 });
    expect(body.changes[key].authorizations).toEqual([{ ...authorization, name: 'Payments updated' }]);
    expect(screen.queryByLabelText('Generation')).toBeNull();
  });
  it('retains invalid scope JSON without saving it', async () => {
    const fetch = setup(); renderWithClient(<MemoryRouter><InstanceSettingsPage payload={{} as BootPayload} /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText('Authorized scope JSON'), { target: { value: '{"targets": [' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment authorizations' }));
    expect((await screen.findByRole('alert')).textContent).toContain('valid JSON');
    expect(screen.getByLabelText('Authorized scope JSON')).toHaveProperty('value', '{"targets": [');
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0);
  });
  it('keeps the draft and optimistic version after a rejected save and refresh', async () => {
    const fetch = setup(409); const { queryClient } = renderWithClient(<MemoryRouter><InstanceSettingsPage payload={{} as BootPayload} /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText('Authorization name'), { target: { value: 'Unsaved name' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment authorizations' }));
    await screen.findByText(/Concurrent update/);
    await queryClient.invalidateQueries({ queryKey: ['settings-catalog'] });
    expect(screen.getByLabelText('Authorization name')).toHaveProperty('value', 'Unsaved name');
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment authorizations' }));
    await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(2));
    for (const [, init] of fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')) expect(JSON.parse(String(init?.body)).expected_versions[key]).toBe(9);
  });
  it('respects read-only settings and provides no alternate generic editor', async () => {
    setup(200, true); renderWithClient(<MemoryRouter><InstanceSettingsPage payload={{} as BootPayload} /></MemoryRouter>);
    expect((await screen.findByLabelText('Authorization name')).matches(':disabled')).toBe(true);
    expect(screen.getByRole('button', { name: 'Save assessment authorizations' }).matches(':disabled')).toBe(true);
    expect(screen.queryByPlaceholderText('key=value')).toBeNull();
    expect(screen.getByLabelText('Assessment reference').matches(':disabled')).toBe(true);
    expect(screen.getByRole('button', { name: 'Add Targets item' }).matches(':disabled')).toBe(true);
  });
  it('requires explicit timezone-bearing validity without losing authored text', async () => {
    const fetch = setup(); renderWithClient(<MemoryRouter><InstanceSettingsPage payload={{} as BootPayload} /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText('Valid until (ISO time with timezone)'), { target: { value: '2026-12-01T00:00:00' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment authorizations' }));
    expect((await screen.findByRole('alert')).textContent).toContain('including timezones');
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0);
  });

  it.each(['cloud', 'web'])('authors a fresh %s scope and credential references without JSON', async (kind) => {
    const fetch = setup(200, false, []);
    renderWithClient(<MemoryRouter><InstanceSettingsPage payload={{} as BootPayload} /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Add authorization' }));
    const fill = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
    fill('Authorization reference', `${kind}-review`); fill('Authorization name', `${kind} review`);
    fill('Authority source', 'Explicit operator scope');
    fill('Valid from (ISO time with timezone)', '2026-01-01T00:00:00Z'); fill('Valid until (ISO time with timezone)', '2026-12-01T00:00:00Z');
    fill('Assessment reference', `${kind}-assessment`); fill('Profiles', kind === 'cloud' ? 'cloud-posture' : 'web-assessment'); fill('Actions', kind === 'cloud' ? 'cloud-posture' : 'web-passive'); fill('Evidence intent', 'Retain bounded report');
    fireEvent.click(screen.getByRole('button', { name: 'Add Targets item' }));
    if (kind === 'cloud') {
      fill('AWS account', '123456789012'); fill('Cloud provider', 'aws'); fill('AWS regions', 'us-east-1');
      fireEvent.click(screen.getByRole('button', { name: 'Add Credential grants item' }));
      fill('Secret reference', 'vault://aws-reader'); fill('Grant purpose', 'cloud_inventory'); fill('Grant account', '123456789012');
      fill('Role ARN', 'arn:aws:iam::123456789012:role/ReadOnly'); fill('Grant expiry (ISO with timezone)', '2026-11-30T00:00:00Z'); fill('Grant profiles', 'cloud-posture');
    } else {
      fill('Web origin', 'https://example.test');
      fireEvent.click(screen.getByRole('button', { name: 'Add Web routes item' })); fill('Route path', '/approved');
      const methods = screen.getByLabelText('Route methods') as HTMLSelectElement;
      methods.options[0]!.selected = true; fireEvent.change(methods);
    }
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment authorizations' }));
    await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1));
    const body = JSON.parse(String(fetch.mock.calls.find(([, init]) => init?.method === 'PATCH')![1]!.body));
    const saved = body.changes[key].authorizations[0];
    expect(saved.enabled).toBe(false);
    expect(body.expected_versions[key]).toBe(9);
    expect(saved.scope.assessmentRef).toBe(`${kind}-assessment`);
    expect(saved.scope.targets).toEqual(kind === 'cloud' ? [{ cloudAccount: '123456789012', cloudProvider: 'aws', cloudRegions: ['us-east-1'] }] : [{ webOrigin: 'https://example.test', webRoutes: [{ path: '/approved', methods: ['GET'] }] }]);
    expect(saved.scope.grants.length).toBe(kind === 'cloud' ? 1 : 0);
    if (kind === 'cloud') expect(saved.scope.grants[0].expiresAt).toBe('2026-11-30T00:00:00Z');
  });

  it('preserves old unknown scope fields while structured edits can be retried or discarded', async () => {
    const original = { ...authorization, scope: { ...authorization.scope, futureExtension: { retained: true } } };
    const fetch = setup(409, false, [original]);
    renderWithClient(<MemoryRouter><InstanceSettingsPage payload={{} as BootPayload} /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText('Evidence intent'), { target: { value: 'Revised intent' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment authorizations' }));
    await screen.findByText(/Concurrent update/);
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment authorizations' }));
    await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(2));
    for (const [, init] of fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')) {
      const body = JSON.parse(String(init?.body));
      expect(body.expected_versions[key]).toBe(9);
      expect(body.changes[key].authorizations[0].scope).toEqual({ ...original.scope, evidenceIntent: 'Revised intent' });
    }
    fireEvent.click(screen.getByRole('button', { name: 'Discard authorization changes' }));
    expect(screen.getByLabelText('Evidence intent')).toHaveProperty('value', 'Retain report');
    expect(JSON.parse((screen.getByLabelText('Authorized scope JSON') as HTMLTextAreaElement).value)).toEqual(original.scope);
  });

  it('saves explicit recurring limits and revocation without resetting aggregate scope', async () => {
    const fetch = setup(); renderWithClient(<MemoryRouter><InstanceSettingsPage payload={{} as BootPayload} /></MemoryRouter>);
    fireEvent.click(await screen.findByLabelText('Bound recurring assessments'));
    fireEvent.change(screen.getByLabelText('Maximum occurrences'), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Revoke authorization' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment authorizations' }));
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true));
    const saved = JSON.parse(String(fetch.mock.calls.find(([, init]) => init?.method === 'PATCH')![1]!.body)).changes[key].authorizations[0];
    expect(saved.enabled).toBe(false);
    expect(saved.recurring).toMatchObject({ maxOccurrences: 4, minIntervalSeconds: 86400 });
    expect(saved.scope).toEqual(authorization.scope);
    expect(saved.maxTotalJobSeconds).toBe(120);
  });

  it('renews a selected authorization without touching another record or sending before save', async () => {
    const other = { ...authorization, ref: 'other', name: 'Other authorization' };
    const fetch = setup();
    const base = fetch.getMockImplementation()!;
    fetch.mockImplementation(async (url, init) => {
      const result = await base(url, init);
      if (init?.method === 'PATCH') return result;
      const catalog = await result.json();
      if (catalog.categories) catalog.categories.Security[0].effective_value.authorizations.push(other);
      return new Response(JSON.stringify(catalog));
    });
    renderWithClient(<AssessmentAuthorizationsSettings authorizationRef="payments-review" />);
    fireEvent.change(await screen.findByLabelText('Valid until (ISO time with timezone)'), { target: { value: '2027-01-01T00:00:00Z' } });
    expect(screen.getAllByLabelText('Authorization name')).toHaveLength(1);
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment authorizations' }));
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true));
    const saved = JSON.parse(String(fetch.mock.calls.find(([, init]) => init?.method === 'PATCH')![1]!.body)).changes[key].authorizations;
    expect(saved[0]).toEqual({ ...authorization, validUntil: '2027-01-01T00:00:00Z' });
    expect(saved[1]).toEqual(other);
  });

});
