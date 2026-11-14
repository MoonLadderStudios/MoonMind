import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { MemoryRouter } from 'react-router-dom';
import { NewWorkDefaultsCard } from '../components/settings/NewWorkDefaultsCard';
import { GeneratedSettingsSection, type SettingDescriptor } from '../components/settings/GeneratedSettingsSection';
import { SettingsDraftGuardProvider } from '../components/settings/SettingsDraftGuard';
import { cleanup, renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import '../styles/dashboard.css';

const runtimeKey = 'workflow.default_runtime';
const profileKey = 'workflow.default_provider_profile_ref';
const publishKey = 'workflow.default_publish_mode';
let descriptors: SettingDescriptor[];
let requests: Array<{ url: string; method: string }>;
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
  requests = [];
  const common = {
    category: 'Workflow', section: 'user-workspace', source: 'instance_override', source_explanation: 'Instance override',
    apply_mode: 'next_workflow', activation_state: 'active', active: true, pending_value: null,
    completion_guidance: 'New workflows will use this value', applies_to: ['workflows'], read_only: false,
    sensitive: false, requires_reload: false, requires_worker_restart: false, requires_process_restart: false,
    audit: { store_old_value: true, store_new_value: true, redact: false }, diagnostics: [], order: 1,
  };
  descriptors = [
    { ...common, key: runtimeKey, title: 'Default Harness', effective_value: 'codex_cli', reset_value: 'omnigent', value_version: 3, type: 'enum', ui: 'select', options: [
      { value: 'omnigent', label: 'OpenCode' }, { value: 'codex_cli', label: 'Codex' }, { value: 'claude_code', label: 'Claude Code' },
    ] },
    { ...common, key: profileKey, title: 'Default Provider Profile', effective_value: null, reset_value: null, value_version: 4, type: 'string', ui: 'provider_profile_picker' },
    { ...common, key: publishKey, title: 'Default Publish Mode', effective_value: 'pr', reset_value: 'none', value_version: 2, type: 'enum', ui: 'select', options: [
      { value: 'none', label: 'None' }, { value: 'pr', label: 'Pull request' },
    ] },
  ];
  vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    requests.push({ url, method: init?.method ?? 'GET' });
    if (url.includes('/settings/catalog')) return respond({ section: 'user-workspace', categories: { Workflow: descriptors } });
    if (url === '/api/v1/provider-profiles') return respond([{ profile_id: 'codex-team', runtime_id: 'codex_cli', provider_id: 'openai', enabled: true }]);
    if (url.endsWith('/validate')) return respond({ accepted: true, issues_by_key: {} });
    if (url.includes('/settings/audit')) return respond({ items: [] });
    if (init?.method === 'DELETE') {
      const key = decodeURIComponent(url.split('/').at(-1)!);
      descriptors = descriptors.map((descriptor) => descriptor.key === key
        ? { ...descriptor, effective_value: descriptor.reset_value, source: 'config_file', value_version: descriptor.value_version + 1 }
        : descriptor);
      return respond({});
    }
    if (init?.method === 'PATCH') return new Promise<Response>((resolve) => {
      const changes = JSON.parse(String(init.body)).changes as Record<string, unknown>;
      finishSave = (response) => {
        descriptors = descriptors.map((descriptor) => Object.hasOwn(changes, descriptor.key)
          ? { ...descriptor, effective_value: changes[descriptor.key], value_version: descriptor.value_version + 1 }
          : descriptor);
        resolve(response);
      };
    });
    throw new Error(`Unexpected request ${url}`);
  });
});

afterEach(() => {
  cleanup(); shell.remove(); document.documentElement.classList.remove('dark');
  vi.restoreAllMocks();
});

// Keep these soft so an expected-red layout run still captures both the initial
// and pending-Save states rather than stopping before the most cramped state.
function expectReadableMobilePicker(picker: HTMLSelectElement, state: string) {
  const row = picker.parentElement!;
  const rowStyle = getComputedStyle(row);
  const rowRect = row.getBoundingClientRect();
  const rect = picker.getBoundingClientRect();
  const leftInset = parseFloat(rowStyle.borderLeftWidth) + parseFloat(rowStyle.paddingLeft);
  const rightInset = parseFloat(rowStyle.borderRightWidth) + parseFloat(rowStyle.paddingRight);
  const available = rowRect.width - leftInset - rightInset;
  expect.soft(rect.width, `${state}: picker spans the available mobile row`).toBeGreaterThanOrEqual(available - 1.5);
  expect.soft(Math.abs(rect.left - rowRect.left - leftInset), `${state}: picker starts at the row's content edge`).toBeLessThanOrEqual(1.5);
  const style = getComputedStyle(picker);
  const textWidth = rect.width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)
    - parseFloat(style.borderLeftWidth) - parseFloat(style.borderRightWidth) - 24; // native dropdown indicator
  const context = document.createElement('canvas').getContext('2d')!;
  context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  const harnessName = picker.selectedOptions[0]!.textContent!.trim().split(' (')[0]!;
  expect.soft(textWidth, `${state}: selected harness name ${harnessName} remains readable`).toBeGreaterThanOrEqual(context.measureText(harnessName).width);
  for (const button of row.querySelectorAll('button')) {
    expect.soft(button.getBoundingClientRect().top, `${state}: actions do not compete with the mobile picker row`).toBeGreaterThanOrEqual(rect.bottom - 1.5);
  }
}

