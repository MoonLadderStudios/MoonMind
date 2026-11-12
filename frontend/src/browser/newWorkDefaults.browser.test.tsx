import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { MemoryRouter } from 'react-router-dom';
import { NewWorkDefaultsCard } from '../components/settings/NewWorkDefaultsCard';
import { SettingsDraftGuardProvider } from '../components/settings/SettingsDraftGuard';
import { cleanup, renderWithClient, screen, waitFor } from '../utils/test-utils';
import '../styles/dashboard.css';

const runtimeKey = 'workflow.default_runtime';
const profileKey = 'workflow.default_provider_profile_ref';
const respond = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
let shell: HTMLElement;
let panel: HTMLElement;
let finishSave: ((response: Response) => void) | undefined;

beforeEach(() => {
  document.body.style.margin = '0';
  document.documentElement.classList.add('dark');
  shell = document.createElement('main');
  shell.className = 'dashboard-root';
  const content = document.createElement('div');
  content.className = 'dashboard-content';
  panel = document.createElement('section');
  panel.className = 'panel panel--data-wide';
  content.appendChild(panel); shell.appendChild(content); document.body.appendChild(shell);
  finishSave = undefined;
  vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/settings/catalog')) return respond({ categories: { Workflow: [
      { key: runtimeKey, title: 'Default Harness', effective_value: 'codex_cli', value_version: 3, ui: 'select', options: [
        { value: 'omnigent', label: 'OpenCode' }, { value: 'codex_cli', label: 'Codex' }, { value: 'claude_code', label: 'Claude Code' },
      ] },
      { key: profileKey, title: 'Default Provider Profile', effective_value: null, value_version: 4, ui: 'provider_profile_picker' },
    ] } });
    if (url === '/api/v1/provider-profiles') return respond([{ profile_id: 'codex-team', runtime_id: 'codex_cli', provider_id: 'openai', enabled: true }]);
    if (url.endsWith('/validate')) return respond({ accepted: true, issues_by_key: {} });
    if (init?.method === 'PATCH') return new Promise<Response>((resolve) => { finishSave = resolve; });
    throw new Error(`Unexpected request ${url}`);
  });
});

afterEach(() => {
  cleanup(); shell.remove(); document.documentElement.classList.remove('dark');
  vi.restoreAllMocks();
});

describe('new-work default control presentation', () => {
  it.each([1280, 390])('keeps the rounded select without a second border at %i px, including Save', async (width) => {
    await page.viewport(width, 850);
    renderWithClient(<MemoryRouter><SettingsDraftGuardProvider><NewWorkDefaultsCard /></SettingsDraftGuardProvider></MemoryRouter>, { container: panel });
    const picker = await screen.findByLabelText('Default for new work') as HTMLSelectElement;
    const card = screen.getByRole('region', { name: 'Defaults for new work' });
    expect(picker.closest('fieldset')).toBeNull();
    expect(getComputedStyle(picker).borderTopWidth).toBe('1px');
    expect(parseFloat(getComputedStyle(picker).borderTopLeftRadius)).toBeGreaterThan(0);
    for (let wrapper = picker.parentElement; wrapper && wrapper !== card; wrapper = wrapper.parentElement) {
      expect(parseFloat(getComputedStyle(wrapper).borderTopWidth)).toBe(0);
      expect(parseFloat(getComputedStyle(wrapper).borderLeftWidth)).toBe(0);
    }
    expect(picker.getBoundingClientRect().right).toBeLessThanOrEqual(card.getBoundingClientRect().right);
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
    expect(card.textContent).not.toMatch(/history|audit/i);
    await page.screenshot({ path: `../../../artifacts/new-work-default-${width}.png` });
    await userEvent.selectOptions(picker, 'runtime:claude_code');
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await waitFor(() => expect(finishSave).toBeDefined());
    expect(picker.disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(true);
    await page.screenshot({ path: `../../../artifacts/new-work-default-${width}-saving.png` });
    finishSave!(respond({}));
    await screen.findByText('Defaults saved');
  });
});
