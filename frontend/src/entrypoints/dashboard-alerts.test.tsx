import { afterEach, describe, expect, it, vi, type MockInstance } from 'vitest';

import { renderWithClient, screen, waitFor } from '../utils/test-utils';
import { DashboardAlerts } from './dashboard-alerts';

type SecretMetadata = { slug: string; status: string };
type ProviderProfileResponse = { profile_id: string; enabled: boolean; launch_ready: boolean };

function mockFetch(
  secrets: SecretMetadata[],
  profiles: ProviderProfileResponse[] = [],
  profileFailure = false,
): MockInstance {
  return vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url === '/api/v1/secrets') {
      return Promise.resolve({ ok: true, json: async () => ({ items: secrets }) } as Response);
    }
    if (url === '/api/v1/provider-profiles') {
      return Promise.resolve({
        ok: !profileFailure,
        json: async () => profiles,
      } as Response);
    }
    return Promise.resolve({ ok: false, status: 404 } as Response);
  });
}

const github = [{ slug: 'GITHUB_TOKEN', status: 'active' }];
const readyProfile = (profile_id: string): ProviderProfileResponse => ({
  profile_id,
  enabled: true,
  launch_ready: true,
});

describe('DashboardAlerts', () => {
  let fetchSpy: MockInstance;

  afterEach(() => fetchSpy?.mockRestore());

  it('shows generic provider-profile and GitHub setup lines without API-key names', async () => {
    fetchSpy = mockFetch([], []);
    renderWithClient(<DashboardAlerts />);
    expect(await screen.findByText(/First-Run Setup:/i)).toBeTruthy();
    expect(screen.getByText(/Set up and enable at least one provider profile/i)).toBeTruthy();
    expect(screen.getByText(/Set up GitHub access/i)).toBeTruthy();
    expect(screen.queryByText(/API_KEY|GITHUB_TOKEN|GITHUB_PAT/i)).toBeNull();
  });

  it.each(['codex_oauth', 'claude_api', 'other_credential']) (
    'does not warn for launch-ready profile %s with GitHub access',
    async (profileId) => {
      fetchSpy = mockFetch(github, [readyProfile(profileId)]);
      renderWithClient(<DashboardAlerts />);
      await waitFor(() => expect(screen.queryByText(/First-Run Setup:/i)).toBeNull());
    },
  );

  it('does not treat an active provider secret as a ready profile', async () => {
    fetchSpy = mockFetch([...github, { slug: 'OPENAI_API_KEY', status: 'active' }], []);
    renderWithClient(<DashboardAlerts />);
    expect(await screen.findByText(/Set up and enable at least one provider profile/i)).toBeTruthy();
  });

  it.each([
    { profile_id: 'disabled', enabled: false, launch_ready: false },
    { profile_id: 'blocked', enabled: true, launch_ready: false },
  ])('warns for a non-ready profile', async (profile) => {
    fetchSpy = mockFetch(github, [profile]);
    renderWithClient(<DashboardAlerts />);
    expect(await screen.findByText(/Set up and enable at least one provider profile/i)).toBeTruthy();
    expect(screen.queryByText(/Set up GitHub access/i)).toBeNull();
  });

  it('shows only GitHub setup when a provider is ready', async () => {
    fetchSpy = mockFetch([], [readyProfile('ready')]);
    renderWithClient(<DashboardAlerts />);
    expect(await screen.findByText(/Set up GitHub access/i)).toBeTruthy();
    expect(screen.queryByText(/Set up and enable/i)).toBeNull();
  });

  it('renders a neutral notice when provider readiness cannot be checked', async () => {
    fetchSpy = mockFetch(github, [], true);
    renderWithClient(<DashboardAlerts />);
    expect(await screen.findByText(/could not verify provider profile readiness/i)).toBeTruthy();
    expect(screen.queryByText(/missing profile|API_KEY/i)).toBeNull();
  });
});
