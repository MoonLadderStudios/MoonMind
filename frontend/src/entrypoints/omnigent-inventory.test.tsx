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
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ id: 'agent-1', name: 'Codex', status: 'ready', description: 'Coding agent' }],
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
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith('/api/omnigent/api/agents', { credentials: 'same-origin' });
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
  });
});
