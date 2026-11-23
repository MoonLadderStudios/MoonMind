import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { act, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi, type MockInstance } from 'vitest';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardApp } from './dashboard-app';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';

// "Run this plan" opens Create with the plan-run preset and the retained plan
// prefilled. The draft is editable; expansion derives the planned jobs on the
// server and the authorization review still decides what may run.
const yaml = createRequire(import.meta.url)('js-yaml') as { load: (text: string) => { slug: string; title: string; description: string; tags: string[]; annotations: { inputSchema: Record<string, unknown>; defaults: Record<string, unknown> } } };
const seed = yaml.load(readFileSync('api_service/data/presets/security-plan-run.yaml', 'utf8'));
const preset = {
  slug: seed.slug, title: seed.title, description: seed.description, tags: seed.tags, version: '1', latestVersion: '1',
  inputSchema: seed.annotations.inputSchema, defaults: seed.annotations.defaults,
};
const catalog = { items: [
  { source: 'builtin', id: 'owasp-asvs-l1', title: 'OWASP ASVS 5.0.0 Level 1', edition: '5.0.0', revision: '1', available: true, reason: null, choice: 'general-best-practices' },
  { source: 'builtin', id: 'iso-27001-readiness', title: 'ISO/IEC 27001', edition: '2022', revision: '1', available: false, reason: 'framework pack is draft, not operative. Register an operative edition.' },
  // A pack registered in a saved environment: selectable with no frontend change.
  { source: 'declared', id: 'operator-policy', title: 'Operator policy', edition: '2026', revision: '2', artifactRef: 'art_policy', sha256: 'c'.repeat(64), available: true, reason: null },
] };
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

const identity = 'a'.repeat(64);
let fetchSpy: MockInstance;
let expanded: Array<Record<string, unknown>>;
let unmount: (() => void) | null = null;

beforeEach(() => {
  expanded = [];
  const inputs = { security_plan: { planRef: 'art_plan', identity } };
  window.history.replaceState({}, '', `/workflows/new?preset=security-plan-run&presetInputs=${encodeURIComponent(JSON.stringify(inputs))}`);
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/ui/info') return json(uiInfo);
    if (url === '/api/presets') return json({ items: [preset] });
    if (url === '/api/presets/security-plan-run') return json(preset);
    if (url === '/api/presets/security-plan-run:expand') {
      const request = JSON.parse(String(init?.body));
      expanded.push(request.inputs);
      return json({ steps: [{ id: 'conclude', type: 'tool', title: 'Conclude security plan run', tool: { id: 'security.assess', inputs: { securityPlan: { planRef: 'art_plan' } } } }],
        appliedTemplate: { slug: 'security-plan-run', version: '1', inputs: request.inputs }, capabilities: [], warnings: [] });
    }
    if (url === '/api/v1/settings/framework-packs') return json(catalog);
    if (url.startsWith('/api/v1/provider-profiles')) return json([]);
    if (url.startsWith('/api/github/branches')) return json({ items: [{ value: 'main', label: 'main', source: 'github' }], defaultBranch: 'main', error: null });
    if (url === '/mcp/tools') return json({ tools: [] });
    if (url.startsWith('/api/workflows/skills')) return json({ items: {} });
    if (url.startsWith('/api/executions')) return json({ items: [] });
    return json({});
  });
});

afterEach(() => {
  unmount?.();
  unmount = null;
  fetchSpy.mockRestore();
  window.history.replaceState({}, '', '/');
});

it('opens a retained plan as an editable plan-run draft and expands it on the server', async () => {
  ({ unmount } = renderWithClient(<DashboardApp payload={payload} />));
  const step = (await screen.findByText('Step 1', {}, { timeout: 10_000 })).closest('section')!;
  await waitFor(() => expect(within(step).getByLabelText('Preset Template')).toHaveProperty('value', 'security-plan-run'));
  await act(async () => { fireEvent.click(within(step).getByRole('button', { name: 'Expand' })); });
  await waitFor(() => expect(expanded).toHaveLength(1));
  expect(expanded[0]).toMatchObject({ security_plan: { planRef: 'art_plan', identity } });
}, 20_000);
