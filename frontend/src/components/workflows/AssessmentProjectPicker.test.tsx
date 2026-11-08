import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { renderWithClient } from '../../utils/test-utils';
import { AssessmentProjectPicker } from './AssessmentProjectPicker';
import { createAssessmentEnvironment, type AssessmentProjectSnapshot } from '../../lib/assessmentProjects';
const environment = { ...createAssessmentEnvironment(), id: 'prod', name: 'Production', assets: ['payment-api'] };
const snapshot: AssessmentProjectSnapshot = { schemaVersion: 'assessment-project/v1', projectId: 'payments', projectName: 'Payments', environmentId: 'prod', environmentName: 'Production', purpose: 'Review payments', boundaries: 'Payment API only', sourceVersion: 2, configuration: environment, digest: 'a'.repeat(64) };
const catalog = { categories: { security: [{ key: 'security.assessment_projects', value_version: 2, effective_value: { projects: [{ id: 'payments', name: 'Payments', purpose: snapshot.purpose, boundaries: snapshot.boundaries, environments: [environment, { ...environment, id: 'staging', name: 'Staging' }] }] } }] } };
const fetchMock = vi.fn();
function Harness({ initial = null, pickerKey = '' }: { initial?: AssessmentProjectSnapshot | null; pickerKey?: string }) {
  const [value, setValue] = useState(initial);
  const [status, setStatus] = useState<string | null>(null);
  return <><AssessmentProjectPicker key={pickerKey} value={value} onChange={setValue} onStatusChange={setStatus} /><output aria-label="Context status">{status || 'ready'}</output><output aria-label="Context payload">{JSON.stringify(value)}</output></>;
}
beforeEach(() => { vi.stubGlobal('fetch', fetchMock); fetchMock.mockImplementation(async (_url, options) => ({ ok: true, json: async () => options?.method === 'POST' ? { snapshot, gaps: [] } : catalog })); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); fetchMock.mockReset(); });
async function selectEnvironment(name = 'prod') {
  await screen.findByRole('option', { name: 'Payments' });
  fireEvent.change(screen.getByLabelText('Assessment system'), { target: { value: 'payments' } });
  fireEvent.change(screen.getByLabelText('Assessment environment'), { target: { value: name } });
}
describe('assessment project selection in Create', () => {
  it('loads a frozen draft with explicit overrides, leaving saved settings unchanged', async () => {
    renderWithClient(<Harness />);
    await selectEnvironment();
    fireEvent.change(screen.getByLabelText('Assets'), { target: { value: 'one-run-asset' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load assessment context' }));
    await waitFor(() => expect(screen.getByLabelText('Context status').textContent).toBe('ready'));
    const request = fetchMock.mock.calls.find(([, options]) => options?.method === 'POST');
    expect(request?.[0]).toBe('/api/v1/settings/assessment-projects/resolve');
    expect(JSON.parse(request?.[1].body)).toMatchObject({ projectId: 'payments', environmentId: 'prod', expectedVersion: 2, overrides: { assets: ['one-run-asset'] } });
    expect(screen.getByLabelText('Context payload').textContent).toContain('payment-api');
    expect(catalog.categories.security[0]!.effective_value.projects[0]!.environments[0]!.assets).toEqual(['payment-api']);
  });
  it('keeps gaps actionable and blocks starting without substituting references', async () => {
    fetchMock.mockImplementation(async (_url, options) => ({ ok: true, json: async () => options?.method === 'POST' ? { snapshot, gaps: [{ field: 'artifactRefs[0]', code: 'missing', message: 'Artifact art_gone is unavailable. Choose another retained artifact.' }] } : catalog }));
    renderWithClient(<Harness />);
    await selectEnvironment();
    fireEvent.click(screen.getByRole('button', { name: 'Load assessment context' }));
    await screen.findByText(/Artifact art_gone is unavailable/);
    expect(screen.getByLabelText('Context status').textContent).not.toBe('ready');
  });
  it('ignores late loads after a newer selection or context removal', async () => {
    let resolve!: (value: unknown) => void;
    fetchMock.mockImplementation(async (_url, options) => options?.method === 'POST' ? new Promise((r) => { resolve = r; }) : { ok: true, json: async () => catalog });
    renderWithClient(<Harness />);
    await selectEnvironment();
    fireEvent.click(screen.getByRole('button', { name: 'Load assessment context' }));
    fireEvent.change(screen.getByLabelText('Assessment environment'), { target: { value: 'staging' } });
    await act(async () => { resolve({ ok: true, json: async () => ({ snapshot, gaps: [] }) }); });
    await waitFor(() => expect(screen.getByLabelText('Assessment environment')).toHaveProperty('value', 'staging'));
    expect(screen.getByLabelText('Context payload').textContent).toBe('null');
    expect(screen.getByLabelText('Context status').textContent).not.toBe('ready');
    fireEvent.click(screen.getByRole('button', { name: 'Remove assessment context' }));
    expect(screen.getByLabelText('Context status').textContent).toBe('ready');
  });
  it('ignores a late load after context removal', async () => {
    let resolve!: (value: unknown) => void;
    fetchMock.mockImplementation(async (_url, options) => options?.method === 'POST' ? new Promise((r) => { resolve = r; }) : { ok: true, json: async () => catalog });
    renderWithClient(<Harness />);
    await selectEnvironment();
    fireEvent.click(screen.getByRole('button', { name: 'Load assessment context' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove assessment context' }));
    await act(async () => { resolve({ ok: true, json: async () => ({ snapshot, gaps: [] }) }); });
    expect(screen.getByLabelText('Context payload').textContent).toBe('null');
    expect(screen.getByLabelText('Context status').textContent).toBe('ready');
  });
  it('keeps the first frozen selection across Settings refresh and explicitly loads a second version', async () => {
    renderWithClient(<Harness initial={snapshot} />);
    await screen.findByRole('option', { name: 'Payments' });
    const next = structuredClone(catalog);
    next.categories.security[0]!.value_version = 3;
    next.categories.security[0]!.effective_value.projects[0]!.environments[0]!.assets = ['new-saved-api'];
    fetchMock.mockImplementation(async (_url, options) => ({ ok: true, json: async () => options?.method === 'POST'
      ? { snapshot: { ...snapshot, sourceVersion: 3, configuration: { ...environment, assets: ['second-run-override'] } }, gaps: [] }
      : next }));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh saved choices' })); });
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, options]) => options?.method !== 'POST').length).toBe(2));
    expect(screen.getByLabelText('Context payload').textContent).toContain('payment-api');
    fireEvent.click(screen.getByRole('button', { name: 'Review current saved environment' }));
    await waitFor(() => expect(screen.getByLabelText('Assets')).toHaveProperty('value', 'new-saved-api'));
    fireEvent.change(screen.getByLabelText('Assets'), { target: { value: 'second-run-override' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load assessment context' }));
    await waitFor(() => expect(screen.getByLabelText('Context payload').textContent).toContain('second-run-override'));
    const request = fetchMock.mock.calls.find(([, options]) => options?.method === 'POST');
    expect(JSON.parse(request?.[1].body)).toMatchObject({ expectedVersion: 3, overrides: { assets: ['second-run-override'] } });
    expect(next.categories.security[0]!.effective_value.projects[0]!.environments[0]!.assets).toEqual(['new-saved-api']);
  });
  it('invalidates a pending load and clears its status when navigation remounts the picker', async () => {
    let resolve!: (value: unknown) => void;
    fetchMock.mockImplementation(async (_url, options) => options?.method === 'POST' ? new Promise((r) => { resolve = r; }) : { ok: true, json: async () => catalog });
    const view = renderWithClient(<Harness pickerKey="first-route" />);
    await selectEnvironment();
    fireEvent.click(screen.getByRole('button', { name: 'Load assessment context' }));
    view.rerender(<Harness pickerKey="second-route" />);
    await act(async () => { resolve({ ok: true, json: async () => ({ snapshot, gaps: [] }) }); });
    expect(screen.getByLabelText('Context payload').textContent).toBe('null');
    expect(screen.getByLabelText('Context status').textContent).toBe('ready');
  });
  it('shows historical context without silently loading changed Settings', async () => {
    const historical = { ...snapshot, sourceVersion: 1, configuration: { ...environment, assets: ['historical-only'] } };
    renderWithClient(<Harness initial={historical} />);
    await screen.findByText(/Frozen assessment context/);
    await screen.findByRole('option', { name: 'Payments' });
    expect(screen.getByLabelText('Context payload').textContent).toContain('historical-only');
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(0);
  });
  it('preserves run-specific edits when resolving fails and lets the user retry', async () => {
    fetchMock.mockImplementation(async (_url, options) => options?.method === 'POST' ? new Response(JSON.stringify({ detail: 'Settings changed. Reload saved choices.' }), { status: 409 }) : { ok: true, json: async () => catalog });
    renderWithClient(<Harness />);
    await selectEnvironment();
    fireEvent.change(screen.getByLabelText('Assets'), { target: { value: 'keep-this-draft' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load assessment context' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Settings changed'));
    expect(screen.getByLabelText('Assets')).toHaveProperty('value', 'keep-this-draft');
    expect(screen.getByLabelText('Context status').textContent).not.toBe('ready');
  });
});
