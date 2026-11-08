import { afterEach, beforeEach, expect, it, vi, type MockInstance } from 'vitest';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardApp } from '../entrypoints/dashboard-app';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';

export function assessmentPresetJourneyCases(userEvent: {
  click: (element: HTMLElement) => Promise<unknown>;
  fill: (element: HTMLElement, value: string) => Promise<unknown>;
  selectOptions: (element: HTMLElement, value: string) => Promise<unknown>;
}, page: { viewport: (width: number, height: number) => Promise<unknown> }) {
// Controlled catalog and admission responses isolate authoring and retry boundaries.


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

beforeEach(() => {
  window.sessionStorage.clear();
  window.localStorage.clear();
  window.history.replaceState({}, '', '/workflows/new');
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/ui/info') return jsonResponse(uiInfo);
      if (url === '/api/executions' && init?.method === 'POST') {
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

// The catalog/expansion service is controlled here. These tests exercise the
// served Create form, not scanner execution or external account readiness.
const preset = {
  slug: 'assessment-schema-fixture', title: 'Bounded assessment', version: '1', latestVersion: '1',
  description: 'Read the selected targets and retain a report.', defaults: {}, uiSchema: {},
  inputSchema: { type: 'object', required: ['assessment'], properties: {
    assessment: { type: 'object', title: 'Assessment', required: ['target'], properties: {
      target: { type: 'string', title: 'Target' },
      regions: { type: 'array', title: 'Regions', items: { type: 'string' } },
      limit: { type: 'integer', title: 'Limit', minimum: 1, maximum: 5, default: 3 },
      optional: { type: 'object', title: 'Optional evidence', required: ['path'], properties: { path: { type: 'string', title: 'Evidence path' } } },
    } },
  } },
};

it.each([false, true])('retains the admitted expansion on retry unless authored inputs change (%s)', async (editBeforeRetry) => {
  const original = fetchSpy.getMockImplementation()!;
  const expanded: Array<Record<string, unknown>> = [];
  const submitted: Array<Record<string, unknown>> = [];
  fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/presets') return jsonResponse({ items: [preset] });
    if (url === `/api/presets/${preset.slug}`) return jsonResponse(preset);
    if (url === `/api/presets/${preset.slug}:expand`) {
      const request = JSON.parse(String(init?.body));
      expanded.push(request.inputs);
      if (expanded.length === 1) return jsonResponse({ detail: 'Prepared feed unavailable. Select an available feed and retry.' }, 422);
      return jsonResponse({ steps: [{ id: 'assessment-report', title: 'Retain report', instructions: 'Produce a bounded assessment report.' }],
        appliedTemplate: { slug: preset.slug, version: '1', inputs: request.inputs }, capabilities: [], warnings: [] });
    }
    if (url === '/api/executions' && init?.method === 'POST') {
      submitted.push(JSON.parse(String(init.body)));
      if (submitted.length === 1) throw new Error('Acknowledgment lost');
      return jsonResponse({ workflowId: 'mm:assessment-once', runId: 'run-1' }, 201);
    }
    return original(input, init);
  });
  const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
  cleanupRender = unmount;
  await screen.findByLabelText('Instructions', {}, { timeout: 10_000 });
  const step = screen.getByText('Step 1').closest('section')!;
  await userEvent.click(within(step).getByRole('radio', { name: 'Preset' }).closest('label')!);
  const select = within(step).getByLabelText('Preset Template');
  await waitFor(() => expect(within(select).getByRole('option', { name: preset.title })).toBeTruthy());
  await userEvent.selectOptions(select, preset.slug);
  await userEvent.fill(await within(step).findByRole('textbox', { name: 'Target' }), 'repo/source');
  await userEvent.fill(within(step).getByRole('textbox', { name: 'Evidence path' }), 'saved/report.json');
  await userEvent.click(within(step).getByRole('button', { name: 'Omit Optional evidence' }));
  expect(within(step).getByRole('textbox', { name: 'Evidence path' })).toHaveProperty('value', '');
  await userEvent.fill(within(step).getByRole('textbox', { name: 'Regions' }), 'us-east-1\neu-west-1');
  const limit = within(step).getByRole('spinbutton', { name: 'Limit' });
  await userEvent.fill(limit, '6');
  await userEvent.click(within(step).getByRole('button', { name: 'Expand' }));
  await waitFor(() => expect(limit.getAttribute('aria-invalid')).toBe('true'));
  expect(expanded).toHaveLength(0);
  await userEvent.fill(limit, '3');
  expect(limit.getAttribute('aria-invalid')).toBe('false');
  await userEvent.click(within(step).getByRole('button', { name: 'Expand' }));
  await screen.findByText(/Prepared feed unavailable/);
  expect(expanded[0]).toEqual({ assessment: { target: 'repo/source', regions: ['us-east-1', 'eu-west-1'], limit: 3 } });
  expect(within(step).getByRole('textbox', { name: 'Target' })).toHaveProperty('value', 'repo/source');
  await userEvent.fill(within(step).getByRole('textbox', { name: 'Target' }), 'repo/approved');
  await userEvent.click(screen.getByRole('button', { name: 'Start Workflow' }));
  await screen.findByText('Acknowledgment lost');
  if (editBeforeRetry) await userEvent.fill(within(step).getByRole('textbox', { name: 'Target' }), 'repo/new-target');
  await userEvent.click(screen.getByRole('button', { name: 'Start Workflow' }));
  await expect.poll(() => window.location.pathname).toBe('/workflows/mm%3Aassessment-once');
  expect(expanded.at(-1)).toEqual({ assessment: { target: editBeforeRetry ? 'repo/new-target' : 'repo/approved', regions: ['us-east-1', 'eu-west-1'], limit: 3 } });
  expect(submitted).toHaveLength(2);
  if (editBeforeRetry) {
    expect(expanded).toHaveLength(3);
    const identity = (request: Record<string, unknown>) => (request.payload as { task: { idempotencyKey: string } }).task.idempotencyKey;
    expect(identity(submitted[1]!)).not.toBe(identity(submitted[0]!));
  } else {
    expect(expanded).toHaveLength(2);
    expect(submitted[1]).toEqual(submitted[0]);
  }
});

it('cancels an interrupted preset submission on navigation without leaking it into the next draft', async () => {
  const original = fetchSpy.getMockImplementation()!;
  let finishExpansion!: (response: Response) => void;
  let beganExpansion = false;
  let admissions = 0;
  fetchSpy.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/presets') return jsonResponse({ items: [preset] });
    if (url === `/api/presets/${preset.slug}`) return jsonResponse(preset);
    if (url === `/api/presets/${preset.slug}:expand`) {
      beganExpansion = true;
      return new Promise<Response>(resolve => { finishExpansion = resolve; });
    }
    if (url === '/api/executions' && init?.method === 'POST') admissions += 1;
    return original(input, init);
  });
  const confirmation = vi.spyOn(window, 'confirm').mockReturnValue(true);
  try {
    const { unmount } = renderWithClient(<DashboardApp payload={payload} />);
    cleanupRender = unmount;
    await screen.findByLabelText('Instructions', {}, { timeout: 10_000 });
    const step = screen.getByText('Step 1').closest('section')!;
    await userEvent.click(within(step).getByRole('radio', { name: 'Preset' }).closest('label')!);
    const select = within(step).getByLabelText('Preset Template');
    await waitFor(() => expect(within(select).getByRole('option', { name: preset.title })).toBeTruthy());
    await userEvent.selectOptions(select, preset.slug);
    await userEvent.fill(await within(step).findByRole('textbox', { name: 'Target' }), 'repo/abandoned');
    await userEvent.fill(within(step).getByRole('textbox', { name: 'Evidence path' }), 'abandoned/report.json');
    await userEvent.click(within(step).getByRole('button', { name: 'Omit Optional evidence' }));
    await userEvent.click(screen.getByRole('button', { name: 'Start Workflow' }));
    await waitFor(() => expect(beganExpansion).toBe(true));
    await userEvent.click(screen.getByRole('link', { name: 'MoonMind workflows' }));
    await expect.poll(() => window.location.pathname).toBe('/workflows');
    finishExpansion(jsonResponse({ steps: [{ id: 'late', instructions: 'Do not submit this abandoned plan.' }], appliedTemplate: { slug: preset.slug, inputs: {} }, capabilities: [], warnings: [] }));
    await userEvent.click(await screen.findByRole('link', { name: 'Create' }));
    await screen.findByLabelText('Instructions', {}, { timeout: 10_000 });
    expect(admissions).toBe(0);
    expect(screen.queryByDisplayValue('repo/abandoned')).toBeNull();
    expect(screen.queryByDisplayValue('Do not submit this abandoned plan.')).toBeNull();
  } finally {
    confirmation.mockRestore();
  }
});

}
