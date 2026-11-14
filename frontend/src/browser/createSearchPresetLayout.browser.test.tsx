import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { commands, page, userEvent } from 'vitest/browser';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardApp } from '../entrypoints/dashboard-app';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import '../styles/dashboard.css';

declare module 'vitest/browser' {
  interface BrowserCommands {
    captureViewport(fileName: string, width: number, height: number): Promise<void>;
  }
}

// Production Create page with controlled catalog responses. Seed-specific metadata
// is covered by the catalog tests; this journey exercises rendering and input retention.
const DESKTOP = { width: 1280, height: 900 };
const MOBILE = { width: 390, height: 844 };
const slug = 'github-issue-search-and-implement';
const detail = {
  slug, title: 'GitHub Issue Search and Implement', version: '1', latestVersion: '1',
  inputSchema: { type: 'object', properties: {
    run_verify: { type: 'boolean', title: 'Run verification', description: 'Adds a separate verification and remediation pass before pull request handoff.', default: false },
    constraints: { type: 'string', title: 'Constraints' },
    issue_search: { type: 'string', title: 'GitHub issue search', description: 'Leave blank to select the next eligible issue.' },
    include_all_authors: { type: 'boolean', title: 'Include issues created by other users', description: 'Otherwise, search only issues created by your GitHub account.', default: false },
  } },
  uiSchema: {
    issue_search: { order: 10, fullWidth: true, widget: 'text' },
    include_all_authors: { order: 20, helpDisplay: 'tooltip' },
    run_verify: { order: 30, helpDisplay: 'tooltip' },
    constraints: { order: 40, fullWidth: true, widget: 'textarea', advanced: true },
  },
  defaults: { run_verify: false, include_all_authors: false },
};
const uiInfo = {
  app: 'moonmind', buildId: 'create-search-layout', apiBase: '/api',
  features: { workflowList: true, workflowActions: true, workflowLiveUpdates: false, artifacts: true }, limits: {}, endpoints: {},
  dashboardConfig: {
    pollIntervalsMs: { list: 60_000, detail: 60_000, events: 60_000 },
    sources: { temporal: { create: '/api/executions', artifactCreate: '/api/artifacts' }, github: { branches: '/api/github/branches?repository={repository}' } },
    system: { defaultRepository: 'MoonLadderStudios/MoonMind', defaultAgentRuntime: 'codex_cli', defaultPublishMode: 'none', providerProfiles: { list: '/api/v1/provider-profiles' }, presetCatalog: { enabled: true, list: '/api/presets' } },
    features: { temporalDashboard: { actionsEnabled: true, listEnabled: false, workspaceShellEnabled: false } },
  },
};
const payload: BootPayload = { page: 'dashboard', apiBase: '/api' };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
let fetchSpy: MockInstance;
let cleanupRender: (() => void) | null = null;
let expandedInputs: Array<Record<string, unknown>>;
beforeEach(() => {
  window.sessionStorage.clear(); window.localStorage.clear();
  window.history.replaceState({}, '', '/workflows/new');
  expandedInputs = [];
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === '/api/ui/info') return json(uiInfo);
    if (url === '/api/presets') return json({ items: [detail] });
    if (url === `/api/presets/${slug}`) return json(detail);
    if (url.startsWith(`/api/presets/${slug}:expand`)) {
      expandedInputs.push(JSON.parse(String(init?.body)).inputs);
      return json({ detail: 'Retain draft for preview' }, 422);
    }
    if (url.startsWith('/api/v1/provider-profiles')) return json([{ profile_id: 'codex', account_label: 'Codex', runtime_id: 'codex_cli', is_default: true, enabled: true, launch_ready: true }]);
    if (url.startsWith('/api/github/branches')) return json({ items: [{ value: 'main', label: 'main' }], defaultBranch: 'main', error: null });
    if (url.startsWith('/api/workflows/skills')) return json({ items: {} });
    if (url.startsWith('/api/executions')) return json({ items: [] });
    return json({});
  });
});
afterEach(async () => {
  cleanupRender?.(); cleanupRender = null;
  fetchSpy.mockRestore();
  window.sessionStorage.clear(); window.localStorage.clear();
  window.history.replaceState({}, '', '/');
  await page.viewport(DESKTOP.width, DESKTOP.height);
});
function expectInsideViewport(element: Element) {
  const rect = element.getBoundingClientRect();
  expect(rect.left).toBeGreaterThanOrEqual(-1);
  expect(rect.right).toBeLessThanOrEqual(document.documentElement.clientWidth + 1);
}

