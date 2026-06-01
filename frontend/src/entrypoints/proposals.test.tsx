import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from 'vitest';
import { fireEvent, renderWithClient, screen, waitFor } from '../utils/test-utils';

import type { BootPayload } from '../boot/parseBootPayload';
import { ProposalsPage } from './proposals';

const mockPayload: BootPayload = {
  page: 'proposals',
  apiBase: '/api',
  initialData: {},
};

describe('ProposalsPage', () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.replaceState({}, '', '/proposals');
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input, init) => {
      const url = String(input);
      const method = String(init?.method || 'GET').toUpperCase();

      if (url.startsWith('/api/proposals?') && method === 'GET') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [
              {
                id: '11111111-1111-4111-8111-111111111111',
                title: 'MM-743 follow-up proposal',
                summary: 'Generated from proposal stage evidence',
                status: 'open',
                category: 'run_quality',
                repository: 'MoonLadderStudios/MoonMind',
                createdAt: '2026-05-31T12:00:00Z',
                promotedAt: null,
                taskPreview: {
                  runtimeMode: 'codex',
                  skillId: 'jira-implement',
                  taskSkills: ['jira-implement'],
                  presetProvenance: 'preserved-binding',
                  authoredPresetCount: 1,
                },
              },
            ],
            nextCursor: 'cursor-next',
          }),
        } as Response);
      }

      if (
        (url === '/api/proposals/11111111-1111-4111-8111-111111111111/promote' ||
          url === '/api/proposals/11111111-1111-4111-8111-111111111111/dismiss') &&
        method === 'POST'
      ) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ id: '11111111-1111-4111-8111-111111111111' }),
        } as Response);
      }

      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        json: async () => ({}),
      } as Response);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    window.history.replaceState({}, '', '/');
  });

  it('surfaces generated proposals with filters, provenance, and review actions for MM-743', async () => {
    renderWithClient(<ProposalsPage payload={mockPayload} />);

    expect(
      (await screen.findAllByText('MM-743 follow-up proposal')).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText('MoonLadderStudios/MoonMind').length).toBeGreaterThan(0);
    expect(screen.getAllByText('codex').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Preserved binding (1)').length).toBeGreaterThan(0);

    const repositoryFilter = screen.getByPlaceholderText('owner/repo');
    fireEvent.change(repositoryFilter, { target: { value: 'MoonLadderStudios/MoonMind' } });

    await waitFor(() =>
      expect(
        fetchSpy.mock.calls.some(([url]) =>
          String(url).includes('repository=MoonLadderStudios%2FMoonMind'),
        ),
      ).toBe(true),
    );

    const promoteButton = screen.getAllByRole('button', { name: 'Promote' })[0];
    expect(promoteButton).toBeDefined();
    fireEvent.click(promoteButton!);
    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/proposals/11111111-1111-4111-8111-111111111111/promote',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    await waitFor(() => {
      const button = screen.getAllByRole('button', { name: 'Promote' })[0] as HTMLButtonElement;
      expect(button.disabled).toBe(false);
    });

    const dismissButton = screen.getAllByRole('button', { name: 'Dismiss' })[0];
    expect(dismissButton).toBeDefined();
    fireEvent.click(dismissButton!);
    await waitFor(() =>
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/proposals/11111111-1111-4111-8111-111111111111/dismiss',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
    await waitFor(() => {
      const button = screen.getAllByRole('button', { name: 'Dismiss' })[0] as HTMLButtonElement;
      expect(button.disabled).toBe(false);
    });
  });
});
