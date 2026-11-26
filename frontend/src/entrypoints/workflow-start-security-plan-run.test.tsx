import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { act, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi, type MockInstance } from 'vitest';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardApp } from './dashboard-app';
import SecurityPage from './security';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';

// A retained plan's "Run this plan" link opens Create with the security plan
// run preset and the plan prefilled. The server derives the plan's jobs and
// conclusion when the step expands; no per-plan frontend code is involved.
const yaml = createRequire(import.meta.url)('js-yaml') as { load: (text: string) => { slug: string; title: string; description: string; tags: string[]; annotations: { inputSchema: Record<string, unknown>; defaults: Record<string, unknown> } } };
const seed = yaml.load(readFileSync('api_service/data/presets/security-plan-run.yaml', 'utf8'));
const preset = {
  slug: seed.slug, title: seed.title, description: seed.description, tags: seed.tags, version: '1', latestVersion: '1',
  inputSchema: seed.annotations.inputSchema, defaults: seed.annotations.defaults,
};
const uiInfo = {
  app: 'moonmind', buildId: 'security-plan-run-test', apiBase: '/api',
  features: { workflowList: true, workflowActions: true, workflowLiveUpdates: false, artifacts: true }, limits: {}, endpoints: {},
  dashboardConfig: {
    pollIntervalsMs: { list: 60_000, detail: 60_000, events: 60_000 },
    sources: { temporal: { create: '/api/executions', artifactCreate: '/api/artifacts' }, github: { branches: '/api/github/branches?repository={repository}' } },
    system: {
      defaultRepository: 'MoonLadderStudios/MoonMind', defaultAgentRuntime: 'codex_cli', defaultTaskModel: 'gpt-5.6-codex', defaultTaskEffort: 'high',
      defaultPublishMode: 'pr', supportedAgentRuntimes: ['codex_cli'], providerProfiles: { list: '/api/v1/provider-profiles' },
      presetCatalog: { enabled: true, list: '/api/presets' },
    },
    features: { temporalDashboard: { actionsEnabled: true, workspaceShellEnabled: false } },
  },
};
const payload: BootPayload = { page: 'dashboard', apiBase: '/api' };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const runHref = `/workflows/new?preset=security-plan-run&presetInputs=${encodeURIComponent(JSON.stringify({ plan_ref: 'art_plan' }))}`;

let fetchSpy: MockInstance;
let expanded: Array<Record<string, unknown>>;
let unmount: (() => void) | null = null;

beforeEach(() => {
  expanded = [];
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/ui/info') return json(uiInfo);
    if (url.startsWith('/api/presets?') || url === '/api/presets') return json({ items: [preset] });
    if (url === '/api/presets/security-plan-run') return json(preset);
    if (url === '/api/presets/security-plan-run:expand') {
      const request = JSON.parse(String(init?.body));
      expanded.push(request.inputs);
      return json({ steps: [{ id: 'conclude', type: 'tool', title: 'Conclude per selected standard', tool: { id: 'security.assess', inputs: {} } }],
        appliedTemplate: { slug: 'security-plan-run', version: '1', inputs: request.inputs }, capabilities: [], warnings: [] });
    }
    if (url.startsWith('/api/executions?')) return json({ items: [{ workflowId: 'plan-run', title: 'Security plan', status: 'completed', createdAt: '2026-11-25T00:00:00Z' }] });
    if (url.startsWith('/api/executions/plan-run?')) return json({ namespace: 'default', runId: 'run-1', inputParameters: {} });
    if (url === '/api/executions/default/plan-run/run-1/artifacts') return json({ artifacts: [
      { artifactId: 'art_plan', links: [{ linkType: 'security.plan' }] },
      { artifactId: 'art_report', links: [{ linkType: 'report.primary', label: 'Security plan report' }] },
      { artifactId: 'art_other', links: [{ linkType: 'output.summary' }] },
    ] });
    if (url.startsWith('/api/v1/provider-profiles')) return json([]);
    if (url.startsWith('/api/github/branches')) return json({ items: [{ value: 'main', label: 'main', source: 'github' }], defaultBranch: 'main', error: null });
    if (url === '/mcp/tools') return json({ tools: [] });
    if (url.startsWith('/api/workflows/skills')) return json({ items: {} });
    return json({});
  });
});

afterEach(() => {
  unmount?.();
  unmount = null;
  fetchSpy.mockRestore();
  window.history.replaceState({}, '', '/');
});

it('opens Create with the retained plan prefilled and expands it on the server', async () => {
  window.history.replaceState({}, '', runHref);
  ({ unmount } = renderWithClient(<DashboardApp payload={payload} />));
  const step = (await screen.findByText('Step 1', {}, { timeout: 10_000 })).closest('section')!;
  await waitFor(() => expect(within(step).getByLabelText('Preset Template')).toHaveProperty('value', 'security-plan-run'));
  await waitFor(() => expect(within(step).getByLabelText('Retained security plan')).toHaveProperty('value', 'art_plan'));
  // The saved authorization and its review come from the existing assessment setup.
  expect(await screen.findByLabelText('Assessment context')).toBeTruthy();
  fireEvent.change(within(step).getByLabelText('Assessment reference'), { target: { value: 'shop-assessment' } });
  await act(async () => { fireEvent.click(within(step).getByRole('button', { name: 'Expand' })); });
  await waitFor(() => expect(expanded).toHaveLength(1));
  expect(expanded[0]).toMatchObject({ plan_ref: 'art_plan', assessment_ref: 'shop-assessment' });
}, 20_000);

it('ignores prefilled inputs the preset does not declare', async () => {
  window.history.replaceState({}, '', `/workflows/new?preset=security-plan-run&presetInputs=${encodeURIComponent(JSON.stringify({ plan_ref: 'art_plan', credentialRef: 'db://x' }))}`);
  ({ unmount } = renderWithClient(<DashboardApp payload={payload} />));
  const step = (await screen.findByText('Step 1', {}, { timeout: 10_000 })).closest('section')!;
  await waitFor(() => expect(within(step).getByLabelText('Retained security plan')).toHaveProperty('value', 'art_plan'));
  fireEvent.change(within(step).getByLabelText('Assessment reference'), { target: { value: 'shop-assessment' } });
  await act(async () => { fireEvent.click(within(step).getByRole('button', { name: 'Expand' })); });
  await waitFor(() => expect(expanded).toHaveLength(1));
  expect(expanded[0]).not.toHaveProperty('credentialRef');
}, 20_000);

it('offers Run this plan for a retained plan among a run’s reports', async () => {
  ({ unmount } = renderWithClient(<MemoryRouter initialEntries={['/security/reports']}><SecurityPage payload={payload} /></MemoryRouter>));
  await screen.findByRole('cell', { name: 'Security plan' }, { timeout: 10_000 });
  fireEvent.change(screen.getByLabelText('Assessment'), { target: { value: 'plan-run' } });
  const reports = await screen.findByRole('region', { name: 'Selected assessment reports' });
  const run = await within(reports).findByRole('link', { name: 'Run this plan' });
  expect(run.getAttribute('href')).toBe(runHref);
  expect(within(reports).getByText('Retained security plan')).toBeTruthy();
  expect(within(reports).getByText('Security plan report')).toBeTruthy();
  expect(within(reports).queryByText('art_other')).toBeNull();
  expect(within(reports).getAllByRole('link', { name: 'Run this plan' })).toHaveLength(1);
}, 20_000);
