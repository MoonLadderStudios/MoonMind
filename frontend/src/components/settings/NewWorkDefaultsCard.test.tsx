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
      default_value: 'omnigent', reset_value: 'omnigent', effective_value: 'codex_cli', value_version: 3,
      options: [{ value: 'omnigent', label: 'OpenCode' }, { value: 'codex_cli', label: 'Codex' }, { value: 'claude_code', label: 'Claude Code' }] },
    { ...common, key: profileKey, title: 'Default Provider Profile', type: 'string', ui: 'provider_profile_picker',
      default_value: null, reset_value: null, effective_value: null, value_version: 4 },
  ] } };
}
let current = catalog();
let issues: Record<string, { message: string }[]> = {};
let patchResponse: Response | null = null;
let validationResponse: Response | null = null;
let validationFailure = false;
const respond = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
let fetchSpy: MockInstance<typeof window.fetch>;
function renderCard() {
  return renderWithClient(<MemoryRouter><SettingsDraftGuardProvider>
    <NewWorkDefaultsCard /><a href="/settings/instance">Instance</a>
  </SettingsDraftGuardProvider></MemoryRouter>);
}
const changeHarness = () => fireEvent.change(screen.getByLabelText('Default for new work'), { target: { value: 'runtime:claude_code' } });
const patches = () => fetchSpy.mock.calls.filter(([, init]) => init?.method === 'PATCH');
beforeEach(() => {
  current = catalog(); issues = {}; patchResponse = null; validationResponse = null; validationFailure = false;
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
    if (init?.method === 'PATCH') {
      if (patchResponse) return patchResponse;
      const changes = JSON.parse(String(init.body)).changes;
      current.categories.Workflow = current.categories.Workflow.map((d) => ({ ...d, effective_value: Object.hasOwn(changes, d.key) ? changes[d.key] : d.effective_value }));
      return respond({});
    }
    throw new Error(`Unexpected request: ${url}`);
  });
});
afterEach(() => vi.restoreAllMocks());

