import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { act, fireEvent, renderWithClient, screen, waitFor, within } from '../../utils/test-utils';
import { SettingsDraftGuardProvider } from './SettingsDraftGuard';
import { NewWorkDefaultsCard } from './NewWorkDefaultsCard';
import type { SettingDescriptor } from './GeneratedSettingsSection';

const runtimeKey = 'workflow.default_runtime';
const profileKey = 'workflow.default_provider_profile_ref';
const offCardSettings = [
  { key: 'workflow.default_publish_mode', title: 'Publish mode', inCatalog: true },
  { key: 'integrations.github.token_ref', title: 'Other setting', inCatalog: false },
];
function catalog(): { section: string; categories: { Workflow: SettingDescriptor[] } } {
  const common = {
    category: 'Workflow', section: 'user-workspace', source: 'instance_override', source_explanation: 'Instance override',
    apply_mode: 'next_workflow', activation_state: 'active', active: true,
    pending_value: null, completion_guidance: 'New workflows will use this value', applies_to: ['workflows'],
    read_only: false, sensitive: false, requires_reload: false, requires_worker_restart: false,
    requires_process_restart: false, audit: { store_old_value: true, store_new_value: true, redact: false },
    diagnostics: [], order: 1,
  };
  return { section: 'user-workspace', categories: { Workflow: [
    { ...common, key: runtimeKey, title: 'Default Harness', type: 'enum', ui: 'select',
      default_value: 'omnigent', effective_value: 'codex_cli', value_version: 3,
      options: [{ value: 'omnigent', label: 'OpenCode' }, { value: 'codex_cli', label: 'Codex' }, { value: 'claude_code', label: 'Claude Code' }] },
    { ...common, key: profileKey, title: 'Default Provider Profile', type: 'string', ui: 'provider_profile_picker',
      default_value: null, effective_value: null, value_version: 4 },
  ] } };
}
let current = catalog();
let issues: Record<string, { message: string }[]> = {};
let patchResponse: Response | null = null;
let validationResponse: Response | null = null;
let validationFailure = false;
let inheritedValues: Record<string, unknown>;
const respond = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
let fetchSpy: MockInstance<typeof window.fetch>;
function renderCard() {
  return renderWithClient(<MemoryRouter><SettingsDraftGuardProvider>
    <NewWorkDefaultsCard /><a href="/settings/instance">Instance</a>
  </SettingsDraftGuardProvider></MemoryRouter>);
}
const changeHarness = () => fireEvent.change(screen.getByLabelText('Harness'), { target: { value: 'claude_code' } });
const patches = () => fetchSpy.mock.calls.filter(([, init]) => init?.method === 'PATCH');
beforeEach(() => {
  current = catalog(); issues = {}; patchResponse = null; validationResponse = null; validationFailure = false;
  inheritedValues = { [runtimeKey]: 'omnigent', [profileKey]: null };
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.startsWith('/api/v1/settings/catalog')) return respond(current);
    if (url === '/api/v1/provider-profiles') return respond([
      { profile_id: 'claude-team', runtime_id: 'claude_code', provider_id: 'anthropic', provider_label: 'Anthropic', enabled: true },
      { profile_id: 'disabled', runtime_id: 'codex_cli', provider_id: 'openai', enabled: false },
    ]);
    if (url === '/api/v1/settings/validate' && validationFailure) return respond({ message: 'Validation storage unavailable' }, 500);
    if (url === '/api/v1/settings/validate' && validationResponse) {
      const response = validationResponse;
      validationResponse = null;
      return response;
    }
    if (url === '/api/v1/settings/validate') return respond({ accepted: Object.keys(issues).length === 0, issues_by_key: issues });
    if (url.startsWith('/api/v1/settings/audit')) return respond({ items: [] });
    if (init?.method === 'DELETE') {
      const key = decodeURIComponent(url.split('/').at(-1)!);
      const descriptor = current.categories.Workflow.find((item) => item.key === key)!;
      descriptor.effective_value = inheritedValues[key];
      descriptor.source = 'config_file';
      return respond({});
    }
    if (init?.method === 'PATCH') {
      if (patchResponse) return patchResponse;
      const changes = JSON.parse(String(init.body)).changes;
      current.categories.Workflow = current.categories.Workflow.map((d) => ({ ...d, effective_value: changes[d.key] ?? d.effective_value }));
      return respond({});
    }
    throw new Error(`Unexpected request: ${url}`);
  });
});
afterEach(() => vi.restoreAllMocks());

