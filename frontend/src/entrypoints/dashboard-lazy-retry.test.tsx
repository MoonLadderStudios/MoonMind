import { afterEach, expect, it, vi } from 'vitest';
import { DashboardApp } from './dashboard-app';
import { cleanup, fireEvent, renderWithClient, screen } from '../utils/test-utils';

const imports = vi.hoisted(() => ({ attempts: 0 }));
vi.mock('./oauth-terminal', () => {
  imports.attempts += 1;
  if (imports.attempts === 1) throw new Error('Failed to fetch dynamically imported module: oauth-terminal');
  return { default: () => <div>Terminal page recovered</div> };
});

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('retries a failed lazy page import with a fresh loader', async () => {
  window.history.replaceState({}, '', '/oauth-terminal');
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(window, 'fetch').mockImplementation(async (input) => ({ ok: true, json: async () => String(input).includes('provider-profiles') ? [{ launch_ready: true }] : { features: { oauthTerminal: true } } }) as Response);
  renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);
  expect(await screen.findByText('This page failed to load')).toBeTruthy();
  expect(screen.queryByText('Terminal page recovered')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText('Terminal page recovered')).toBeTruthy();
  expect(screen.queryByText('This page failed to load')).toBeNull();
  expect(imports.attempts).toBeGreaterThanOrEqual(2);
});
