import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { useState } from 'react';
import { act, fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi, type MockInstance } from 'vitest';

import type { BootPayload } from '../boot/parseBootPayload';
import { SchemaCapabilityFields, sourcedOptionErrors } from '../components/SchemaCapabilityFields';
import { loadSchemaOptions } from '../lib/schemaOptionSources';
import { DashboardApp } from './dashboard-app';
import { renderWithClient, screen, waitFor, within } from '../utils/test-utils';

// The Security workspace's start action opens Create with the security plan
// preset. Its inputs are a subject type and catalog-fed standards rendered by
// the shared schema renderer; the saved environment comes from the existing
// assessment picker. No JSON is written.
const yaml = createRequire(import.meta.url)('js-yaml') as { load: (text: string) => { slug: string; title: string; description: string; tags: string[]; annotations: { inputSchema: Record<string, unknown>; defaults: Record<string, unknown> } } };
const seed = yaml.load(readFileSync('api_service/data/presets/security-plan.yaml', 'utf8'));
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
  app: 'moonmind', buildId: 'security-plan-test', apiBase: '/api',
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

let fetchSpy: MockInstance;
let expanded: Array<Record<string, unknown>>;
let unmount: (() => void) | null = null;

beforeEach(() => {
  expanded = [];
  window.history.replaceState({}, '', '/workflows/new?preset=security-plan');
  fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === '/api/ui/info') return json(uiInfo);
    if (url === '/api/presets') return json({ items: [preset] });
    if (url === '/api/presets/security-plan') return json(preset);
    if (url === '/api/presets/security-plan:expand') {
      const request = JSON.parse(String(init?.body));
      expanded.push(request.inputs);
      return json({ steps: [{ id: 'context', type: 'tool', title: 'Prepare planning context', tool: { id: 'security.plan', inputs: { phase: 'context' } } }],
        appliedTemplate: { slug: 'security-plan', version: '1', inputs: request.inputs }, capabilities: [], warnings: [] });
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

it('opens the security plan from its start link and chooses standards from the catalog', async () => {
  ({ unmount } = renderWithClient(<DashboardApp payload={payload} />));
  const step = (await screen.findByText('Step 1', {}, { timeout: 10_000 })).closest('section')!;
  await waitFor(() => expect(within(step).getByLabelText('Preset Template')).toHaveProperty('value', 'security-plan'));
  // The saved environment comes from the existing assessment picker.
  expect(await screen.findByLabelText('Assessment context')).toBeTruthy();

  const subject = within(step).getByLabelText('Subject type') as HTMLSelectElement;
  expect(Array.from(subject.options).map((option) => option.value).filter(Boolean)).toEqual(['application']);
  const standards = await within(step).findByRole('listbox', { name: 'Standards' }) as HTMLSelectElement;
  await waitFor(() => expect(standards.options).toHaveLength(3));
  const [general, iso, declared] = Array.from(standards.options);
  expect(general!.textContent).toContain('General best practices for applications');
  expect(iso!.disabled).toBe(true);
  expect(iso!.textContent).toContain('unavailable: framework pack is draft');
  expect(declared!.disabled).toBe(false);
  expect(within(step).queryByRole('textbox', { name: /Standards/ })).toBeNull();

  // Nothing selected: Expand refuses before any request.
  await act(async () => { fireEvent.click(within(step).getByRole('button', { name: 'Expand' })); });
  expect((await within(step).findAllByText('Choose at least 1 standards.')).length).toBeGreaterThan(0);
  expect(expanded).toHaveLength(0);

  general!.selected = true;
  declared!.selected = true;
  await act(async () => { fireEvent.change(standards); });
  await act(async () => { fireEvent.click(within(step).getByRole('button', { name: 'Expand' })); });
  await waitFor(() => expect(expanded).toHaveLength(1));
  expect(expanded[0]).toEqual({
    subject_type: 'application',
    standards: [
      { id: 'owasp-asvs-l1', edition: '5.0.0' },
      { packId: 'operator-policy', edition: '2026', artifactRef: 'art_policy', revision: '2', sha256: 'c'.repeat(64) },
    ],
  });
}, 20_000);

it('refuses a saved standard that has since become unavailable', async () => {
  await loadSchemaOptions('framework-packs');
  const schema = preset.inputSchema.properties as Record<string, Record<string, unknown>>;
  expect(sourcedOptionErrors('standards', schema.standards!, [{ id: 'owasp-asvs-l1', edition: '5.0.0' }])).toBeNull();
  expect(sourcedOptionErrors('standards', schema.standards!, [{ edition: '2022', id: 'iso-27001-readiness' }]))
    .toBe('ISO/IEC 27001 · 2022 · rev 1 is unavailable: framework pack is draft, not operative. Register an operative edition.');
  expect(sourcedOptionErrors('standards', schema.standards!, [{ id: 'retired-pack', edition: '1' }])).toBe('retired-pack is no longer in the catalog.');
  expect(sourcedOptionErrors('standards', schema.standards!, [])).toBe('Choose at least 1 standards.');
});

it('lists a saved standard the catalog no longer offers so the operator can remove it', async () => {
  const schema = preset.inputSchema as Record<string, unknown> & { properties: Record<string, unknown> };
  const current = { id: 'owasp-asvs-l1', edition: '5.0.0' };
  function Form() {
    const [values, setValues] = useState<Record<string, unknown>>({ standards: [{ id: 'retired-pack', edition: '1' }, current] });
    return <><SchemaCapabilityFields fields={[['standards', schema.properties.standards]]} detail={{ inputSchema: schema }} values={values}
      errors={{}} disabled={false} repositoryOptions={[]} branchOptions={[]}
      onChange={(name, value) => setValues((previous) => ({ ...previous, [name]: value }))} />
      <output data-testid="values">{JSON.stringify(values)}</output></>;
  }
  ({ unmount } = renderWithClient(<Form />));
  const standards = await screen.findByRole('listbox', { name: 'Standards' }) as HTMLSelectElement;
  await waitFor(() => expect(standards.options).toHaveLength(4));
  const retired = Array.from(standards.options).find((option) => option.textContent?.includes('retired-pack'))!;
  expect(retired.textContent).toContain('no longer in the catalog');
  expect(retired.selected).toBe(true);
  expect(retired.disabled).toBe(false);

  retired.selected = false;
  await act(async () => { fireEvent.change(standards); });
  expect(JSON.parse(screen.getByTestId('values').textContent!)).toEqual({ standards: [current] });
  await waitFor(() => expect(standards.options).toHaveLength(3));
  expect(screen.queryByText(/retired-pack/)).toBeNull();
});
