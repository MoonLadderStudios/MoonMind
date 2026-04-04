import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient, screen, waitFor } from '../utils/test-utils';
import { MissionControlApp } from './mission-control-app';

describe('Mission Control shared entry', () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/v1/secrets') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [] }),
        } as Response);
      }
      if (url === '/api/v1/provider-profiles') {
        return Promise.resolve({
          ok: true,
          json: async () => [],
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        text: async () => 'Unhandled fetch',
      } as Response);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it('renders dashboard alerts and lazy-loads the requested page component', async () => {
    const payload: BootPayload = {
      page: 'tasks-home',
      apiBase: '/api',
      initialData: {
        layout: {
          dataWidePanel: true,
        },
      },
    };

    renderWithClient(<MissionControlApp payload={payload} />);

    expect(await screen.findByText('Hello from Tasks Home!')).toBeTruthy();
    expect(await screen.findByText(/First-Run Setup:/i)).toBeTruthy();
    await waitFor(() => {
      expect(document.querySelector('.panel--data-wide')).toBeTruthy();
    });
  });

  it('renders an explicit error state for unknown pages', async () => {
    renderWithClient(
      <MissionControlApp payload={{ page: 'not-a-page', apiBase: '/api' }} />,
    );

    expect(await screen.findByText(/Unknown Mission Control page:/i)).toBeTruthy();
    expect(screen.getByText('not-a-page')).toBeTruthy();
  });
});
