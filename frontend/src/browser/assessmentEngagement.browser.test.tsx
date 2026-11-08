import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { MemoryRouter } from 'react-router-dom';
import { cleanup, renderWithClient, screen, waitFor } from '../utils/test-utils';
import { InstanceSettingsPage } from '../entrypoints/settings';
import { WorkflowStartPage } from '../entrypoints/workflow-start';
import type { BootPayload } from '../boot/parseBootPayload';
import '../styles/dashboard.css';

const key = 'security.assessment_authorizations';
const payload: BootPayload = { page: 'workflow-start', apiBase: '/api', initialData: { dashboardConfig: { system: { defaultAgentRuntime: 'codex_cli', supportedAgentRuntimes: ['codex_cli'], defaultPublishMode: 'none' }, features: { temporalDashboard: { temporalWorkflowEditing: true } } } } };
const authorization = { ref: 'payments', name: 'Payments authorization', generation: 'generation-1', enabled: true, authorityKind: 'self_hosted', authoritySource: 'Owned test system', validFrom: '2026-01-01T00:00:00Z', validUntil: '2027-01-01T00:00:00Z', maxTotalJobSeconds: 120, scope: { assessmentRef: 'payments', targets: [{ workspacePaths: ['.'] }], profileRefs: ['test-profile'], actionClasses: ['passive'], maxJobs: 2, maxJobSeconds: 60, maxCpuMillis: 1000, maxMemoryMiB: 1024, evidenceIntent: 'Retain report' } };
const review = { schemaVersion: 'assessment-engagement/v1', authorizationRef: 'payments', authorizationGeneration: 'generation-1', scopeDigest: 'a'.repeat(64), proposalDigest: 'b'.repeat(64), scope: authorization.scope, sections: [{ label: 'Targets and exclusions', items: ['payments.example.test only; exclude /admin'] }, { label: 'Limits and validity', items: ['2 jobs; 60 seconds/job; 120 seconds total; expires 2027-01-01 UTC'] }], limitations: ['No active exploitation'] };
let launches: unknown[];
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
beforeEach(() => {
  window.sessionStorage.clear(); window.localStorage.clear(); launches = [];
  vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/settings/catalog')) return response({ categories: { Security: [{ key, title: 'Assessment authorizations', type: 'object', ui: 'key_value', category: 'Security', effective_value: { authorizations: [authorization] }, value_version: 2, read_only: false, audit: { store_old_value: true, store_new_value: true, redact: false }, diagnostics: [], applies_to: [], order: 1 }] } });
    if (url === '/api/executions/assessment-proposal') return response(review);
    if (url === '/api/executions' && init?.method === 'POST') { launches.push(JSON.parse(String(init.body))); return response({ detail: 'Test admission rejected; draft retained.' }, 422); }
    if (url.startsWith('/api/v1/provider-profiles')) return response([{ profile_id: 'codex-test', runtime_id: 'codex_cli', account_label: 'Test profile', enabled: true }]);
    if (url.startsWith('/api/workflows/skills')) return response({ items: { worker: [] } });
    return response({ items: [] });
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('assessment engagement browser journeys', () => {
  it('reviews, cancels, edits, and explicitly starts with no launch during preview', async () => {
    window.history.replaceState({}, '', '/workflows/new');
    renderWithClient(<WorkflowStartPage payload={payload} />);
    await screen.findByRole('option', { name: 'Payments authorization' });
    await userEvent.selectOptions(screen.getByLabelText('Assessment authorization'), 'payments');
    await userEvent.type(screen.getByLabelText('Instructions'), 'Inspect admitted targets only.');
    await page.getByRole('button', { name: 'Review rules of engagement', exact: true }).click();
    await screen.findByLabelText('Rules of engagement'); expect(launches).toHaveLength(0);
    await page.getByRole('button', { name: 'Cancel review', exact: true }).click();
    await waitFor(() => expect(screen.queryByLabelText('Rules of engagement')).toBeNull());
    expect(window.location.pathname).toBe('/workflows/new');
    await page.getByRole('button', { name: 'Review rules of engagement', exact: true }).click();
    await screen.findByLabelText('Rules of engagement');
    await userEvent.clear(screen.getByLabelText('Instructions')); await userEvent.type(screen.getByLabelText('Instructions'), 'Inspect a revised plan.');
    expect(screen.queryByLabelText('Rules of engagement')).toBeNull(); expect(launches).toHaveLength(0);
    await page.getByRole('button', { name: 'Review rules of engagement', exact: true }).click();
    await screen.findByLabelText('Rules of engagement');
    await page.screenshot({ path: '../../../artifacts/assessment-engagement-create.png' });
    await page.getByRole('button', { name: 'Start reviewed workflow', exact: true }).click();
    await screen.findByText(/Test admission rejected/);
    expect(launches).toHaveLength(1);
    expect(launches[0]).toMatchObject({ payload: { assessmentAuthorizationRef: 'payments', assessmentReview: review } });
    expect((screen.getByLabelText('Instructions') as HTMLTextAreaElement).value).toBe('Inspect a revised plan.');
  });
  it('preserves edited authorization scope when the Settings departure dialog is dismissed', async () => {
    window.history.replaceState({}, '', '/settings/instance');
    renderWithClient(<MemoryRouter initialEntries={['/settings/instance']}><InstanceSettingsPage payload={payload} /><a href="/workflows/new">Go to Create</a></MemoryRouter>);
    await screen.findByLabelText('Authorization name');
    await userEvent.clear(screen.getByLabelText('Authority source')); await userEvent.type(screen.getByLabelText('Authority source'), 'Updated authority reference');
    await page.getByRole('link', { name: 'Go to Create' }).click();
    await screen.findByRole('dialog'); await page.getByRole('button', { name: 'Stay', exact: true }).click();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect((screen.getByLabelText('Authority source') as HTMLInputElement).value).toBe('Updated authority reference');
    expect(window.location.pathname).toBe('/settings/instance');
    await page.screenshot({ path: '../../../artifacts/assessment-engagement-settings.png' });
  });
});
