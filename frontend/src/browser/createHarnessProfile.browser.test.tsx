import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page, userEvent } from 'vitest/browser';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardApp } from '../entrypoints/dashboard-app';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import '../styles/dashboard.css';

// Real-browser Create journey for Harness and Provider Profile authoring
// (MoonLadderStudios/MoonMind#4639). It mounts the production dashboard router
// at /workflows/new and captures the ordinary POST /api/executions request.

const DESKTOP = { width: 1280, height: 900 } as const;
const MOBILE = { width: 390, height: 844 } as const;
// Equal long labels in two Harnesses must stay distinguishable by Harness.
const LONG_LABEL =
  'Shared engineering subscription with an intentionally long account label for narrow layouts';

const uiInfo = {
  app: 'moonmind',
  buildId: 'create-harness-profile-browser-test',
  apiBase: '/api',
  features: { workflowList: true, workflowActions: true, workflowLiveUpdates: false, artifacts: true },
  limits: {},
  endpoints: {},
  dashboardConfig: {
    pollIntervalsMs: { list: 60_000, detail: 60_000, events: 60_000 },
    sources: {
      temporal: { create: '/api/executions', artifactCreate: '/api/artifacts' },
      github: { branches: '/api/github/branches?repository={repository}' },
    },
    system: {
      defaultRepository: 'MoonLadderStudios/MoonMind',
      defaultAgentRuntime: 'codex_cli',
      defaultPublishMode: 'none',
      providerProfiles: { list: '/api/v1/provider-profiles' },
      presetCatalog: { enabled: true, list: '/api/presets' },
    },
    features: {
      temporalDashboard: { actionsEnabled: true, listEnabled: false, workspaceShellEnabled: false },
    },
  },
};

const payload: BootPayload = { page: 'dashboard', apiBase: '/api' };

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

let fetchSpy: MockInstance;
let cleanupRender: (() => void) | null = null;
let createRequests: Array<Record<string, unknown>>;

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/workflows/new');
  createRequests = [];
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/ui/info') return jsonResponse(uiInfo);
      if (url === '/api/executions' && init?.method === 'POST') {
        createRequests.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return jsonResponse({ workflowId: 'mm:created', runId: 'created-run' }, 201);
      }
      if (url.includes('/capabilities')) {
        // Advisory model discovery is unavailable; the draft stays usable.
        return jsonResponse({ detail: 'discovery unavailable' }, 503);
      }
      if (url.startsWith('/api/v1/provider-profiles')) {
        return jsonResponse([
          { profile_id: 'codex-shared', account_label: LONG_LABEL, runtime_id: 'codex_cli', is_default: true, enabled: true, launch_ready: true },
          { profile_id: 'claude-shared', account_label: LONG_LABEL, runtime_id: 'claude_code', enabled: true, launch_ready: true },
        ]);
      }
      if (url.startsWith('/api/github/branches')) {
        return jsonResponse({ items: [{ value: 'main', label: 'main', source: 'github' }], defaultBranch: 'main', error: null });
      }
      if (url.startsWith('/api/workflows/skills')) return jsonResponse({ items: {} });
      if (url.startsWith('/api/presets')) return jsonResponse({ items: [] });
      if (url.startsWith('/api/artifacts')) return jsonResponse({ artifacts: [] });
      if (url.startsWith('/api/executions')) return jsonResponse({ items: [] });
      return jsonResponse({});
    },
  );
});

afterEach(async () => {
  cleanupRender?.();
  cleanupRender = null;
  fetchSpy.mockRestore();
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/');
  await page.viewport(DESKTOP.width, DESKTOP.height);
});

function expectInsideViewport(controls: Element[]) {
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth + 2);
  for (const control of controls) {
    const rectangle = control.getBoundingClientRect();
    expect(rectangle.left).toBeGreaterThanOrEqual(-1);
    expect(rectangle.right).toBeLessThanOrEqual(window.innerWidth + 1);
  }
}

describe('Create Harness and Provider Profile authoring', () => {
  for (const viewport of [DESKTOP, MOBILE]) {
    it(`keeps Advanced mode below the form content at ${viewport.width}px`, async () => {
      await page.viewport(viewport.width, viewport.height);
      const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
      cleanupRender = unmount;

      const skill = await screen.findByRole('combobox', { name: 'Step 1 skill' }, { timeout: 10_000 });
      expect((skill as HTMLInputElement).value).toBe('auto');
      const advanced = screen.getByRole('checkbox', { name: 'Advanced mode' });
      const steps = screen.getByRole('region', { name: 'Steps' });
      expect(advanced.getBoundingClientRect().top).toBeGreaterThan(steps.getBoundingClientRect().bottom);

      await userEvent.click(advanced);
      expect(screen.getByLabelText('Priority')).toBeTruthy();
      expectInsideViewport([advanced]);
    });

    it(`keeps an explicit Profile recoverable across Harness changes at ${viewport.width}px`, async () => {
      await page.viewport(viewport.width, viewport.height);
      const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
      cleanupRender = unmount;

      const instructions = (await screen.findByLabelText(
        'Instructions',
        {},
        { timeout: 10_000 },
      )) as HTMLTextAreaElement;
      const harness = screen.getByLabelText('Harness') as HTMLSelectElement;
      // The Provider Profile control appears once the authorized inventory loads.
      const profile = (await screen.findByLabelText(
        'Provider Profile',
        {},
        { timeout: 10_000 },
      )) as HTMLSelectElement;
      const start = screen.getByRole('button', { name: 'Start Workflow' });
      await waitFor(() => expect(profile.value).toBe('codex-shared'));
      expect(screen.queryByLabelText('Runtime')).toBeNull();
      expect(screen.queryByLabelText('Backend')).toBeNull();
      expect(within(profile).getAllByRole('option')).toHaveLength(1);
      expectInsideViewport([harness, profile, start]);

      await userEvent.fill(instructions, 'Keep this draft through Harness changes.');
      // An explicit choice is never replaced by the other Harness's account.
      await userEvent.selectOptions(profile, 'codex-shared');
      harness.focus();
      expect(document.activeElement).toBe(harness);
      await userEvent.selectOptions(harness, 'claude_code');
      expect(profile.value).toBe('');
      const mismatch = await screen.findByText(/Choose a Claude Code Provider Profile\./);
      expectInsideViewport([harness, profile, mismatch]);
      expect(start).toBeDisabled();
      expect(instructions.value).toBe('Keep this draft through Harness changes.');

      await userEvent.selectOptions(harness, 'codex_cli');
      expect(profile.value).toBe('codex-shared');
      await waitFor(() => expect(start).not.toBeDisabled());
      await userEvent.click(start);
      await waitFor(() => expect(createRequests).toHaveLength(1));
      const task = (createRequests[0]?.payload as { task: { runtime: Record<string, unknown> } }).task;
      expect(task.runtime).toMatchObject({ mode: 'codex_cli', profileId: 'codex-shared' });
    });
  }
});
