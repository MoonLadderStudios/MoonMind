import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithClient, screen, waitFor } from '../../utils/test-utils';
import { InstanceSettingsPage } from '../../entrypoints/settings';
import type { BootPayload } from '../../boot/parseBootPayload';

const key = 'security.assessment_authorizations';
const authorization = { ref: 'payments-review', name: 'Payments', enabled: true, generation: 'generation-7', authorityKind: 'client_engagement', authoritySource: 'Signed scope reference', validFrom: '2026-01-01T00:00:00Z', validUntil: '2026-12-01T00:00:00Z', maxTotalJobSeconds: 120, scope: { assessmentRef: 'payments', profileRefs: ['scanner@1'], actionClasses: ['passive'], targets: [{ workspacePaths: ['.'] }], maxJobs: 2, maxJobSeconds: 60, evidenceIntent: 'Retain report' } };
function setup(status = 200, readOnly = false) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    if (init?.method === 'PATCH') return new Response(JSON.stringify({ detail: 'Concurrent update' }), { status });
    if (String(url).includes('/audit?')) return new Response(JSON.stringify({ items: [] }));
    return new Response(JSON.stringify({ categories: { Security: [{ key, title: 'Assessment authorizations', type: 'object', ui: 'key_value', category: 'Security', effective_value: { authorizations: [authorization] }, value_version: 9, read_only: readOnly, audit: { store_old_value: true, store_new_value: true, redact: false }, diagnostics: [], applies_to: [], order: 1 }] } }));
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
  });
  it('requires explicit timezone-bearing validity without losing authored text', async () => {
    const fetch = setup(); renderWithClient(<MemoryRouter><InstanceSettingsPage payload={{} as BootPayload} /></MemoryRouter>);
    fireEvent.change(await screen.findByLabelText('Valid until (ISO time with timezone)'), { target: { value: '2026-12-01T00:00:00' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment authorizations' }));
    expect((await screen.findByRole('alert')).textContent).toContain('including timezones');
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0);
  });

});