async function captureVisibleRegion(element: HTMLElement, filename: string) {
  // The production dashboard scroll containers remain intact. Capture a row or
  // audit panel only after scrolling it into view, not an oversized region that
  // crosses clipped scrollports and produces black or truncated screenshots.
  element.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
  await waitFor(() => {
    const rect = element.getBoundingClientRect();
    let top = 0; let bottom = window.innerHeight; let left = 0; let right = window.innerWidth;
    for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor);
      const bounds = ancestor.getBoundingClientRect();
      if (/^(auto|scroll|hidden|clip)$/.test(style.overflowY)) {
        top = Math.max(top, bounds.top + ancestor.clientTop);
        bottom = Math.min(bottom, bounds.top + ancestor.clientTop + ancestor.clientHeight);
      }
      if (/^(auto|scroll|hidden|clip)$/.test(style.overflowX)) {
        left = Math.max(left, bounds.left + ancestor.clientLeft);
        right = Math.min(right, bounds.left + ancestor.clientLeft + ancestor.clientWidth);
      }
    }
    expect(rect.top).toBeGreaterThanOrEqual(top - 1.5);
    expect(rect.bottom).toBeLessThanOrEqual(bottom + 1.5);
    expect(rect.left).toBeGreaterThanOrEqual(left - 1.5);
    expect(rect.right).toBeLessThanOrEqual(right + 1.5);
  });
  await page.screenshot({ element, path: `../../../artifacts/browser-layout/${filename}.png` });
}

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
    if (width === 390) expectReadableMobilePicker(picker, 'initial');
    await page.screenshot({ path: `../../../artifacts/browser-layout/new-work-default-${width}.png` });
    await userEvent.selectOptions(picker, 'runtime:claude_code');
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await waitFor(() => expect(finishSave).toBeDefined());
    expect(picker.disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(true);
    if (width === 390) expectReadableMobilePicker(picker, 'pending Save');
    await page.screenshot({ path: `../../../artifacts/browser-layout/new-work-default-${width}-saving.png` });
    finishSave!(respond({}));
    await screen.findByText('Defaults saved');
    expect(picker.disabled).toBe(false);
    expect(picker.value).toBe('runtime:claude_code');
    expect(requests.some((request) => request.url.includes('/audit'))).toBe(false);
  });
  it.each([1280, 390])('shows the locked compact control without another box at %i px', async (width) => {
    await page.viewport(width, 850);
    descriptors = descriptors.map((descriptor) => ({ ...descriptor, read_only: true, read_only_reason: 'Managed by deployment policy' }));
    renderWithClient(<MemoryRouter><SettingsDraftGuardProvider><NewWorkDefaultsCard /></SettingsDraftGuardProvider></MemoryRouter>, { container: panel });
    const picker = await screen.findByLabelText('Default for new work') as HTMLSelectElement;
    const card = screen.getByRole('region', { name: 'Defaults for new work' });
    expect(picker.disabled).toBe(true);
    expect(picker.value).toBe('runtime:codex_cli');
    expect(picker.closest('fieldset')).toBeNull();
    expect(card.textContent).toContain('Managed by deployment policy');
    expect(card.textContent).not.toMatch(/audit|history/i);
    expect(within(card).queryByRole('button', { name: 'Save' })).toBeNull();
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
    if (width === 390) expectReadableMobilePicker(picker, 'locked');
    await captureVisibleRegion(card, `new-work-default-${width}-locked`);
    expect(requests.some((request) => request.method === 'PATCH' || request.url.includes('/audit'))).toBe(false);
  });

  it.each([1280, 390])('keeps Instance default resets without default audit access at %i px', async (width) => {
    await page.viewport(width, 1000);
    const { queryClient } = renderWithClient(<MemoryRouter><SettingsDraftGuardProvider>
      <GeneratedSettingsSection title="Instance settings" />
    </SettingsDraftGuardProvider></MemoryRouter>, { container: panel });
    await screen.findByLabelText('Default Harness');
    await screen.findByRole('option', { name: /codex-team/ });
    for (const title of ['Default Harness', 'Default Provider Profile']) {
      const control = screen.getByLabelText(title);
      const row = control.closest<HTMLElement>('[data-setting-key]')!;
      expect(control.closest('fieldset')).toBeNull();
      expect(parseFloat(getComputedStyle(control).borderTopLeftRadius)).toBeGreaterThan(0);
      for (let wrapper = control.parentElement; wrapper && wrapper !== row; wrapper = wrapper.parentElement) {
        expect(parseFloat(getComputedStyle(wrapper).borderTopWidth)).toBe(0);
        expect(parseFloat(getComputedStyle(wrapper).borderLeftWidth)).toBe(0);
      }
      expect(within(row).queryByRole('button', { name: /audit|history/i })).toBeNull();
      expect(within(row).getByRole('button', { name: `Reset ${title}` })).toBeTruthy();
      expect(screen.queryByRole('region', { name: `Audit history for ${title}` })).toBeNull();
    }
    expect(requests.some((request) => request.url.includes('/audit'))).toBe(false);
    expect(screen.getByRole('button', { name: 'View audit for Default Publish Mode' })).toBeTruthy();
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(width);
    for (const [title, slug] of [['Default Harness', 'harness'], ['Default Provider Profile', 'profile'], ['Default Publish Mode', 'publish']]) {
      const row = screen.getByLabelText(title!).closest<HTMLElement>('[data-setting-key]')!;
      await captureVisibleRegion(row, `new-work-default-instance-${width}-${slug}`);
    }

    await page.getByRole('button', { name: 'Reset Default Harness', exact: true }).click();
    await screen.findByText('Default Harness reset.');
    await waitFor(() => expect((screen.getByLabelText('Default Harness') as HTMLSelectElement).value).toBe('omnigent'));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Reset Default Harness', exact: true })).toBeNull());
    // Reset removes an action above this row. Settle the new mobile scroll
    // position before sending the next native pointer interaction in WebKit.
    await captureVisibleRegion(
      screen.getByLabelText('Default Provider Profile').closest<HTMLElement>('[data-setting-key]')!,
      `new-work-default-instance-${width}-profile-before-reset`,
    );
    await page.getByRole('button', { name: 'Reset Default Provider Profile', exact: true }).click();
    await screen.findByText('Default Provider Profile reset.');
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Reset Default Provider Profile', exact: true })).toBeNull());
    for (const key of [runtimeKey, profileKey]) {
      expect(requests.some((request) => request.method === 'DELETE' && request.url === `/api/v1/settings/${key}`)).toBe(true);
    }
    expect(requests.some((request) => request.url.includes('/audit'))).toBe(false);

    await page.getByRole('button', { name: 'View audit for Default Publish Mode', exact: true }).click();
    await screen.findByText('No audit events were found for this setting.');
    expect(screen.getByRole('region', { name: 'Audit history for Default Publish Mode' })).toBeTruthy();
    const auditRequests = requests.filter((request) => request.url.includes('/audit'));
    expect(auditRequests.length).toBeGreaterThan(0);
    expect(auditRequests.every((request) => new URL(request.url, window.location.origin).searchParams.get('key') === publishKey)).toBe(true);
    await captureVisibleRegion(screen.getByRole('region', { name: 'Audit history for Default Publish Mode' }), `new-work-default-instance-${width}-other-audit`);
    await page.getByRole('button', { name: 'Close audit history for Default Publish Mode', exact: true }).click();
    descriptors = descriptors.map((descriptor) => descriptor.key === runtimeKey
      ? { ...descriptor, read_only: true, read_only_reason: 'Harness managed by deployment policy' } : descriptor);
    await queryClient.invalidateQueries({ queryKey: ['settings-catalog'] });
    await screen.findByText('Harness managed by deployment policy');
    expect((screen.getByRole('combobox', { name: 'Default Harness' }) as HTMLSelectElement).disabled).toBe(true);
    expect(screen.getByRole('combobox', { name: 'Default Provider Profile' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'View audit for Default Harness' })).toBeNull();
    await captureVisibleRegion(screen.getByRole('combobox', { name: 'Default Harness' }).closest<HTMLElement>('[data-setting-key]')!, `new-work-default-instance-${width}-locked`);
  }, 20000);

});
