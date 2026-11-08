import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, renderWithClient, screen, waitFor } from '../../utils/test-utils';
import { MemoryRouter } from 'react-router-dom';
import { SettingsDraftGuardProvider, useSettingsDraftGuard } from './SettingsDraftGuard';
import { AssessmentEnvironmentFields, AssessmentProjectsSettings } from './AssessmentProjectsSettings';
import { GeneratedSettingsSection, type SettingsCatalogResponse } from './GeneratedSettingsSection';

const key = 'security.assessment_projects';
const environment = { id: 'prod', name: 'Production', assets: ['example.com'], exclusions: [], repositories: [{ repository: 'org/app', connectionRef: 'github-main' }], artifactRefs: [], originCandidates: [], cloudAccountCandidates: [], connectionRefs: [], credentialRefs: [], dataClassification: 'internal', frameworks: [{ id: 'soc2', edition: '2017' }], jurisdictions: ['US'], period: null, budget: { baseSeconds: 300, maxSeconds: 300, progressStallSeconds: 120 }, retentionDays: 30 };
const value = { projects: [{ id: 'payments', name: 'Payments', purpose: 'Evidence review', boundaries: 'Read only', environments: [environment] }] };
function mockCatalog(status = 200, readOnly = false) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    if (init?.method === 'PATCH') return new Response(JSON.stringify({ detail: 'Concurrent update' }), { status });
    if (String(url).includes('/audit?')) return new Response(JSON.stringify({ items: [] }));
    return new Response(JSON.stringify({ section: 'user-workspace', categories: { Security: [{ key, title: 'Assessment projects', type: 'object', ui: 'key_value', category: 'Security', source: 'instance_override', apply_mode: 'next_task', activation_state: 'active', effective_value: value, value_version: 7, read_only: readOnly, read_only_reason: 'Operator locked', audit: { store_old_value: true, store_new_value: true, redact: false }, diagnostics: [], applies_to: [], order: 1 }] } }));
  });
}
function LeaveButton() {
  const { requestDeparture } = useSettingsDraftGuard();
  return <button onClick={() => requestDeparture(() => undefined)}>Leave settings</button>;
}
afterEach(() => vi.restoreAllMocks());
describe('AssessmentProjectsSettings', () => {
  it('edits nested environment fields and saves through versioned settings', async () => {
    const fetch = mockCatalog();
    renderWithClient(<AssessmentProjectsSettings />);
    expect((await screen.findByLabelText('System name') as HTMLInputElement).value).toBe('Payments');
    expect((screen.getByLabelText('Repository') as HTMLInputElement).value).toBe('org/app');
    fireEvent.change(screen.getByLabelText('Environment name'), { target: { value: 'Staging' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment context' }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/v1/settings', expect.objectContaining({ method: 'PATCH' })));
    const request = fetch.mock.calls.find(([, init]) => init?.method === 'PATCH')![1]!;
    const body = JSON.parse(String(request.body));
    expect(body.expected_versions).toEqual({ [key]: 7 });
    expect(body.changes[key].projects[0].environments[0]).toEqual({ ...environment, name: 'Staging' });
  });
  it('retains the draft and original version after a save conflict and catalog refresh', async () => {
    const fetch = mockCatalog(409);
    const { queryClient } = renderWithClient(<AssessmentProjectsSettings />);
    await screen.findByLabelText('Environment name');
    fireEvent.change(screen.getByLabelText('Environment name'), { target: { value: 'My draft' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment context' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/409|Concurrent/);
    await queryClient.invalidateQueries({ queryKey: ['settings-catalog'] });
    const refreshed = structuredClone(queryClient.getQueryData<SettingsCatalogResponse>(['settings-catalog'])!);
    refreshed.categories.Security![0]!.value_version = 8;
    queryClient.setQueryData(['settings-catalog'], refreshed);
    expect((screen.getByLabelText('Environment name') as HTMLInputElement).value).toBe('My draft');
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment context' }));
    await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(2));
    expect(JSON.parse(String(fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')[1]![1]!.body)).expected_versions[key]).toBe(7);
  });
  it('saves explicit evidence processing limits without changing data classification', async () => {
    const fetch = mockCatalog();
    renderWithClient(<AssessmentProjectsSettings />);
    const delivery = await screen.findByLabelText('Evidence model delivery');
    fireEvent.change(delivery, { target: { value: 'local_only' } });
    fireEvent.change(screen.getByLabelText('Permitted Provider Profile references (optional)'), { target: { value: 'review-private' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment context' }));
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(true));
    const request = fetch.mock.calls.find(([, init]) => init?.method === 'PATCH')![1]!;
    const saved = JSON.parse(String(request.body)).changes[key].projects[0].environments[0];
    expect(saved.dataClassification).toBe('internal');
    expect(saved.evidenceProcessing).toEqual({ modelDelivery: 'local_only', allowedProfileRefs: ['review-private'] });
    expect(screen.getByText(/Local-only model delivery is currently unavailable/)).toBeTruthy();
  });
  it('copies systems and environments with distinct identities and shows audit', async () => {
    mockCatalog(); renderWithClient(<AssessmentProjectsSettings />);
    await screen.findByLabelText('System name');
    fireEvent.click(screen.getByRole('button', { name: 'Copy environment' }));
    expect(screen.getAllByLabelText('Environment ID').map(input => (input as HTMLInputElement).value)).toHaveLength(2);
    expect(new Set(screen.getAllByLabelText('Environment ID').map(input => (input as HTMLInputElement).value)).size).toBe(2);
    fireEvent.click(screen.getByRole('button', { name: 'Copy system' }));
    expect(new Set(screen.getAllByLabelText('System ID').map(input => (input as HTMLInputElement).value)).size).toBe(2);
    fireEvent.click(screen.getByRole('button', { name: 'View assessment audit' }));
    expect(await screen.findByText('No audit events were found for this setting.')).toBeTruthy();
  });
  it('respects operator locks and omits the duplicate generic editor', async () => {
    mockCatalog(200, true); renderWithClient(<><AssessmentProjectsSettings /><GeneratedSettingsSection /></>);
    expect((await screen.findByLabelText('System name')).matches(':disabled')).toBe(true);
    expect(screen.getByRole('button', { name: 'Save assessment context' }).matches(':disabled')).toBe(true);
    expect(screen.queryByPlaceholderText('key=value')).not.toBeTruthy();
  });
  it('guards unsaved drafts and blocks oversized saves without losing input', async () => {
    const fetch = mockCatalog();
    renderWithClient(<MemoryRouter><SettingsDraftGuardProvider><AssessmentProjectsSettings /><LeaveButton /></SettingsDraftGuardProvider></MemoryRouter>);
    await screen.findByLabelText('Purpose');
    const oversized = 'A'.repeat(17000);
    fireEvent.change(screen.getByLabelText('Purpose'), { target: { value: oversized } });
    fireEvent.click(screen.getByRole('button', { name: 'Save assessment context' }));
    expect((await screen.findByRole('alert')).textContent).toContain('16 KiB');
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(0);
    expect((screen.getByLabelText('Purpose') as HTMLInputElement).value).toBe(oversized);
    fireEvent.click(screen.getByRole('button', { name: 'Leave settings' }));
    expect(screen.getByRole('dialog')).toBeTruthy();
  });
  it('shares typed run-only fields without allowing identity changes', () => {
    const onChange = vi.fn();
    renderWithClient(<AssessmentEnvironmentFields environment={{ ...environment, dataClassification: 'internal' }} onChange={onChange} allowIdentityEdit={false} />);
    expect(screen.queryByLabelText('Environment ID')).toBeNull();
    expect(screen.getByLabelText('Evidence period start (YYYY-MM-DD)')).toHaveProperty('type', 'date');
    expect(screen.getByLabelText('Evidence period end (YYYY-MM-DD)')).toHaveProperty('type', 'date');
    fireEvent.change(screen.getByLabelText('Environment name'), { target: { value: 'Run only' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ id: 'prod', name: 'Run only' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add AWS account' }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ cloudAccountCandidates: [{ provider: 'aws', account: '', regions: [] }] }));
  });

});
