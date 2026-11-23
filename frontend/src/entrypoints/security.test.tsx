import { afterEach, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { fireEvent, screen } from '@testing-library/react';
import { renderWithClient } from '../utils/test-utils';
import SecurityPage from './security';
import { authorizationStatus } from '../lib/assessmentAuthorizationStatus';

const payload = { page: 'security', apiBase: '/api' };
afterEach(() => vi.restoreAllMocks());
function responses(presetStatus?: number, presetCode = 'preset_catalog_disabled') {
  return vi.spyOn(window, 'fetch').mockImplementation(async input => {
    const url = new URL(String(input), window.location.origin);
    let data: unknown;
    if (url.pathname.endsWith('/presets') && presetStatus) return new Response(JSON.stringify({ detail: { code: presetCode, message: 'Preset catalog unavailable' } }), { status: presetStatus, headers: { 'Content-Type': 'application/json' } });
    if (url.pathname.endsWith('/presets')) data = { items: [{ slug: 'security-web-api', title: 'Web and API', tags: ['security', 'assessment'] }, { slug: 'unrelated', tags: [] }] };
    else if (url.pathname === '/api/executions') data = { items: [{ workflowId: 'assess:1', title: 'Web review', status: 'completed', createdAt: '2026-11-18T12:00:00Z' }], nextPageToken: null };
    else if (url.pathname.endsWith('/artifacts')) data = { artifacts: [{ artifact_id: 'art_report', links: [{ link_type: 'report.primary', label: 'Findings' }], raw_access_allowed: false, default_read_ref: { artifact_id: 'art_preview' } }] };
    else data = { namespace: 'default', runId: 'run1', inputParameters: { assessmentProject: { schemaVersion: 'assessment-project/v1', projectId: 'store', projectName: 'Store', environmentId: 'test', environmentName: 'Test' } } };
    return { ok: true, json: async () => data } as Response;
  });
}
it('lists assessment workflows and links back to the existing runner without launching work', async () => {
  const fetch = responses();
  renderWithClient(<MemoryRouter initialEntries={['/security']}><SecurityPage payload={payload} /></MemoryRouter>);
  expect(await screen.findByRole('cell', { name: 'Web review' })).toBeTruthy();
  expect(await screen.findByRole('cell', { name: 'Store / Test' })).toBeTruthy();
  // The start action leads to the security plan preset in Create.
  expect(screen.getByRole('link', { name: 'Start a security plan' }).getAttribute('href')).toBe('/workflows/new?preset=security-plan');
  expect(screen.getByRole('link', { name: 'Start another assessment' }).getAttribute('href')).toBe('/workflows/new');
  expect(screen.getByRole('link', { name: 'Open / resume' }).getAttribute('href')).toBe('/workflows/assess%3A1');
  expect(fetch.mock.calls.some(([input]) => String(input).includes('targetSkillIn=') && !String(input).includes('unrelated'))).toBe(true);
});
it('uses existing report evidence without granting raw access', async () => {
  responses();
  renderWithClient(<MemoryRouter initialEntries={['/security/reports']}><SecurityPage payload={payload} /></MemoryRouter>);
  await screen.findByRole('cell', { name: 'Store / Test' });
  fireEvent.change(screen.getByLabelText('Assessment'), { target: { value: 'assess:1' } });
  expect(await screen.findByText('Findings')).toBeTruthy();
  expect(screen.getByRole('link', { name: 'Review evidence' }).getAttribute('href')).toBe('/workflows/assess%3A1/evidence');
  expect(screen.queryByRole('link', { name: 'Download' })).toBeNull();
  expect(screen.getByRole('link', { name: 'Preview' }).getAttribute('href')).toBe('/api/artifacts/art_preview/download');
});
it('distinguishes authorization validity from scope admission', () => {
  const now = Date.parse('2026-11-18T12:00:00Z');
  const record = { enabled: true, validFrom: '2026-11-17T00:00:00Z', validUntil: '2026-11-19T00:00:00Z' };
  expect(authorizationStatus(record, now)).toBe('Current');
  expect(authorizationStatus({ ...record, enabled: false }, now)).toBe('Disabled');
  expect(authorizationStatus({ ...record, validUntil: '2026-11-18T12:00:00Z' }, now)).toBe('Expired');
  expect(authorizationStatus({ ...record, validFrom: '2026-11-19T00:00:00Z', validUntil: '2026-11-20T00:00:00Z' }, now)).toBe('Not yet valid');
  expect(authorizationStatus({ ...record, validUntil: 'bad' }, now)).toBe('Invalid validity window');
});

it.each(['/security/assessments', '/security/reports'])('loads direct assessment history with a disabled catalog at %s', async path => {
  const fetch = responses(404);
  renderWithClient(<MemoryRouter initialEntries={[path]}><SecurityPage payload={payload} /></MemoryRouter>);
  expect(await screen.findByRole('cell', { name: 'Web review' })).toBeTruthy();
  expect(screen.getByText(/preset-based history is unavailable/i)).toBeTruthy();
  const request = fetch.mock.calls.find(([input]) => new URL(String(input), window.location.origin).pathname === '/api/executions');
  expect(new URL(String(request?.[0]), window.location.origin).searchParams.get('targetSkillIn')).toBe('security-review,security.assess');
  if (path.endsWith('/reports')) {
    await screen.findByRole('cell', { name: 'Store / Test' });
    fireEvent.change(screen.getByLabelText('Assessment'), { target: { value: 'assess:1' } });
    expect(await screen.findByText('Findings')).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Download' })).toBeNull();
  }
});
it.each([403, 404, 500])('does not treat an unrelated catalog HTTP %s as disabled', async status => {
  const fetch = responses(status, 'catalog_request_failed');
  renderWithClient(<MemoryRouter initialEntries={['/security']}><SecurityPage payload={payload} /></MemoryRouter>);
  expect(await screen.findByText(/Preset catalog unavailable/)).toBeTruthy();
  expect(fetch.mock.calls.some(([input]) => new URL(String(input), window.location.origin).pathname === '/api/executions')).toBe(false);
});

it('runs a retained security plan from its report through Create', async () => {
  const identity = 'a'.repeat(64);
  vi.spyOn(window, 'fetch').mockImplementation(async input => {
    const url = new URL(String(input), window.location.origin);
    let data: unknown;
    if (url.pathname.endsWith('/presets')) data = { items: [{ slug: 'security-plan', title: 'Security plan', tags: ['security', 'assessment'] }] };
    else if (url.pathname === '/api/executions') data = { items: [{ workflowId: 'plan:1', title: 'Shop plan', status: 'completed', createdAt: '2026-11-23T12:00:00Z' }], nextPageToken: null };
    else if (url.pathname.endsWith('/artifacts')) data = { artifacts: [
      { artifact_id: 'art_plan', links: [{ link_type: 'report.structured', label: 'Security plan' }, { link_type: 'security.plan' }], metadata: { producer: 'security.plan', subject: identity } },
      // A readable report is not a plan, and a plan-shaped subject alone is not one either.
      { artifact_id: 'art_report', links: [{ link_type: 'report.primary', label: 'Plan report' }], metadata: { subject: identity } },
    ] };
    else data = { namespace: 'default', runId: 'run1', inputParameters: {} };
    return { ok: true, json: async () => data } as Response;
  });
  renderWithClient(<MemoryRouter initialEntries={['/security/reports']}><SecurityPage payload={payload} /></MemoryRouter>);
  await screen.findByRole('cell', { name: 'Shop plan' });
  fireEvent.change(screen.getByLabelText('Assessment'), { target: { value: 'plan:1' } });
  expect(await screen.findByText('Plan report')).toBeTruthy();
  const runs = screen.getAllByRole('link', { name: 'Run this plan' });
  expect(runs).toHaveLength(1);
  const href = new URL(runs[0]!.getAttribute('href')!, window.location.origin);
  expect(href.pathname).toBe('/workflows/new');
  expect(href.searchParams.get('preset')).toBe('security-plan-run');
  expect(JSON.parse(href.searchParams.get('presetInputs')!)).toEqual({ security_plan: { planRef: 'art_plan', identity } });
});
