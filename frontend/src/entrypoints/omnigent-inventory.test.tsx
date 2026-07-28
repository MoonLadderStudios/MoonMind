import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BrowserRouter } from 'react-router-dom';

import { renderWithClient } from '../utils/test-utils';
import OmnigentInventoryPage from './omnigent-inventory';

describe('OmnigentInventoryPage', () => {
  const renderPage = (payload: Parameters<typeof OmnigentInventoryPage>[0]['payload']) =>
    renderWithClient(<BrowserRouter><OmnigentInventoryPage payload={payload} /></BrowserRouter>);
  beforeEach(() => {
    window.history.replaceState({}, '', '/omnigent/agents');
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/omnigent/agent-profiles') {
        return {
          ok: true,
          json: async () => [{
            profileId: 'codex-team',
            displayName: 'Team Codex',
            state: 'active',
            activeVersion: 2,
            defaultForRuntime: true,
            versions: [{
              version: 2,
              digest: `sha256:${'a'.repeat(64)}`,
              validationResult: { ready: true },
            }],
          }],
        };
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
    expect(screen.getByText('Version 2')).toBeTruthy();
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

  it('validates a draft profile through the authenticated lifecycle API', async () => {
    vi.mocked(fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/omnigent/agent-profiles/demo/validate') {
        expect(init?.method).toBe('POST');
        return { ok: true, json: async () => ({ ready: true, checks: [] }) } as Response;
      }
      if (String(input) === '/api/omnigent/agent-profiles') {
        return {
          ok: true,
          json: async () => [{
            profileId: 'demo', displayName: 'Demo', state: 'draft',
            activeVersion: null, versions: [{ version: 1, digest: `sha256:${'b'.repeat(64)}` }],
          }],
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

    fireEvent.click(await screen.findByRole('button', { name: 'Validate Demo' }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      '/api/omnigent/agent-profiles/demo/validate',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' }),
    ));
  });

  it('does not fetch or render future policy actions without a capability contract', async () => {
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
});
