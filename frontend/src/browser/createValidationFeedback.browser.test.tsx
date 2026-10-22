import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { page, userEvent, commands } from 'vitest/browser';

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
  it('retries a lost Create acknowledgment with one admitted execution', async () => {
    const original = fetchSpy.getMockImplementation()!;
    const keys: string[] = [];
    const accepted = new Set<string>();
    fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/executions' && init?.method === 'POST') {
        const key = JSON.parse(String(init.body)).payload.task.idempotencyKey as string;
        keys.push(key);
        accepted.add(key);
        if (keys.length === 1) throw new Error('Acknowledgment lost');
        return jsonResponse({ workflowId: 'mm:accepted-once', runId: 'run-1' }, 201);
      }
      return original(input, init);
    });
    const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
    cleanupRender = unmount;
    await userEvent.fill(await screen.findByLabelText('Instructions'), 'Admit this workflow only once.');
    await userEvent.click(screen.getByRole('button', { name: 'Start Workflow' }));
    await screen.findByText('Acknowledgment lost');
    await userEvent.click(screen.getByRole('button', { name: 'Start Workflow' }));
    await expect.poll(() => window.location.pathname).toBe('/workflows/mm%3Aaccepted-once');
    expect(keys).toHaveLength(2);
    expect(keys[0]).toMatch(/^create:/);
    expect(keys[1]).toBe(keys[0]);
    expect(accepted.size).toBe(1);
  });

  it('labels the bottom bar and presses Start with a green glow, flash and expanding ring', async () => {
    const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
    cleanupRender = unmount;
    await screen.findByLabelText('Instructions');
    const start = screen.getByRole('button', { name: 'Start Workflow' }) as HTMLButtonElement;
    await waitFor(() => expect(start.disabled).toBe(false));
    const bar = screen.getByRole('group', { name: 'Workflow submission controls' });
    for (const label of ['Repository', 'Branch', 'Publish Mode']) {
      expect(within(bar).getByText(label, { selector: '.queue-inline-selector-label' })).toBeTruthy();
      expect(within(bar).getByLabelText(label).closest('label')?.textContent).toContain(label);
    }
    const harness = screen.getByLabelText('Harness');
    expect(harness.closest('label')?.querySelectorAll('input, select, textarea, button').length).toBe(1);
    expect(screen.getByRole('button', { name: 'Add Step' }).getBoundingClientRect().height).toBeLessThanOrEqual(64);
    await expect.poll(() => getComputedStyle(start).backgroundColor).toBe('rgb(21, 147, 118)');
    expect(getComputedStyle(start).backgroundImage).toContain('linear-gradient');
    expect(getComputedStyle(start).color).toBe('rgb(255, 255, 255)');
    const restingShadow = getComputedStyle(start).boxShadow;
    expect(restingShadow).not.toBe('none');
    await userEvent.hover(start);
    const arrow = start.querySelector('.queue-submit-primary-arrow svg')!;
    await expect.poll(async () => {
      // The panel's entry animation can move the fixed bar after initial data
      // arrives; aim at the settled button before sampling its hover state.
      await userEvent.hover(start);
      return getComputedStyle(arrow).animationName;
    }).toBe('queue-submit-primary-arrow-cycle');
    await commands.pointerButton('down');
    // The arrow-to-check morph lasts about 230ms, so sample it first.
    expect(getComputedStyle(start).backgroundImage).toBe('none');
    expect(getComputedStyle(start).color).toBe('rgb(21, 147, 118)');
    expect(getComputedStyle(start.querySelector('[data-submit-icon="check"]')!).animationName).toBe('queue-submit-primary-check-pop');
    await expect.poll(() => {
      const ripple = document.querySelector('.queue-submit-primary-ripple');
      return ripple ? getComputedStyle(ripple).animationName : null;
    }).toBe('queue-submit-primary-ripple');
    // The flash holds for as long as the button stays pressed.
    await expect.poll(() => getComputedStyle(start).filter).toContain('brightness(1.22)');
    expect(getComputedStyle(start).boxShadow).not.toBe(restingShadow);
    // Release away from the button: inspect press without submitting a workflow.
    await userEvent.hover(screen.getByLabelText('Instructions'));
    await commands.pointerButton('up');
    expect(createRequests).toBe(0);
    // The ring removes itself once it has faded out.
    await expect.poll(() => document.querySelector('.queue-submit-primary-ripple')).toBeNull();
  });
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