describe('NewWorkDefaultsCard', () => {
  it('renders the normal select without a fieldset box or default history access', async () => {
    renderCard(); const picker = await screen.findByLabelText('Default for new work');
    expect(picker.closest('fieldset')).toBeNull();
    const card = screen.getByRole('region', { name: 'Defaults for new work' });
    expect(card.textContent).not.toMatch(/history|audit/i);
    expect(within(card).getByRole('link', { name: 'Advanced default settings and reset' }).getAttribute('href')).toBe('/settings/instance');
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/audit'))).toBe(false);
  });
  it('offers one default picker and protects both values when only runtime changes', async () => {
    renderCard();
    const picker = await screen.findByLabelText('Default for new work');
    expect(screen.getAllByRole('combobox')).toHaveLength(1);
    fireEvent.change(picker, { target: { value: 'runtime:claude_code' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[0]![1]?.body))).toMatchObject({
      changes: { [runtimeKey]: 'claude_code' },
      expected_versions: { [runtimeKey]: 3, [profileKey]: 4 },
    });
  });
  it('version-checks the unchanged harness when selecting a profile', async () => {
    current.categories.Workflow[0]!.effective_value = 'claude_code';
    renderCard(); const picker = await screen.findByLabelText('Default for new work');
    await screen.findByRole('option', { name: /claude-team/ });
    fireEvent.change(picker, { target: { value: 'profile:claude-team' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[0]![1]?.body))).toMatchObject({
      changes: { [profileKey]: 'claude-team' },
      expected_versions: { [runtimeKey]: 3, [profileKey]: 4 },
    });
  });
  it('keeps a mismatched saved profile effective until explicit selection and Save', async () => {
    current.categories.Workflow[1]!.effective_value = 'claude-team';
    renderCard();
    const picker = await screen.findByLabelText('Default for new work') as HTMLSelectElement;
    await screen.findByText(/The saved profile takes priority/);
    expect(picker.value).toBe('saved:claude-team');
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(patches()).toHaveLength(0);
    fireEvent.change(picker, { target: { value: 'profile:claude-team' } });
    expect(screen.getByText('Harness: Codex → Claude Code')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(picker.value).toBe('saved:claude-team');
    fireEvent.change(picker, { target: { value: 'profile:claude-team' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[0]![1]?.body))).toMatchObject({
      changes: { [runtimeKey]: 'claude_code' },
      expected_versions: { [runtimeKey]: 3, [profileKey]: 4 },
    });
  });
  it('clears a saved profile atomically when selecting a preferred harness or OpenCode basic', async () => {
    current.categories.Workflow[1]!.effective_value = 'disabled';
    renderCard(); const picker = await screen.findByLabelText('Default for new work');
    fireEvent.change(picker, { target: { value: 'runtime:omnigent' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[0]![1]?.body)).changes).toEqual({ [runtimeKey]: 'omnigent', [profileKey]: null });
    expect((picker as HTMLSelectElement).value).toBe('runtime:omnigent');
  });
  it('preserves unknown saved runtime values and lists supported OpenCode profiles', async () => {
    current.categories.Workflow[0]!.effective_value = 'custom-runtime';
    const fallback = fetchSpy.getMockImplementation()!;
    fetchSpy.mockImplementation((input, init) => String(input) === '/api/v1/provider-profiles'
      ? Promise.resolve(respond([{ profile_id: 'opencode-local', runtime_id: 'omnigent', provider_id: 'custom', enabled: true }]))
      : fallback(input, init));
    renderCard(); const picker = await screen.findByLabelText('Default for new work') as HTMLSelectElement;
    expect(picker.value).toBe('runtime:custom-runtime');
    await screen.findByRole('option', { name: /opencode-local/ });
    fireEvent.change(picker, { target: { value: 'profile:opencode-local' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[0]![1]?.body)).changes).toEqual({ [runtimeKey]: 'omnigent', [profileKey]: 'opencode-local' });
  });
  it('guards the unchanged profile version against concurrent changes', async () => {
    renderCard(); await screen.findByLabelText('Default for new work'); changeHarness();
    current.categories.Workflow[1]!.effective_value = 'disabled';
    current.categories.Workflow[1]!.value_version = 9;
    patchResponse = respond({ error: 'version_conflict', message: 'Changed elsewhere' }, 409);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText(/Provider profile: Changed elsewhere/);
    expect(screen.queryByText(/Harness: Changed elsewhere/)).toBeNull();
    patchResponse = null;
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[1]![1]?.body)).expected_versions).toEqual({ [runtimeKey]: 3, [profileKey]: 9 });
    expect(JSON.parse(String(patches()[1]![1]?.body)).changes).toEqual({ [runtimeKey]: 'claude_code', [profileKey]: null });
  });
  it('disables editing and cancel while a Save is pending', async () => {
    renderCard(); const picker = await screen.findByLabelText('Default for new work') as HTMLSelectElement; changeHarness();
    const fallback = fetchSpy.getMockImplementation()!;
    let finishSave!: (response: Response) => void;
    fetchSpy.mockImplementation((input, init) => init?.method === 'PATCH'
      ? new Promise<Response>((resolve) => { finishSave = resolve; }) : fallback(input, init));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect((picker as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(picker, { target: { value: 'runtime:omnigent' } });
    expect(picker.value).toBe('runtime:claude_code');
    await act(async () => { finishSave(respond({})); });
    await screen.findByText('Defaults saved');
  });
  it('retains a missing saved profile through edits and lets the operator restore the pair', async () => {
    current.categories.Workflow[0]!.effective_value = 'custom-runtime';
    current.categories.Workflow[1]!.effective_value = 'gone';
    renderCard(); const picker = await screen.findByLabelText('Default for new work') as HTMLSelectElement;
    await screen.findByRole('option', { name: 'gone (not found)' });
    changeHarness();
    expect(screen.getByRole('option', { name: 'gone (not found)' })).toBeTruthy();
    expect(screen.getByRole('option', { name: 'custom-runtime (saved custom harness)' })).toBeTruthy();
    fireEvent.change(picker, { target: { value: 'profile:gone' } });
    expect(picker.value).toBe('profile:gone');
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(patches()).toHaveLength(0);
  });
  it.each(['edit', 'cancel'])('aborts and ignores stale validation after %s', async (action) => {
    renderCard(); await screen.findByLabelText('Default for new work');
    const fallback = fetchSpy.getMockImplementation()!;
    let finishValidation!: (response: Response) => void;
    let signal: AbortSignal | null | undefined;
    fetchSpy.mockImplementation((input, init) => {
      if (String(input).endsWith('/validate') && !finishValidation) {
        signal = init?.signal;
        return new Promise<Response>((resolve) => { finishValidation = resolve; });
      }
      return fallback(input, init);
    });
    changeHarness();
    await waitFor(() => expect(finishValidation).toBeDefined());
    if (action === 'cancel') fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    else fireEvent.change(screen.getByLabelText('Default for new work'), { target: { value: 'runtime:omnigent' } });
    expect(signal?.aborted).toBe(true);
    await act(async () => { finishValidation(respond({ accepted: false, issues_by_key: { [profileKey]: [{ message: 'Stale blocker' }] } })); });
    expect(screen.queryByText('Stale blocker')).toBeNull();
    expect(patches()).toHaveLength(0);
  });
  it('releases the baseline when the operator returns to the original choice', async () => {
    const { queryClient } = renderCard();
    const picker = await screen.findByLabelText('Default for new work') as HTMLSelectElement;
    changeHarness();
    fireEvent.change(picker, { target: { value: 'runtime:codex_cli' } });
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    current.categories.Workflow[0]!.effective_value = 'omnigent';
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ['settings-catalog'] }); });
    await waitFor(() => expect(picker.value).toBe('runtime:omnigent'));
  });
  it('allows a same-harness profile switch while the harness is locked', async () => {
    Object.assign(current.categories.Workflow[0]!, { read_only: true, read_only_reason: 'Harness locked' });
    current.categories.Workflow[1]!.effective_value = 'codex-a';
    const fallback = fetchSpy.getMockImplementation()!;
    fetchSpy.mockImplementation((input, init) => String(input) === '/api/v1/provider-profiles'
      ? Promise.resolve(respond(['codex-a', 'codex-b'].map((id) => ({ profile_id: id, runtime_id: 'codex_cli', enabled: true })))) : fallback(input, init));
    renderCard(); const picker = await screen.findByLabelText('Default for new work');
    const option = await screen.findByRole('option', { name: /codex-b/ }) as HTMLOptionElement;
    expect((picker as HTMLSelectElement).disabled).toBe(false);
    expect(option.disabled).toBe(false);
    fireEvent.change(picker, { target: { value: 'profile:codex-b' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[0]![1]?.body))).toMatchObject({
      changes: { [profileKey]: 'codex-b' }, expected_versions: { [runtimeKey]: 3, [profileKey]: 4 },
    });
  });
  it.each(['claude_code', 'omnigent'])('allows %s with a locked null profile', async (nextRuntime) => {
    Object.assign(current.categories.Workflow[1]!, { read_only: true, read_only_reason: 'Profile locked' });
    renderCard(); const picker = await screen.findByLabelText('Default for new work');
    expect((picker as HTMLSelectElement).disabled).toBe(false);
    const profileOption = await screen.findByRole('option', { name: /claude-team/ }) as HTMLOptionElement;
    expect(profileOption.disabled).toBe(true);
    fireEvent.change(picker, { target: { value: profileOption.value } });
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(patches()).toHaveLength(0);
    fireEvent.change(picker, { target: { value: `runtime:${nextRuntime}` } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[0]![1]?.body))).toMatchObject({
      changes: { [runtimeKey]: nextRuntime }, expected_versions: { [runtimeKey]: 3, [profileKey]: 4 },
    });
  });
  it.each([0, 1])('preserves a mismatched pair with key %i locked and permits only legal alignment', async (lockedIndex) => {
    current.categories.Workflow[1]!.effective_value = 'claude-team';
    Object.assign(current.categories.Workflow[lockedIndex]!, { read_only: true, read_only_reason: 'Deployment policy' });
    renderCard(); const picker = await screen.findByLabelText('Default for new work') as HTMLSelectElement;
    await screen.findByText(/The saved profile takes priority/);
    expect(picker.value).toBe('saved:claude-team');
    const aligned = screen.getAllByRole('option').find((option) => (option as HTMLOptionElement).value === 'profile:claude-team') as HTMLOptionElement;
    expect(aligned.disabled).toBe(lockedIndex === 0);
    fireEvent.change(picker, { target: { value: 'profile:claude-team' } });
    if (lockedIndex === 0) {
      expect(picker.value).toBe('saved:claude-team');
      expect(screen.getByText(/cannot be aligned while its setting is locked/)).toBeTruthy();
      expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
      expect(patches()).toHaveLength(0);
    } else {
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
      await screen.findByText('Defaults saved');
      expect(JSON.parse(String(patches()[0]![1]?.body))).toMatchObject({
        changes: { [runtimeKey]: 'claude_code' }, expected_versions: { [runtimeKey]: 3, [profileKey]: 4 },
      });
    }
  });
  it.each([
    { runtime: 'custom-runtime', profile: null, locked: 0, selected: 'runtime:custom-runtime', allLocked: false },
    { runtime: 'custom-runtime', profile: 'gone', locked: 1, selected: 'profile:gone', allLocked: false },
    { runtime: 'codex_cli', profile: 'disabled', locked: 0, selected: 'profile:disabled', allLocked: true },
  ])('preserves a locked retained selection $selected without impermissible edits', async ({ runtime, profile, locked, selected, allLocked }) => {
    current.categories.Workflow[0]!.effective_value = runtime;
    current.categories.Workflow[1]!.effective_value = profile;
    current.categories.Workflow[locked]!.read_only = true;
    if (allLocked) current.categories.Workflow.forEach((descriptor) => { descriptor.read_only = true; });
    renderCard(); const picker = await screen.findByLabelText('Default for new work') as HTMLSelectElement;
    await screen.findByRole('option', { name: /claude-team/ });
    expect(picker.value).toBe(selected);
    expect((picker as HTMLSelectElement).disabled).toBe(allLocked);
    const incompatible = screen.getByRole('option', { name: 'Claude Code (preferred profile)' }) as HTMLOptionElement;
    expect(incompatible.disabled).toBe(true);
    fireEvent.change(picker, { target: { value: incompatible.value } });
    expect(picker.value).toBe(selected);
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(patches()).toHaveLength(0);
    expect(screen.getByRole('link', { name: 'Advanced default settings and reset' })).toBeTruthy();
  });
  it('blocks saving a draft whose changed value becomes locked on refresh', async () => {
    const { queryClient } = renderCard();
    await screen.findByLabelText('Default for new work'); changeHarness();
    Object.assign(current.categories.Workflow[0]!, { read_only: true, read_only_reason: 'New lock' });
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ['settings-catalog'] }); });
    await screen.findByText('Harness: New lock');
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(patches()).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect((screen.getByLabelText('Default for new work') as HTMLSelectElement).value).toBe('runtime:codex_cli');
  });
  it('restores the selected runtime on retry after an unchanged runtime companion conflicts', async () => {
    current.categories.Workflow[1]!.effective_value = 'codex-a';
    const fallback = fetchSpy.getMockImplementation()!;
    fetchSpy.mockImplementation((input, init) => String(input) === '/api/v1/provider-profiles'
      ? Promise.resolve(respond(['codex-a', 'codex-b'].map((id) => ({ profile_id: id, runtime_id: 'codex_cli', enabled: true })))) : fallback(input, init));
    renderCard(); const picker = await screen.findByLabelText('Default for new work') as HTMLSelectElement;
    await screen.findByRole('option', { name: /codex-b/ });
    fireEvent.change(picker, { target: { value: 'profile:codex-b' } });
    current.categories.Workflow[0]!.effective_value = 'claude_code';
    current.categories.Workflow[0]!.value_version = 8;
    patchResponse = respond({ error: 'version_conflict', message: 'Changed elsewhere', key: runtimeKey }, 409);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText(/Harness: Changed elsewhere/);
    expect(picker.value).toBe('profile:codex-b');
    expect(JSON.parse(String(patches()[0]![1]?.body)).changes).toEqual({ [profileKey]: 'codex-b' });
    patchResponse = null;
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[1]![1]?.body))).toMatchObject({
      changes: { [runtimeKey]: 'codex_cli', [profileKey]: 'codex-b' }, expected_versions: { [runtimeKey]: 8, [profileKey]: 4 },
    });
    expect(current.categories.Workflow.map((d) => d.effective_value)).toEqual(['codex_cli', 'codex-b']);
  });
  it('recognizes a concurrently saved matching pair without a redundant retry', async () => {
    const { queryClient } = renderCard();
    const picker = await screen.findByLabelText('Default for new work') as HTMLSelectElement;
    changeHarness();
    current.categories.Workflow[0]!.effective_value = 'claude_code';
    current.categories.Workflow[0]!.value_version = 8;
    patchResponse = respond({ error: 'version_conflict', message: 'Changed elsewhere', key: runtimeKey }, 409);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Harness: Changed elsewhere to Claude Code. Your selected value is already saved.');
    expect(picker.value).toBe('runtime:claude_code');
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(patches()).toHaveLength(1);
    current.categories.Workflow[0]!.effective_value = 'omnigent';
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ['settings-catalog'] }); });
    await waitFor(() => expect(picker.value).toBe('runtime:omnigent'));
  });
  it('keeps the selected pair but blocks retry when the conflicted companion becomes locked', async () => {
    renderCard(); const picker = await screen.findByLabelText('Default for new work') as HTMLSelectElement;
    changeHarness();
    Object.assign(current.categories.Workflow[1]!, { effective_value: 'disabled', value_version: 9, read_only: true, read_only_reason: 'Profile policy changed' });
    patchResponse = respond({ error: 'version_conflict', message: 'Changed elsewhere', key: profileKey }, 409);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText(/Provider profile: Changed elsewhere/);
    expect(picker.value).toBe('runtime:claude_code');
    expect(screen.getByText('Provider profile: Profile policy changed')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(patches()).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(picker.value).toBe('profile:disabled');
  });
  it('shows only compact controls; dirty labels and Cancel restore saved values', async () => {
    renderCard();
    const harness = await screen.findByLabelText('Default for new work') as HTMLSelectElement;
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
    expect(harness.value).toBe('runtime:codex_cli');
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });
  it('saves all changed keys atomically with versions and labels, then reloads saved values and toasts', async () => {
    const view = renderCard();
    await screen.findByLabelText('Default for new work');
    await screen.findByRole('option', { name: /claude-team/ });
    changeHarness();
    fireEvent.change(screen.getByLabelText('Default for new work'), { target: { value: 'profile:claude-team' } });
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
    expect((await screen.findByLabelText('Default for new work') as HTMLSelectElement).value).toBe('profile:claude-team');
  });
  it('keeps the draft on conflict and uses refreshed versions only on another explicit Save', async () => {
    renderCard(); await screen.findByLabelText('Default for new work'); changeHarness();
    current.categories.Workflow[0]!.effective_value = 'omnigent'; current.categories.Workflow[0]!.value_version = 8;
    patchResponse = respond({ error: 'version_conflict', message: 'Changed elsewhere', key: runtimeKey }, 409);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Harness: Changed elsewhere to OpenCode. Save again to replace it, or cancel.');
    expect((screen.getByLabelText('Default for new work') as HTMLSelectElement).value).toBe('runtime:claude_code');
    expect(patches()).toHaveLength(1);
    patchResponse = null;
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[1]![1]?.body)).expected_versions).toEqual({ [runtimeKey]: 8, [profileKey]: 4 });
  });
  it.each(['omnigent', 'claude_code'])('retains the edit baseline after a background refresh to %s', async (latest) => {
    const { queryClient } = renderCard();
    await screen.findByLabelText('Default for new work'); changeHarness();
    current.categories.Workflow[0]!.effective_value = latest;
    current.categories.Workflow[0]!.value_version = 8;
    current.categories.Workflow[1]!.effective_value = 'disabled';
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ['settings-catalog'] }); });
    await waitFor(() => expect((screen.getByLabelText('Default for new work') as HTMLSelectElement).value).toBe('runtime:claude_code'));
    expect(screen.getByText('Harness: Codex → Claude Code')).toBeTruthy();
    expect((screen.getByLabelText('Default for new work') as HTMLSelectElement).value).toBe('runtime:claude_code');
    patchResponse = respond({ error: 'version_conflict', message: 'Changed elsewhere', key: runtimeKey }, 409);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    const label = latest === 'omnigent' ? 'OpenCode' : 'Claude Code';
    await screen.findByText(`Harness: Changed elsewhere to ${label}. ${latest === 'claude_code' ? 'Your selected value is already saved.' : 'Save again to replace it, or cancel.'}`);
    expect(JSON.parse(String(patches()[0]![1]?.body)).expected_versions).toEqual({ [runtimeKey]: 3, [profileKey]: 4 });
    expect(patches()).toHaveLength(1);
    patchResponse = null;
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[1]![1]?.body)).expected_versions).toEqual({ [runtimeKey]: 8, [profileKey]: 4 });
    expect(JSON.parse(String(patches()[1]![1]?.body)).changes).toEqual(latest === 'claude_code' ? { [profileKey]: null } : { [runtimeKey]: 'claude_code', [profileKey]: null });
  });
  it('keeps the version on further edits and acknowledges a validation conflict before Save', async () => {
    const { queryClient } = renderCard();
    await screen.findByLabelText('Default for new work'); changeHarness();
    current.categories.Workflow[0]!.effective_value = 'omnigent';
    current.categories.Workflow[0]!.value_version = 8;
    current.categories.Workflow[1]!.effective_value = 'disabled';
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ['settings-catalog'] }); });
    await waitFor(() => expect((screen.getByLabelText('Default for new work') as HTMLSelectElement).value).toBe('runtime:claude_code'));
    validationResponse = respond({ error: 'version_conflict', key: runtimeKey, message: 'Changed elsewhere' }, 409);
    fireEvent.change(screen.getByLabelText('Default for new work'), { target: { value: 'runtime:omnigent' } });
    await screen.findByText('Harness: Changed elsewhere to OpenCode. Your selected value is already saved.');
    const validation = fetchSpy.mock.calls.find(([url]) => String(url).endsWith('/validate'));
    expect(JSON.parse(String(validation?.[1]?.body)).expected_versions).toEqual({ [runtimeKey]: 3, [profileKey]: 4 });
    expect(patches()).toHaveLength(0);
    expect((screen.getByLabelText('Default for new work') as HTMLSelectElement).value).toBe('runtime:omnigent');
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(JSON.parse(String(patches()[0]![1]?.body)).expected_versions).toEqual({ [runtimeKey]: 8, [profileKey]: 4 });
  });
  it.each(['edit', 'cancel and restart', 'cancel'])('ignores an old validation conflict after %s during its catalog refresh', async (action) => {
    renderCard(); await screen.findByLabelText('Default for new work');
    const fallback = fetchSpy.getMockImplementation()!;
    let finishRefresh!: (response: Response) => void;
    let refreshing = false;
    fetchSpy.mockImplementation((input, init) => {
      if (String(input).startsWith('/api/v1/settings/catalog')) {
        refreshing = true;
        return new Promise<Response>((resolve) => { finishRefresh = resolve; });
      }
      return fallback(input, init);
    });
    validationResponse = respond({ error: 'version_conflict', key: runtimeKey, message: 'Changed elsewhere' }, 409);
    changeHarness();
    await waitFor(() => expect(refreshing).toBe(true));
    if (action !== 'edit') {
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      if (action === 'cancel and restart') changeHarness();
    } else {
      fireEvent.change(screen.getByLabelText('Default for new work'), { target: { value: 'runtime:omnigent' } });
    }
    current.categories.Workflow[0]!.effective_value = 'omnigent';
    current.categories.Workflow[0]!.value_version = 8;
    await act(async () => { finishRefresh(respond(current)); });
    expect(patches()).toHaveLength(0);
    if (action === 'cancel') {
      expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
      await waitFor(() => expect((screen.getByLabelText('Default for new work') as HTMLSelectElement).value).toBe('runtime:omnigent'));
    } else {
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(patches()).toHaveLength(1));
      expect(JSON.parse(String(patches()[0]![1]?.body)).expected_versions).toEqual({ [runtimeKey]: 3, [profileKey]: 4 });
    }
    expect(screen.queryByText(/Changed elsewhere to/)).toBeNull();
  });
  it('ignores a failed validation-conflict refresh after Cancel', async () => {
    renderCard(); await screen.findByLabelText('Default for new work');
    const fallback = fetchSpy.getMockImplementation()!;
    let finishRefresh!: (response: Response) => void;
    let refreshing = false;
    fetchSpy.mockImplementation((input, init) => {
      if (String(input).startsWith('/api/v1/settings/catalog')) {
        refreshing = true;
        return new Promise<Response>((resolve) => { finishRefresh = resolve; });
      }
      return fallback(input, init);
    });
    validationResponse = respond({ error: 'version_conflict', key: runtimeKey, message: 'Changed elsewhere' }, 409);
    changeHarness();
    await waitFor(() => expect(refreshing).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await act(async () => { finishRefresh(respond({ message: 'Catalog unavailable' }, 500)); });
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    await waitFor(() => expect(screen.getByRole('region', { name: 'Defaults for new work' }).textContent).toContain('Catalog unavailable'));
    expect(screen.queryByLabelText('Dashboard notifications')).toBeNull();
    expect(patches()).toHaveLength(0);
  });
  it('identifies only the conflicted key while preserving the unified draft', async () => {
    renderCard(); await screen.findByLabelText('Default for new work');
    await screen.findByRole('option', { name: /claude-team/ });
    changeHarness();
    fireEvent.change(screen.getByLabelText('Default for new work'), { target: { value: 'profile:claude-team' } });
    current.categories.Workflow[0]!.effective_value = 'omnigent';
    current.categories.Workflow[0]!.value_version = 8;
    patchResponse = respond({ error: 'version_conflict', message: 'Changed elsewhere' }, 409);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Harness: Changed elsewhere to OpenCode. Save again to replace it, or cancel.');
    expect((screen.getByLabelText('Default for new work') as HTMLSelectElement).value).toBe('profile:claude-team');
    expect(screen.getAllByText(/Changed elsewhere to/)).toHaveLength(1);
  });
  it('debounces validation, keeps issues under the picker, and does not PATCH with issues', async () => {
    issues = { [runtimeKey]: [{ message: 'Harness unavailable' }] };
    renderCard(); await screen.findByLabelText('Default for new work'); changeHarness();
    expect(fetchSpy.mock.calls.filter(([url]) => String(url).endsWith('/validate'))).toHaveLength(0);
    const issue = await screen.findByText('Harness unavailable');
    expect(within(issue.closest('[data-setting-row]') as HTMLElement).getByLabelText('Default for new work')).toBeTruthy();
    const validation = fetchSpy.mock.calls.find(([url]) => String(url).endsWith('/validate'));
    expect(JSON.parse(String(validation?.[1]?.body))).toEqual({ changes: { [runtimeKey]: 'claude_code' }, expected_versions: { [runtimeKey]: 3, [profileKey]: 4 } });
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(patches()).toHaveLength(0);
  });
  it.each(offCardSettings)('labels off-card validation for $key without exposing the key or silently PATCHing', async ({ key, title, inCatalog }) => {
    if (inCatalog) current.categories.Workflow.push({ ...current.categories.Workflow[0]!, key, title });
    issues = { [key]: [{ message: `${key} is unavailable` }] };
    renderCard(); await screen.findByLabelText('Default for new work'); changeHarness();
    const error = await screen.findByText(`${title}: ${title} is unavailable`);
    expect(error.closest('[aria-label="Defaults for new work"]')).toBeTruthy();
    expect(error.closest('[data-setting-row]')).toBeNull();
    expect(within(error).getByRole('link', { name: 'View Instance settings' }).getAttribute('href')).toBe('/settings/instance');
    expect(screen.getByRole('region', { name: 'Defaults for new work' }).textContent).not.toContain(key);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(patches()).toHaveLength(0);
    expect((screen.getByLabelText('Default for new work') as HTMLSelectElement).value).toBe('runtime:claude_code');
  });
  it.each(offCardSettings.flatMap((setting) => [400, 423].map((status) => ({ ...setting, status }))))('labels off-card PATCH errors for $key and HTTP $status', async ({ key, title, inCatalog, status }) => {
    if (inCatalog) current.categories.Workflow.push({ ...current.categories.Workflow[0]!, key, title });
    renderCard(); await screen.findByLabelText('Default for new work'); changeHarness();
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
    expect((screen.getByLabelText('Default for new work') as HTMLSelectElement).value).toBe('runtime:claude_code');
  });
  it.each([400, 423])('keeps the server row error inline for HTTP %i', async (status) => {
    renderCard(); await screen.findByLabelText('Default for new work'); changeHarness();
    patchResponse = respond({ error: 'operator_locked', key: runtimeKey, message: `${runtimeKey} is locked by deployment` }, status);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    const error = await screen.findByText('Harness is locked by deployment');
    expect(within(error.closest('[data-setting-row]') as HTMLElement).getByLabelText('Default for new work')).toBeTruthy();
    expect((screen.getByLabelText('Default for new work') as HTMLSelectElement).value).toBe('runtime:claude_code');
    expect(error.textContent).not.toContain(runtimeKey);
  });
  it('toasts an unavailable validation probe and still lets the backend validate Save', async () => {
    validationFailure = true;
    renderCard(); await screen.findByLabelText('Default for new work'); changeHarness();
    const error = await screen.findByText('Validation storage unavailable');
    expect(error.closest('[aria-label="Dashboard notifications"]')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Defaults saved');
    expect(patches()).toHaveLength(1);
  });
  it('shows other server errors as toasts', async () => {
    renderCard(); await screen.findByLabelText('Default for new work'); changeHarness();
    patchResponse = respond({ message: 'Settings storage unavailable' }, 500);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    const toast = await screen.findByText('Settings storage unavailable');
    expect(toast.closest('[aria-label="Dashboard notifications"]')).toBeTruthy();
  });
  it('links to advanced defaults and reset without offering a non-atomic reset', async () => {
    renderCard(); await screen.findByLabelText('Default for new work');
    expect(screen.getByRole('link', { name: 'Advanced default settings and reset' }).getAttribute('href')).toBe('/settings/instance');
    expect(screen.queryByRole('button', { name: /actions/ })).toBeNull();
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
  });
  it('protects a dirty draft on departure', async () => {
    renderCard(); await screen.findByLabelText('Default for new work'); changeHarness();
    fireEvent.click(screen.getByRole('link', { name: 'Instance' }));
    expect(screen.getByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
    expect((screen.getByLabelText('Default for new work') as HTMLSelectElement).value).toBe('runtime:claude_code');
  });
  it.each([0, 1])('renders read-only values and reasons for key %i, and keeps disabled/missing profile choices visible', async (index) => {
    Object.assign(current.categories.Workflow[index]!, { read_only: true, read_only_reason: 'Deployment policy' });
    current.categories.Workflow[1]!.effective_value = 'gone';
    renderCard();
    await screen.findByText(/Deployment policy/);
    expect((screen.getByRole('combobox', { name: 'Default for new work' }) as HTMLSelectElement).disabled).toBe(false);
    await screen.findByRole('option', { name: 'gone (not found)' });
    expect(screen.getByRole('option', { name: /disabled.*disabled/ })).toBeTruthy();
  });
});
