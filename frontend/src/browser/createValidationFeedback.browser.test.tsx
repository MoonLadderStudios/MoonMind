import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page, userEvent } from 'vitest/browser';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardApp } from '../entrypoints/dashboard-app';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import '../styles/dashboard.css';

// Real-browser check that a Create validation failure is visible wherever the
// form is scrolled: the reason shows in the fixed submit bar beside Start, and
// focus moves to the field that needs the correction.

const VIEWPORT = { width: 1280, height: 700 } as const;

const uiInfo = {
  app: 'moonmind',
  buildId: 'create-validation-browser-test',
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
      defaultTaskModel: 'gpt-5.6-codex',
      defaultTaskEffort: 'high',
      defaultPublishMode: 'pr',
      supportedAgentRuntimes: ['codex_cli'],
      providerProfiles: { list: '/api/v1/provider-profiles' },
      presetCatalog: { enabled: true, list: '/api/presets' },
    },
    features: {
      temporalDashboard: { actionsEnabled: true, listEnabled: false, workspaceShellEnabled: false },
    },
  },
  settingsPermissions: [],
};

const payload: BootPayload = { page: 'dashboard', apiBase: '/api' };

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function inViewport(element: Element): boolean {
  const rectangle = element.getBoundingClientRect();
  return rectangle.top >= 0 && rectangle.bottom <= window.innerHeight + 1;
}

let fetchSpy: MockInstance;
let cleanupRender: (() => void) | null = null;
let createRequests: number;

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/workflows/new');
  createRequests = 0;
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/ui/info') return jsonResponse(uiInfo);
      if (url === '/api/executions' && init?.method === 'POST') {
        createRequests += 1;
        return jsonResponse({ workflowId: 'mm:created', runId: 'created-run' }, 201);
      }
      if (url.startsWith('/api/v1/provider-profiles')) {
        return jsonResponse([
          {
            profile_id: 'oauth-1',
            runtime_id: 'codex_cli',
            account_label: 'Codex OAuth',
            provider_id: 'openai',
            enabled: true,
            launch_ready: true,
          },
        ]);
      }
      if (url.startsWith('/api/github/branches')) {
        return jsonResponse({
          items: [{ value: 'main', label: 'main', source: 'github' }],
          defaultBranch: 'main',
          error: null,
        });
      }
      if (url === '/mcp/tools') return jsonResponse({ tools: [] });
      if (url.startsWith('/api/workflows/skills')) return jsonResponse({ items: {} });
      if (url.startsWith('/api/presets')) return jsonResponse({ items: [] });
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
  await page.viewport(1280, 900);
});

describe('Create validation feedback', () => {
  it('shows an off-screen step error in the submit bar and focuses the field', async () => {
    await page.viewport(VIEWPORT.width, VIEWPORT.height);
    const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
    cleanupRender = unmount;

    const instructions = await screen.findByLabelText('Instructions', {}, { timeout: 10_000 });
    await userEvent.fill(instructions, 'Run the primary step.');
    const addStep = screen.getByRole('button', { name: 'Add Step' });
    await userEvent.click(addStep);
    await userEvent.click(addStep);
    const third = screen.getByText('Step 3').closest('section') as HTMLElement;
    // The segmented control's radio is visually hidden behind its label.
    const toolType = within(third).getByRole('radio', { name: 'Tool' });
    await userEvent.click(toolType.closest('label') as HTMLElement);
    await userEvent.fill(within(third).getByLabelText('Step 3 Instructions'), 'Run a tool.');

    window.scrollTo(0, 0);
    const toolId = within(third).getByLabelText('Tool ID');
    expect(inViewport(toolId)).toBe(false);

    await userEvent.click(screen.getByRole('button', { name: 'Start Workflow' }));

    const bar = screen.getByRole('group', { name: 'Workflow submission controls' });
    const message = await within(bar).findByRole('alert');
    expect(message.textContent).toBe('Select a Tool before submitting Step 3.');
    expect(inViewport(message)).toBe(true);
    await waitFor(() => expect(document.activeElement).toBe(toolId));
    expect(toolId.getAttribute('aria-invalid')).toBe('true');
    expect(createRequests).toBe(0);
  });
});
