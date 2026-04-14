import { afterEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import { renderWithClient, screen, waitFor } from '../utils/test-utils';
import { DashboardAlerts } from './dashboard-alerts';

type SecretMetadata = {
  slug: string;
  status: string;
};

type ProviderProfileResponse = {
  profile_id: string;
  credential_source: string;
  enabled: boolean;
};

function mockDashboardAlertFetch(
  secrets: SecretMetadata[],
  profiles: ProviderProfileResponse[] = [],
): MockInstance {
  return vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url === '/api/v1/secrets') {
      return Promise.resolve({
        ok: true,
        json: async () => ({ items: secrets }),
      } as Response);
    }
    if (url === '/api/v1/provider-profiles') {
      return Promise.resolve({
        ok: true,
        json: async () => profiles,
      } as Response);
    }
    return Promise.resolve({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      text: async () => 'Unhandled fetch',
    } as Response);
  });
}

describe('DashboardAlerts', () => {
  let fetchSpy: MockInstance;

  afterEach(() => {
    fetchSpy?.mockRestore();
  });

  it('prompts for provider and GitHub credentials when both are absent', async () => {
    fetchSpy = mockDashboardAlertFetch([]);

    renderWithClient(<DashboardAlerts />);

    expect(await screen.findByText(/First-Run Setup:/i)).toBeTruthy();
    expect(screen.getByText(/ANTHROPIC_API_KEY/i)).toBeTruthy();
    expect(screen.getByText(/GITHUB_TOKEN/i)).toBeTruthy();
    expect(screen.getByText(/GITHUB_PAT/i)).toBeTruthy();
  });

  it('does not warn when provider and GitHub secrets are active', async () => {
    fetchSpy = mockDashboardAlertFetch([
      { slug: 'OPENAI_API_KEY', status: 'active' },
      { slug: 'GITHUB_TOKEN', status: 'active' },
    ]);

    renderWithClient(<DashboardAlerts />);

    await waitFor(() => {
      expect(screen.queryByText(/First-Run Setup:/i)).toBeNull();
    });
  });

  it('does not warn when an enabled OAuth-backed provider profile and GitHub secret exist', async () => {
    fetchSpy = mockDashboardAlertFetch(
      [{ slug: 'GITHUB_TOKEN', status: 'active' }],
      [
        {
          profile_id: 'codex-default',
          credential_source: 'oauth_volume',
          enabled: true,
        },
      ],
    );

    renderWithClient(<DashboardAlerts />);

    await waitFor(() => {
      expect(screen.queryByText(/First-Run Setup:/i)).toBeNull();
    });
  });
});