describe('NewWorkDefaultsCard', () => {
  it('shows only compact controls; dirty labels and Cancel restore saved values', async () => {
    renderCard();
    const harness = await screen.findByLabelText('Harness') as HTMLSelectElement;
    const card = screen.getByRole('region', { name: 'Defaults for new work' });
    expect(card.id).toBe('new-work-defaults');
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(card.textContent).toContain("Used when a launch doesn't choose a harness or profile.");
    for (const copy of ['Instance override', 'Pending', 'Affects', 'New workflows will use this value', 'Details', runtimeKey, profileKey]) {
      expect(card.textContent).not.toContain(copy);
    }
    changeHarness();
    expect(screen.getByText('Harness: Codex → Claude Code')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(harness.value).toBe('codex_cli');
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });
  it('saves all changed keys atomically with versions and labels, then reloads saved values and toasts', async () => {
    const view = renderCard();
    await screen.findByLabelText('Harness');
    await screen.findByRole('option', { name: /claude-team/ });
    changeHarness();
    fireEvent.change(screen.getByLabelText('Provider profile'), { target: { value: 'claude-team' } });
    expect(screen.getByText(/Provider profile: Not set → claude-team.*Claude Code/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(patches()).toHaveLength(1);
    expect(JSON.parse(String(patches()[0]![1]?.body))).toEqual({
      changes: { [runtimeKey]: 'claude_code', [profileKey]: 'claude-team' },
      expected_versions: { [runtimeKey]: 3, [profileKey]: 4 }, reason: 'Updated from Providers & Secrets.',
    });
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    view.unmount(); renderCard();
    expect((await screen.findByLabelText('Harness') as HTMLSelectElement).value).toBe('claude_code');
  });
  it('keeps the draft on conflict and uses refreshed versions only on another explicit Save', async () => {
    renderCard(); await screen.findByLabelText('Harness'); changeHarness();
    current.categories.Workflow[0]!.effective_value = 'omnigent'; current.categories.Workflow[0]!.value_version = 8;
    patchResponse = respond({ error: 'version_conflict', message: 'Changed elsewhere', key: runtimeKey }, 409);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Changed elsewhere to OpenCode. Save again to replace it, or cancel.');
    expect((screen.getByLabelText('Harness') as HTMLSelectElement).value).toBe('claude_code');
    expect(patches()).toHaveLength(1);
    patchResponse = null;
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[1]![1]?.body)).expected_versions).toEqual({ [runtimeKey]: 8 });
  });
  it.each(['omnigent', 'claude_code'])('retains the edit baseline after a background refresh to %s', async (latest) => {
    const { queryClient } = renderCard();
    await screen.findByLabelText('Harness'); changeHarness();
    current.categories.Workflow[0]!.effective_value = latest;
    current.categories.Workflow[0]!.value_version = 8;
    current.categories.Workflow[1]!.effective_value = 'disabled';
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ['settings-catalog'] }); });
    await waitFor(() => expect((screen.getByLabelText('Provider profile') as HTMLSelectElement).value).toBe('disabled'));
    expect(screen.getByText('Harness: Codex → Claude Code')).toBeTruthy();
    expect((screen.getByLabelText('Harness') as HTMLSelectElement).value).toBe('claude_code');
    patchResponse = respond({ error: 'version_conflict', message: 'Changed elsewhere', key: runtimeKey }, 409);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    const label = latest === 'omnigent' ? 'OpenCode' : 'Claude Code';
    await screen.findByText(`Changed elsewhere to ${label}. Save again to replace it, or cancel.`);
    expect(JSON.parse(String(patches()[0]![1]?.body)).expected_versions).toEqual({ [runtimeKey]: 3 });
    expect(patches()).toHaveLength(1);
    patchResponse = null;
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[1]![1]?.body)).expected_versions).toEqual({ [runtimeKey]: 8 });
  });
  it('keeps the version on further edits and acknowledges a validation conflict before Save', async () => {
    const { queryClient } = renderCard();
    await screen.findByLabelText('Harness'); changeHarness();
    current.categories.Workflow[0]!.effective_value = 'omnigent';
    current.categories.Workflow[0]!.value_version = 8;
    current.categories.Workflow[1]!.effective_value = 'disabled';
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ['settings-catalog'] }); });
    await waitFor(() => expect((screen.getByLabelText('Provider profile') as HTMLSelectElement).value).toBe('disabled'));
    validationResponse = respond({ error: 'version_conflict', key: runtimeKey, message: 'Changed elsewhere' }, 409);
    fireEvent.change(screen.getByLabelText('Harness'), { target: { value: 'omnigent' } });
    await screen.findByText('Changed elsewhere to OpenCode. Save again to replace it, or cancel.');
    const validation = fetchSpy.mock.calls.find(([url]) => String(url).endsWith('/validate'));
    expect(JSON.parse(String(validation?.[1]?.body)).expected_versions).toEqual({ [runtimeKey]: 3 });
    expect(patches()).toHaveLength(0);
    expect((screen.getByLabelText('Harness') as HTMLSelectElement).value).toBe('omnigent');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[0]![1]?.body)).expected_versions).toEqual({ [runtimeKey]: 8 });
  });
  it('identifies only the conflicted row while preserving every draft', async () => {
    renderCard(); await screen.findByLabelText('Harness');
    await screen.findByRole('option', { name: /claude-team/ });
    changeHarness();
    fireEvent.change(screen.getByLabelText('Provider profile'), { target: { value: 'claude-team' } });
    current.categories.Workflow[0]!.effective_value = 'omnigent';
    current.categories.Workflow[0]!.value_version = 8;
    patchResponse = respond({ error: 'version_conflict', message: 'Changed elsewhere' }, 409);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Changed elsewhere to OpenCode. Save again to replace it, or cancel.');
    const profileRow = screen.getByLabelText('Provider profile').closest('[data-setting-row]')!;
    expect(profileRow.textContent).not.toContain('Changed elsewhere');
    expect((screen.getByLabelText('Provider profile') as HTMLSelectElement).value).toBe('claude-team');
  });
  it('debounces validation, keeps issues under their row, and does not PATCH with issues', async () => {
    issues = { [runtimeKey]: [{ message: 'Harness unavailable' }] };
    renderCard(); await screen.findByLabelText('Harness'); changeHarness();
    expect(fetchSpy.mock.calls.filter(([url]) => String(url).endsWith('/validate'))).toHaveLength(0);
    const issue = await screen.findByText('Harness unavailable');
    expect(within(issue.closest('[data-setting-row]') as HTMLElement).getByLabelText('Harness')).toBeTruthy();
    const validation = fetchSpy.mock.calls.find(([url]) => String(url).endsWith('/validate'));
    expect(JSON.parse(String(validation?.[1]?.body))).toEqual({ changes: { [runtimeKey]: 'claude_code' }, expected_versions: { [runtimeKey]: 3 } });
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(patches()).toHaveLength(0);
  });
  it.each(offCardSettings)('labels off-card validation for $key without exposing the key or silently PATCHing', async ({ key, title, inCatalog }) => {
    if (inCatalog) current.categories.Workflow.push({ ...current.categories.Workflow[0]!, key, title });
    issues = { [key]: [{ message: `${key} is unavailable` }] };
    renderCard(); await screen.findByLabelText('Harness'); changeHarness();
    const error = await screen.findByText(`${title}: ${title} is unavailable`);
    expect(error.closest('[aria-label="Defaults for new work"]')).toBeTruthy();
    expect(error.closest('[data-setting-row]')).toBeNull();
    expect(within(error).getByRole('link', { name: 'View Instance settings' }).getAttribute('href')).toBe('/settings/instance');
    expect(screen.getByRole('region', { name: 'Defaults for new work' }).textContent).not.toContain(key);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(patches()).toHaveLength(0);
    expect((screen.getByLabelText('Harness') as HTMLSelectElement).value).toBe('claude_code');
  });
  it.each(offCardSettings.flatMap((setting) => [400, 423].map((status) => ({ ...setting, status }))))('labels off-card PATCH errors for $key and HTTP $status', async ({ key, title, inCatalog, status }) => {
    if (inCatalog) current.categories.Workflow.push({ ...current.categories.Workflow[0]!, key, title });
    renderCard(); await screen.findByLabelText('Harness'); changeHarness();
    patchResponse = respond(status === 400 ? {
      error: 'validation_failed', message: 'Invalid settings', details: { issues: [
        { key, message: 'Managed secret is missing' },
        { key: runtimeKey, message: 'Harness is unavailable' },
      ] },
    } : { key, message: `${key} is locked by deployment` }, status);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    const error = await screen.findByText(`${title}: ${status === 400 ? 'Managed secret is missing' : `${title} is locked by deployment`}`);
    expect(error.closest('[aria-label="Defaults for new work"]')).toBeTruthy();
    expect(error.closest('[data-setting-row]')).toBeNull();
    expect(within(error).getByRole('link', { name: 'View Instance settings' }).getAttribute('href')).toBe('/settings/instance');
    expect(screen.getByRole('region', { name: 'Defaults for new work' }).textContent).not.toContain(key);
    expect(patches()).toHaveLength(1);
    if (status === 400) {
      expect(screen.getByText('Harness is unavailable').closest('[data-setting-row]')).toBeTruthy();
    }
    expect((screen.getByLabelText('Harness') as HTMLSelectElement).value).toBe('claude_code');
  });
  it.each([400, 423])('keeps the server row error inline for HTTP %i', async (status) => {
    renderCard(); await screen.findByLabelText('Harness'); changeHarness();
    patchResponse = respond({ error: 'operator_locked', key: runtimeKey, message: `${runtimeKey} is locked by deployment` }, status);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    const error = await screen.findByText('Harness is locked by deployment');
    expect(within(error.closest('[data-setting-row]') as HTMLElement).getByLabelText('Harness')).toBeTruthy();
    expect((screen.getByLabelText('Harness') as HTMLSelectElement).value).toBe('claude_code');
    expect(error.textContent).not.toContain(runtimeKey);
  });
  it('toasts an unavailable validation probe and still lets the backend validate Save', async () => {
    validationFailure = true;
    renderCard(); await screen.findByLabelText('Harness'); changeHarness();
    const error = await screen.findByText('Validation storage unavailable');
    expect(error.closest('[aria-label="Dashboard notifications"]')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(patches()).toHaveLength(1);
  });
  it('shows other server errors as toasts', async () => {
    renderCard(); await screen.findByLabelText('Harness'); changeHarness();
    patchResponse = respond({ message: 'Settings storage unavailable' }, 500);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    const toast = await screen.findByText('Settings storage unavailable');
    expect(toast.closest('[aria-label="Dashboard notifications"]')).toBeTruthy();
  });
  it('resets immediately only for overrides, keeps the other draft and shares history rendering', async () => {
    current.categories.Workflow[1]!.source = 'config_file';
    renderCard(); await screen.findByLabelText('Harness');
    fireEvent.change(screen.getByLabelText('Provider profile'), { target: { value: 'claude-team' } });
    fireEvent.click(screen.getByRole('button', { name: 'Provider profile actions' }));
    expect(screen.queryByRole('menuitem', { name: /Reset to default/ })).toBeNull();
    fireEvent.click(screen.getByRole('menuitem', { name: 'View history' }));
    await screen.findByText('No audit events were found for this setting.');
    changeHarness();
    fireEvent.click(screen.getByRole('button', { name: 'Harness actions' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Reset to default' }));
    await screen.findByText('Harness reset to default');
    expect(fetchSpy.mock.calls.filter(([, init]) => init?.method === 'DELETE').map(([url]) => url)).toEqual([`/api/v1/settings/${runtimeKey}`]);
    expect((screen.getByLabelText('Provider profile') as HTMLSelectElement).value).toBe('claude-team');
    expect((screen.getByLabelText('Harness') as HTMLSelectElement).value).toBe('omnigent');
  });
  it.each([
    { key: runtimeKey, title: 'Harness', inherited: 'claude_code', proposed: 'omnigent' },
    { key: profileKey, title: 'Provider profile', inherited: 'claude-team', proposed: 'disabled' },
  ])('resets $title to inherited configuration without advertising the registry default', async ({ key, title, inherited, proposed }) => {
    inheritedValues[key] = inherited;
    renderCard(); await screen.findByLabelText('Harness');
    await screen.findByRole('option', { name: /claude-team/ });
    fireEvent.change(screen.getByLabelText(title), { target: { value: proposed } });
    fireEvent.click(screen.getByRole('button', { name: `${title} actions` }));
    const reset = screen.getByRole('menuitem', { name: 'Reset to default' });
    fireEvent.click(reset);
    await screen.findByText(`${title} reset to default`);
    expect(fetchSpy.mock.calls.filter(([, init]) => init?.method === 'DELETE').map(([url]) => url)).toEqual([`/api/v1/settings/${key}`]);
    expect((screen.getByLabelText(title) as HTMLSelectElement).value).toBe(inherited);
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });
  it('protects a dirty draft on departure', async () => {
    renderCard(); await screen.findByLabelText('Harness'); changeHarness();
    fireEvent.click(screen.getByRole('link', { name: 'Instance' }));
    expect(screen.getByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
    expect((screen.getByLabelText('Harness') as HTMLSelectElement).value).toBe('claude_code');
  });
  it('renders read-only values and reasons, and keeps disabled/missing profile choices visible', async () => {
    Object.assign(current.categories.Workflow[0]!, { read_only: true, read_only_reason: 'Deployment policy' });
    current.categories.Workflow[1]!.effective_value = 'gone';
    renderCard();
    await screen.findByText('Deployment policy');
    expect(screen.queryByRole('combobox', { name: 'Harness' })).toBeNull();
    await screen.findByRole('option', { name: 'gone (not found)' });
    expect(screen.getByRole('option', { name: /disabled.*disabled/ })).toBeTruthy();
  });
});