describe('Search preset progressive disclosure', () => {
  for (const viewport of [DESKTOP, MOBILE]) {
    it(`keeps search primary and help accessible at ${viewport.width}px`, async () => {
      await page.viewport(viewport.width, viewport.height);
      const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
      cleanupRender = unmount;
      const presetRadio = await screen.findByRole('radio', { name: 'Preset' }, { timeout: 10_000 });
      await userEvent.click(presetRadio.closest('label')!);
      const presetSelect = screen.getByLabelText('Preset Template');
      await screen.findByRole('option', { name: detail.title });
      await userEvent.selectOptions(presetSelect, slug);
      const search = await screen.findByLabelText('GitHub issue search');
      const verify = screen.getByRole('checkbox', { name: 'Run verification' });
      const authors = screen.getByRole('checkbox', { name: 'Include issues created by other users' });
      expect(verify).not.toBeChecked(); expect(authors).not.toBeChecked();
      expect(search.getBoundingClientRect().bottom).toBeLessThan(verify.getBoundingClientRect().top);
      const options = screen.getByLabelText('Step Preset Options');
      const width = options.getBoundingClientRect().width;
      expect(search.getBoundingClientRect().width).toBeGreaterThan(width * 0.9);
      expect(screen.queryByLabelText('Constraints')).toBeNull();
      const help = screen.getByRole('button', { name: 'Help for Run verification' });
      const panel = document.getElementById(help.getAttribute('aria-describedby')!)!;
      expect(panel.hidden).toBe(true);
      help.focus();
      await expect.element(page.getByRole('tooltip', { includeHidden: true }).filter({ hasText: 'Adds a separate verification' })).toBeVisible();
      expectInsideViewport(panel);
      await userEvent.keyboard('{Escape}');
      await expect.element(page.getByRole('tooltip', { includeHidden: true }).filter({ hasText: 'Adds a separate verification' })).not.toBeVisible();
      await userEvent.click(help);
      await expect.element(page.getByRole('tooltip', { includeHidden: true }).filter({ hasText: 'Adds a separate verification' })).toBeVisible();
      await userEvent.click(help);
      await expect.element(page.getByRole('tooltip', { includeHidden: true }).filter({ hasText: 'Adds a separate verification' })).not.toBeVisible();
      await userEvent.fill(search, 'label:bug');
      const authorHelp = screen.getByRole('button', { name: 'Help for Include issues created by other users' });
      await userEvent.hover(authorHelp);
      const authorPanel = document.getElementById(authorHelp.getAttribute('aria-describedby')!)!;
      // Wait until intentional hover has actually opened help, then use the
      // next checkbox normally. A delay must not merely hide an overlap bug.
      await expect.element(page.getByRole('tooltip', { includeHidden: true }).filter({ hasText: 'Otherwise, search only' })).toBeVisible();
      expect(authorPanel.getBoundingClientRect().bottom).toBeLessThan(authorHelp.getBoundingClientRect().top);
      await userEvent.click(verify);
      expect(verify).toBeChecked();
      await userEvent.click(authors);
      const advanced = screen.getByRole('checkbox', { name: 'Advanced mode' });
      await userEvent.click(advanced);
      const constraints = await screen.findByLabelText('Constraints');
      await userEvent.fill(constraints, 'Preserve existing work.');
      expect(constraints.closest('details')).toBeNull();
      await userEvent.click(advanced);
      expect(screen.queryByLabelText('Constraints')).toBeNull();
      expect(verify).toBeChecked(); expect(authors).toBeChecked();
      await userEvent.click(advanced);
      expect((await screen.findByLabelText('Constraints') as HTMLTextAreaElement).value).toBe('Preserve existing work.');
      await userEvent.click(advanced);
      await userEvent.click(within(options).getByRole('button', { name: 'Expand' }));
      await waitFor(() => expect(expandedInputs).toHaveLength(1));
      expect(expandedInputs[0]).toMatchObject({ issue_search: 'label:bug', run_verify: true, include_all_authors: true, constraints: 'Preserve existing work.' });
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + 2);
      for (const control of [search, verify, authors, help]) expectInsideViewport(control);
      window.scrollTo(0, 0);
      await commands.captureViewport(`search-preset-${viewport.width}.png`, viewport.width, viewport.height);
      help.focus();
      await expect.element(page.getByRole('tooltip', { includeHidden: true }).filter({ hasText: 'Adds a separate verification' })).toBeVisible();
      expectInsideViewport(panel);
      await commands.captureViewport(`search-preset-help-${viewport.width}.png`, viewport.width, viewport.height);
    });
  }
});
