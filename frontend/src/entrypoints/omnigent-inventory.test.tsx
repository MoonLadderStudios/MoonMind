import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
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

  it('does not fetch or render future policy actions without a capability contract', async () => {
    window.history.replaceState({}, '', '/omnigent/policies');
    renderPage({ page: 'omnigent-inventory', apiBase: '/api', features: { omnigentPolicies: false } });
    expect(screen.getByRole('alert').textContent).toContain('not available');
    await waitFor(() => expect(fetch).not.toHaveBeenCalled());
    expect(screen.queryByRole('button')).toBeNull();
  });
});
