import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BrowserRouter } from 'react-router-dom';

import { renderWithClient } from '../utils/test-utils';
import OmnigentInventoryPage from './omnigent-inventory';

describe('OmnigentInventoryPage', () => {
  const listResponse = [{
    profileId: 'codex-team',
    displayName: 'Team Codex',
    state: 'active',
    activeVersion: 2,
    defaultForRuntime: true,
    versions: [{
      version: 2,
      digest: `sha256:${'a'.repeat(64)}`,
      validationResult: { ready: true },
    }, {
      version: 1,
      digest: `sha256:${'b'.repeat(64)}`,
      validationResult: null,
    }],
  }];
  const renderPage = (payload: Parameters<typeof OmnigentInventoryPage>[0]['payload']) =>
    renderWithClient(<BrowserRouter><OmnigentInventoryPage payload={payload} /></BrowserRouter>);
  beforeEach(() => {
    window.history.replaceState({}, '', '/omnigent/agents');
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/omnigent/agent-profiles') {
        return { ok: true, json: async () => listResponse };
      }
      return {
        ok: true,
        json: async () => [{ id: 'agent-1', name: 'Codex', status: 'ready', description: 'Coding agent' }],
      };
    }));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses the advertised same-origin compact list once and keeps agent filters distinct', async () => {
    renderPage({
      page: 'omnigent-inventory',
      apiBase: '/api',
      features: { omnigentAgents: true },
      initialData: { uiEndpoints: { omnigentAgents: '/api/omnigent/api/agents' } },
    });

    expect(await screen.findByText('Codex')).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch).toHaveBeenCalledWith('/api/omnigent/api/agents', { credentials: 'same-origin' });
    expect(fetch).toHaveBeenCalledWith('/api/omnigent/agent-profiles', { credentials: 'same-origin' });
    expect(screen.getByText('Team Codex')).toBeTruthy();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'missing' } });
    expect(await screen.findByText('No agents match this filter.')).toBeTruthy();
    expect(window.location.search).toContain('omnigent_agents_q=missing');
  });

  it('preserves the shell state when a list request fails', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({ ok: false, status: 503 } as Response);
    renderPage({
      page: 'omnigent-inventory', apiBase: '/api', features: { omnigentAgents: true },
      initialData: { uiEndpoints: { omnigentAgents: '/api/omnigent/api/agents' } },
    });
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Agents' })).toBeTruthy();
  });

  it('offers activation when a newer validated version is ready', async () => {
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/omnigent/agent-profiles') {
        return {
          ok: true,
          json: async () => [{ ...listResponse[0], activeVersion: 1 }],
        } as Response;
      }
      return { ok: true, json: async () => [] } as Response;
    });
    renderPage({
      page: 'omnigent-inventory',
      apiBase: '/api',
      features: { omnigentAgents: true },
      initialData: { uiEndpoints: { omnigentAgents: '/api/omnigent/api/agents' } },
    });

    expect(await screen.findByRole('button', { name: 'Activate Team Codex' })).toBeTruthy();
  });

  it('creates an upstream profile through structured controls without raw JSON', async () => {
    renderPage({
      page: 'omnigent-inventory', apiBase: '/api', features: { omnigentAgents: true },
      initialData: { uiEndpoints: { omnigentAgents: '/api/omnigent/api/agents' } },
    });
    await screen.findByText('Team Codex');
    fireEvent.click(screen.getByRole('button', { name: 'Create from upstream or bundle' }));
    expect(screen.queryByLabelText('Normalized profile document (JSON)')).toBeNull();
    fireEvent.change(screen.getByLabelText('Profile id'), { target: { value: 'new-codex' } });
    fireEvent.change(screen.getByLabelText('Display name'), { target: { value: 'New Codex' } });
    fireEvent.change(screen.getByLabelText('Stable upstream agent id'), { target: { value: 'agent-42' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save immutable profile version' }));

    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url, init]) =>
      String(url) === '/api/omnigent/agent-profiles' && (init as RequestInit | undefined)?.method === 'POST')).toBe(true));
    const call = vi.mocked(fetch).mock.calls.find(([url, init]) =>
      String(url) === '/api/omnigent/agent-profiles' && (init as RequestInit | undefined)?.method === 'POST');
    const body = JSON.parse(String((call?.[1] as RequestInit).body));
    expect(body.document).toMatchObject({
      endpointRef: 'default', source: { upstreamId: 'agent-42' }, harness: 'codex-native',
      continuations: { checkpoint: true, branch: true, remediation: true },
    });
  });

  it('does not fetch policy actions without a capability contract', async () => {
    window.history.replaceState({}, '', '/omnigent/policies');
    renderPage({ page: 'omnigent-inventory', apiBase: '/api', features: { omnigentPolicies: false } });
    expect(screen.getByRole('alert').textContent).toContain('not available');
    await waitFor(() => expect(fetch).not.toHaveBeenCalled());
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('keeps policy deep links on the policies inventory', async () => {
    window.history.replaceState({}, '', '/omnigent/policies/default');
    renderPage({
      page: 'omnigent-inventory',
      apiBase: '/api',
      features: { omnigentPolicies: true },
      initialData: { uiEndpoints: { omnigentPolicies: '/api/omnigent/api/policies' } },
    });

    expect(await screen.findByRole('heading', { name: 'Policies' })).toBeTruthy();
    expect(fetch).toHaveBeenCalledWith('/api/omnigent/api/policies', { credentials: 'same-origin' });
  });

  it('renders immutable policy inspection and lifecycle actions', async () => {
    window.history.replaceState({}, '', '/omnigent/policies');
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith('/versions/2/usage')) return {
        ok: true, json: async () => ({
          policyRef: 'codex-static@2', default: true,
          dependents: {
            hostBindings: ['oauth-host-codex'], hostBindingCount: 1,
            providerProfiles: ['codex-profile'], providerProfileCount: 1,
            workflows: ['workflow-1'], workflowCount: 1,
            bridgeSessions: ['bridge-1'], bridgeSessionCount: 1,
            activeBridgeSessions: ['bridge-1'], activeBridgeSessionCount: 1,
          },
          activationImpact: { willSwitchDefault: false, compatible: true, diagnostics: [] },
          unavailabilityBlockers: ['Switch the policy default before disabling or deprecating this version.'],
        }),
      } as Response;
      if (url.endsWith('/versions')) return {
        ok: true, json: async () => ({ items: [
          { policyId: 'codex-static', version: 2, ref: 'codex-static@2', state: 'active', digest: 'sha256:2',
            validation: { valid: true }, document: { host: { mode: 'static_compose' } } },
          { policyId: 'codex-static', version: 1, ref: 'codex-static@1', state: 'superseded', digest: 'sha256:1',
            validation: { valid: true }, document: { host: { mode: 'static_compose' } } },
        ] }),
      } as Response;
      if (url.endsWith('/audit')) return {
        ok: true, json: async () => ({ items: [
          { eventId: 'event-1', version: 2, type: 'default_changed', actor: 'operator', createdAt: '2026-01-01T00:00:00Z' },
        ] }),
      } as Response;
      return {
        ok: true,
        json: async () => ({ items: [{
          id: 'codex-static', name: 'Static Codex', status: 'active', defaultVersion: 2,
          summary: 'Immutable policy authority', version: {
            validation: { valid: true }, document: { host: { mode: 'static_compose' } },
          },
        }] }),
      } as Response;
    });
    renderPage({
      page: 'omnigent-inventory', apiBase: '/api', features: { omnigentPolicies: true },
      initialData: { uiEndpoints: { omnigentPolicies: '/api/omnigent/policies' } },
    });
    expect(await screen.findByText('Static Codex')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Inspect' }));
    expect(screen.getByRole('region', { name: 'Immutable policy version' })).toBeTruthy();
    expect(screen.getByText('Validation: Valid')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Activate / rollback' })).toBeTruthy();
    expect(await screen.findByRole('button', { name: 'codex-static@1 · superseded' })).toBeTruthy();
    expect(await screen.findByText(/default_changed/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Validate against deployment' })).toBeTruthy();
    expect(await screen.findByText('Dependent host profiles: 1')).toBeTruthy();
    expect(screen.getByText('oauth-host-codex')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Disable codex-static@2' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Deprecate codex-static@2' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
