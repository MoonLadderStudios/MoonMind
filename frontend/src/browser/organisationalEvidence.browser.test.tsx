import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { cleanup, renderWithClient, screen, waitFor } from '../utils/test-utils';
import { WorkflowStartPage } from '../entrypoints/workflow-start';
import type { BootPayload } from '../boot/parseBootPayload';
import '../styles/dashboard.css';

const payload: BootPayload = { page: 'workflow-start', apiBase: '/api', initialData: { dashboardConfig: {
  system: { defaultAgentRuntime: 'codex_cli', supportedAgentRuntimes: ['codex_cli'], defaultPublishMode: 'none' },
  features: { temporalDashboard: { temporalWorkflowEditing: true } },
} } };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
let uploadAttempts: number;
let launches: Array<Record<string, unknown>>;
beforeEach(() => {
  window.sessionStorage.clear(); window.localStorage.clear();
  window.history.replaceState({}, '', '/workflows/new');
  uploadAttempts = 0; launches = [];
  vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url === '/mcp/tools') return response({ tools: [{ name: 'security.assess', inputSchema: { type: 'object', properties: { controlAssessment: { type: 'object' } } } }] });
    if (url.startsWith('/api/v1/provider-profiles')) return response([{ profile_id: 'codex-test', runtime_id: 'codex_cli', account_label: 'Test profile', enabled: true }]);
    if (url.startsWith('/api/workflows/skills')) return response({ items: { worker: [] } });
    if (url === '/api/artifacts' && init?.method === 'POST') {
      if (++uploadAttempts === 1) return response({ detail: 'Controlled unavailable upload' }, 503);
      return response({ artifact_ref: { artifact_id: 'art_policy' }, upload: { mode: 'single_put', upload_url: '/api/artifacts/art_policy/content', required_headers: {} } });
    }
    if (url === '/api/artifacts/art_policy/content' || url === '/api/artifacts/art_policy/complete') return response({ artifact_id: 'art_policy' });
    if (url === '/api/executions' && init?.method === 'POST') {
      launches.push(JSON.parse(String(init.body)));
      return response({ detail: 'Controlled evidence admission rejection; retry with this draft.' }, 422);
    }
    return response({ items: [] });
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('organisational evidence browser journey', () => {
  it('retries document import, preserves scoped edits and submits through ordinary Create without image attachments', async () => {
    renderWithClient(<WorkflowStartPage payload={payload} />);
    await screen.findByText('Step 1');
    expect(screen.queryByText('Organisational evidence requests and review')).toBeNull();
    await userEvent.fill(screen.getByLabelText('Instructions'), 'Review the policy and missing operational record.');
    await userEvent.click(screen.getByRole('radio', { name: 'Tool' }).closest('label')!);
    await page.getByRole('button', { name: 'security.assess', exact: true }).click();
    await page.getByText('Edit JSON', { exact: true }).click();
    const editor = screen.getByLabelText('Tool Inputs (JSON object)') as HTMLTextAreaElement;
    await userEvent.fill(editor, JSON.stringify({ controlAssessment: { requirements: [{ id: 'policy', organisationalEvidence: [] }] } }));
    await page.getByText('Organisational evidence requests and review', { exact: true }).click();
    await page.getByLabelText('Import organisational evidence', { exact: true }).upload(new File(['Synthetic controlled policy'], 'policy.txt', { type: 'text/plain' }));
    await page.getByRole('button', { name: 'Upload evidence', exact: true }).click();
    await screen.findByText(/Evidence import did not complete/);
    expect((screen.getByLabelText('Import organisational evidence') as HTMLInputElement).files?.[0]?.name).toBe('policy.txt');
    expect(launches).toHaveLength(0);
    await page.getByRole('button', { name: 'Upload evidence', exact: true }).click();
    await screen.findByText('art_policy');
    expect(uploadAttempts).toBe(2);
    await userEvent.selectOptions(screen.getByLabelText('Evidence requirement'), '0');
    await page.getByRole('button', { name: 'Add evidence request', exact: true }).click();
    const inputs = JSON.parse(editor.value);
    const request = inputs.controlAssessment.requirements[0].organisationalEvidence[0];
    request.revisions = [{ artifactRef: 'art_policy', kind: 'document', source: 'Controlled fixture', collectedAt: '2026-11-07T12:00:00Z', expiresAt: '2026-12-01T00:00:00Z', period: { start: '2026-11-01', end: '2026-11-30' }, population: ['app'], sensitivity: 'internal', review: { outcome: 'accepted', purpose: 'design', reviewer: 'Operator', reason: 'Policy design only, not adoption', reviewedAt: '2026-11-07T12:00:00Z', period: { start: '2026-11-07', end: '2026-11-07' }, population: ['app'] } }];
    await userEvent.fill(editor, JSON.stringify(inputs));
    await page.getByRole('button', { name: 'Add evidence request', exact: true }).click();
    const submitted = JSON.parse(editor.value);
    expect(submitted.controlAssessment.requirements[0].organisationalEvidence[1].revisions).toEqual([]);
    await userEvent.fill(editor, '{"controlAssessment":');
    await screen.findByText('Correct the Tool input JSON before editing evidence. Existing inputs are preserved.');
    expect((screen.getByRole('button', { name: 'Add evidence request' }).closest('fieldset') as HTMLFieldSetElement).disabled).toBe(true);
    await userEvent.fill(editor, JSON.stringify(submitted));
    await page.getByText('Organisational evidence requests and review', { exact: true }).click();
    await page.getByText('Organisational evidence requests and review', { exact: true }).click();
    expect(JSON.parse(editor.value)).toEqual(submitted);
    expect(screen.getByText(/Assertions and generated drafts cannot establish adoption/)).toBeTruthy();
    await page.screenshot({ path: '../../../artifacts/organisational-evidence-import-review.png' });
    await page.getByRole('button', { name: 'Start Workflow', exact: true }).click();
    await screen.findByText(/Controlled evidence admission rejection/);
    expect(launches).toHaveLength(1);
    expect(launches[0]).toMatchObject({ payload: { task: { steps: [{ tool: { inputs: submitted } }] } } });
    expect(JSON.stringify(launches[0])).not.toContain('inputAttachments');
    expect(JSON.parse(editor.value)).toEqual(submitted);
    expect(window.location.pathname).toBe('/workflows/new');
    await page.getByRole('button', { name: 'Start Workflow', exact: true }).click();
    await waitFor(() => expect(launches).toHaveLength(2));
    expect(launches[1]).toMatchObject({ payload: { task: { steps: [{ tool: { inputs: submitted } }] } } });
  });
});
