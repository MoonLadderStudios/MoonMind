import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { MemoryRouter } from 'react-router-dom';
import { cleanup, renderWithClient, screen, waitFor } from '../utils/test-utils';
import { InstanceSettingsPage } from '../entrypoints/settings';
import { WorkflowStartPage } from '../entrypoints/workflow-start';
import { createAssessmentEnvironment, type AssessmentProjectSnapshot, type AssessmentProjects } from '../lib/assessmentProjects';
import type { BootPayload } from '../boot/parseBootPayload';
import '../styles/dashboard.css';

const key = 'security.assessment_projects';
const payload: BootPayload = { page: 'workflow-start', apiBase: '/api', initialData: { dashboardConfig: {
  system: { defaultAgentRuntime: 'codex_cli', supportedAgentRuntimes: ['codex_cli'], defaultPublishMode: 'none' },
  features: { temporalDashboard: { temporalWorkflowEditing: true } },
} } };
let saved: AssessmentProjects;
let resolveRejects: boolean;
let snapshot: AssessmentProjectSnapshot;
let requests: Array<Record<string, unknown>>;
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
beforeEach(() => {
  window.sessionStorage.clear(); window.localStorage.clear();
  const prod = { ...createAssessmentEnvironment(), id: 'prod', name: 'Production', assets: ['payments-api'] };
  saved = { projects: [{ id: 'payments', name: 'Payments', purpose: 'Review retained evidence', boundaries: 'Payments API only', environments: [prod, { ...prod, id: 'staging', name: 'Staging' }] }] };
  snapshot = { schemaVersion: 'assessment-project/v1', projectId: 'payments', projectName: 'Payments', environmentId: 'prod', environmentName: 'Production', sourceVersion: 2, purpose: 'Review retained evidence', boundaries: 'Payments API only', digest: 'b'.repeat(64), configuration: prod };
  requests = []; resolveRejects = false;
  vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/settings/catalog')) return response({ section: 'user-workspace', categories: { Security: [{ key, title: 'Assessment projects', type: 'object', ui: 'key_value', category: 'Security', source: 'instance_override', apply_mode: 'next_task', activation_state: 'active', effective_value: saved, value_version: 2, read_only: false, audit: { store_old_value: true, store_new_value: true, redact: false }, diagnostics: [], applies_to: [], order: 1 }] } });
    if (url === '/api/v1/settings' && init?.method === 'PATCH') {
      const request = JSON.parse(String(init.body)); saved = request.changes[key]; requests.push(request); return response({});
    }
    if (url.endsWith('/assessment-projects/resolve')) {
      const request = JSON.parse(String(init?.body)); requests.push(request);
      if (resolveRejects) return response({ detail: 'Saved context changed. Review the current saved environment.' }, 409);
      const environment = saved.projects[0]!.environments.find(item => item.id === request.environmentId)!;
      return response({ snapshot: { ...snapshot, environmentId: environment.id, environmentName: environment.name, configuration: { ...environment, ...request.overrides } }, gaps: [] });
    }
    if (url === '/mcp/tools') return response({ tools: [{ name: 'security.assess', inputSchema: { type: 'object', 'x-assessment-project-defaults': true } }, { name: 'jira.get_issue', inputSchema: { type: 'object' } }] });
    if (url.startsWith('/api/v1/provider-profiles')) return response([{ profile_id: 'codex-test', runtime_id: 'codex_cli', account_label: 'Test profile', enabled: true }]);
    if (url.startsWith('/api/workflows/skills')) return response({ items: { worker: [] } });
    return response({ items: [] });
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('reusable assessment context browser journeys', () => {
  it('starts with ordinary steps and shows assessment setup only when a relevant Tool is chosen', async () => {
    window.history.replaceState({}, '', '/workflows/new');
    renderWithClient(<WorkflowStartPage payload={payload} />);
    await screen.findByText('Step 1');
    expect(screen.queryByText(/Repository scanners/)).toBeNull();
    expect(screen.queryByLabelText('Assessment context')).toBeNull();
    expect(screen.queryByLabelText('Assessment authorization')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Add assessment setup' })).toBeNull();
    await page.screenshot({ path: '../../../artifacts/create-ordinary-uncluttered.png' });
    // The segmented control's native radio is hidden behind its clickable label.
    await userEvent.click(screen.getByRole('radio', { name: 'Tool', exact: true }).closest('label')!);
    await page.getByRole('button', { name: 'jira.get_issue', exact: true }).click();
    expect(screen.queryByLabelText('Assessment context')).toBeNull();
    await page.getByRole('button', { name: 'security.assess', exact: true }).click();
    const context = await screen.findByLabelText('Assessment context');
    expect(screen.getByText('Step 1').compareDocumentPosition(context) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await screen.findByRole('option', { name: 'Payments' });
    await userEvent.selectOptions(screen.getByLabelText('Assessment system'), 'payments');
    await userEvent.selectOptions(screen.getByLabelText('Assessment environment'), 'prod');
    await page.getByText('Override context for this run', { exact: true }).click();
    await userEvent.clear(screen.getByLabelText('Assets')); await userEvent.type(screen.getByLabelText('Assets'), 'draft-api');
    await page.getByText('Security assessment setup', { exact: true }).click();
    await page.getByRole('button', { name: 'jira.get_issue', exact: true }).click();
    await page.getByText('Security assessment setup', { exact: true }).click();
    expect((screen.getByLabelText('Assets') as HTMLTextAreaElement).value).toBe('draft-api');
    await page.getByRole('button', { name: 'security.assess', exact: true }).click();
    await page.screenshot({ path: '../../../artifacts/create-assessment-opt-in.png' });
  });
  it('edits and copies nested Settings, saves, and dismisses the departure guard without losing the draft', async () => {
    window.history.replaceState({}, '', '/settings/instance');
    renderWithClient(<MemoryRouter initialEntries={['/settings/instance']}><InstanceSettingsPage payload={payload} /><a href="/workflows/new">Go to Create</a></MemoryRouter>);
    await screen.findByLabelText('System name');
    const purpose = screen.getByLabelText('Purpose');
    await userEvent.clear(purpose); await userEvent.type(purpose, 'Browser-verified evidence review');
    await userEvent.selectOptions(screen.getAllByLabelText('Evidence model delivery')[0]!, 'local_only');
    await userEvent.type(screen.getAllByLabelText('Permitted Provider Profile references (optional)')[0]!, 'review-private');
    await page.getByRole('button', { name: 'Save assessment context' }).click();
    await screen.findByText('Assessment context saved.');
    expect(saved.projects[0]!.purpose).toBe('Browser-verified evidence review');
    expect(saved.projects[0]!.environments[0]!.evidenceProcessing).toEqual({ modelDelivery: 'local_only', allowedProfileRefs: ['review-private'] });
    expect(screen.getAllByText(/Local-only model delivery is currently unavailable/).length).toBeGreaterThan(0);
    await page.getByRole('button', { name: 'Copy system' }).click();
    expect(screen.getAllByLabelText('System ID')).toHaveLength(2);
    await page.getByRole('link', { name: 'Go to Create' }).click();
    await screen.findByRole('dialog');
    await page.getByRole('button', { name: 'Stay', exact: true }).click();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(window.location.pathname).toBe('/settings/instance');
    expect(screen.getAllByLabelText('System name')).toHaveLength(2);
    expect((screen.getAllByLabelText('Evidence model delivery')[0] as HTMLSelectElement).value).toBe('local_only');
    expect((screen.getAllByLabelText('Permitted Provider Profile references (optional)')[0] as HTMLTextAreaElement).value).toBe('review-private');
    await page.screenshot({ path: '../../../artifacts/assessment-settings-browser.png' });
  });
  it('uses normal Create to select, clear, retry rejected context, and keep run-only overrides', async () => {
    window.history.replaceState({}, '', '/workflows/new');
    renderWithClient(<WorkflowStartPage payload={payload} />);
    await page.getByLabelText('Advanced mode', { exact: true }).click();
    await page.getByRole('button', { name: 'Add assessment setup', exact: true }).click();
    await page.getByLabelText('Advanced mode', { exact: true }).click();
    await screen.findByRole('option', { name: 'Payments' });
    await userEvent.selectOptions(screen.getByLabelText('Assessment system'), 'payments');
    await userEvent.selectOptions(screen.getByLabelText('Assessment environment'), 'prod');
    await page.getByRole('button', { name: 'Load assessment context', exact: true }).click();
    await screen.findByLabelText('Frozen assessment context');
    await page.getByRole('button', { name: 'Remove assessment context' }).click();
    expect(screen.queryByLabelText('Frozen assessment context')).toBeNull();
    expect((screen.getByLabelText('Assessment system') as HTMLSelectElement).value).toBe('');
    expect(window.location.pathname).toBe('/workflows/new');
    await userEvent.selectOptions(screen.getByLabelText('Assessment system'), 'payments');
    await userEvent.selectOptions(screen.getByLabelText('Assessment environment'), 'staging');
    await page.getByText('Override context for this run', { exact: true }).click();
    await userEvent.clear(screen.getByLabelText('Assets')); await userEvent.type(screen.getByLabelText('Assets'), 'run-only-api');
    await userEvent.selectOptions(screen.getByLabelText('Evidence model delivery'), 'disabled');
    resolveRejects = true;
    await page.getByRole('button', { name: 'Load assessment context', exact: true }).click();
    await screen.findByText(/Your draft changes are retained/);
    expect((screen.getByLabelText('Assets') as HTMLTextAreaElement).value).toBe('run-only-api');
    expect((screen.getByLabelText('Evidence model delivery') as HTMLSelectElement).value).toBe('disabled');
    expect(window.location.pathname).toBe('/workflows/new');
    resolveRejects = false;
    await page.getByRole('button', { name: 'Load assessment context', exact: true }).click();
    await waitFor(() => expect(screen.getByLabelText('Frozen assessment context').textContent).toContain('run-only-api'));
    expect(screen.getByLabelText('Frozen assessment context').textContent).toContain('disabled');
    expect(saved.projects[0]!.environments[1]!.evidenceProcessing).toBeUndefined();
    expect(saved.projects[0]!.environments[1]!.assets).toEqual(['payments-api']);
    expect(requests.at(-1)).toMatchObject({ expectedVersion: 2, environmentId: 'staging', overrides: { assets: ['run-only-api'], evidenceProcessing: { modelDelivery: 'disabled', allowedProfileRefs: [] } } });
    await page.screenshot({ path: '../../../artifacts/assessment-create-browser.png' });
  });
});
