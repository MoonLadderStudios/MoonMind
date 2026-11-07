import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  ConfigurationAlert, summarizeConfigurationHealth, secretSlugFromRef,
  type ConfigurationAlertProps,
} from './ConfigurationHealthSummary';
import type { ProviderProfile } from './ProviderProfilesManager';

function makeProfile(overrides: Partial<ProviderProfile> = {}): ProviderProfile {
  return {
    profile_id: 'profile-1', runtime_id: 'claude_code', provider_id: 'anthropic',
    provider_label: 'Anthropic', credential_source: 'managed_secret',
    runtime_materialization_mode: 'ephemeral', secret_refs: {}, max_parallel_runs: 1,
    cooldown_after_429_seconds: 0, rate_limit_policy: 'default', enabled: true,
    is_default: false, ...overrides,
  };
}
const defaults = { runtime: 'claude_code', profileRef: null };
function renderAlert(props: Partial<ConfigurationAlertProps> = {}) {
  return render(<ConfigurationAlert providerProfiles={[makeProfile()]} secrets={[]}
    newWorkDefaults={defaults} canWriteProviderProfiles canRunGithubTokenProbe {...props} />);
}

describe('summarizeConfigurationHealth', () => {
  it('accepts an enabled nonpreferred fallback and healthy bound secrets', () => {
    expect(summarizeConfigurationHealth({ newWorkDefaults: defaults,
      providerProfiles: [makeProfile({ secret_refs: { key: 'db://api-key' } })],
      secrets: [{ slug: 'api-key', status: 'active' }],
    })).toEqual({ level: 'ready', warnings: [] });
  });
  it.each(['disabled', 'missing', 'blocked'])('blocks a %s explicit default', (problem) => {
    const profile = makeProfile({ enabled: problem !== 'disabled',
      ...(problem === 'blocked' ? { readiness: { status: 'blocked', summary: 'Credentials expired' } as NonNullable<ProviderProfile['readiness']> } : {}),
    });
    const summary = summarizeConfigurationHealth({ newWorkDefaults: { ...defaults, profileRef: 'profile-1' },
      providerProfiles: problem === 'missing' ? [] : [profile], secrets: [],
    });
    expect(summary.level).toBe('blocked');
    expect(summary.warnings[0]!.target).toEqual({ kind: 'defaults', id: 'new-work-defaults' });
    expect(summary.warnings[0]!.message).toMatch(/New work defaults to profile-1/);
  });
  it('blocks native fallback without an enabled profile', () => {
    const summary = summarizeConfigurationHealth({ newWorkDefaults: defaults,
      providerProfiles: [makeProfile({ enabled: false })], secrets: [] });
    expect(summary.warnings[0]!.message).toBe('New work defaults to Claude Code, which has no enabled profile.');
  });
  it('does not require an OpenCode profile', () => {
    expect(summarizeConfigurationHealth({ newWorkDefaults: { runtime: 'omnigent', profileRef: null },
      providerProfiles: [], secrets: [] })).toEqual({ level: 'ready', warnings: [] });
  });
  it.each(['disabled', 'rotated', 'deleted', 'invalid', 'missing'])('links a bound %s secret to its row', (status) => {
    const summary = summarizeConfigurationHealth({ newWorkDefaults: defaults,
      providerProfiles: [makeProfile({ secret_refs: { key: 'db://api-key' } })],
      secrets: [{ slug: 'api-key', status }],
    });
    expect(summary.level).toBe('blocked');
    expect(summary.warnings[0]!.message).toBe(`profile-1 can't launch because its secret api-key is ${status}.`);
    expect(summary.warnings[0]!.target).toEqual({ kind: 'secret', id: 'api-key' });
  });
  it('does not block on broken secrets unused by an enabled profile', () => {
    expect(summarizeConfigurationHealth({ newWorkDefaults: defaults,
      providerProfiles: [makeProfile(), makeProfile({ profile_id: 'disabled', enabled: false, secret_refs: { key: 'db://api-key' } })],
      secrets: [{ slug: 'api-key', status: 'invalid' }],
    })).toEqual({ level: 'ready', warnings: [] });
  });
  it('checks the actually selected fallback in preference, priority, then ID order', () => {
    const blocked = { status: 'blocked', summary: 'Credentials expired' } as NonNullable<ProviderProfile['readiness']>;
    const profiles = [makeProfile({ profile_id: 'z', priority: 10 }),
      makeProfile({ profile_id: 'a', priority: 5, readiness: blocked })];
    const get = () => summarizeConfigurationHealth({ newWorkDefaults: defaults, providerProfiles: profiles, secrets: [] });
    expect(get().warnings.filter((w) => w.target.kind === 'defaults')).toHaveLength(0);
    profiles[1]!.is_default = true;
    expect(get().warnings.some((w) => w.target.kind === 'defaults')).toBe(true);
    profiles[1]!.is_default = false;
    profiles[1]!.priority = 10;
    expect(get().warnings.some((w) => w.target.kind === 'defaults')).toBe(true);
  });
  it('does not treat environment or file references as managed secrets', () => {
    expect(secretSlugFromRef('env://API_KEY')).toBeNull();
    expect(secretSlugFromRef('file:///keys/api')).toBeNull();
    expect(secretSlugFromRef('db://api-key')).toBe('api-key');
    expect(secretSlugFromRef('api-key')).toBe('api-key');
  });
});

describe('ConfigurationAlert', () => {
  it.each([{}, { isLoading: true }, { isError: true }])('renders nothing when healthy, loading or failed: %j', (state) => {
    const { container } = renderAlert(state);
    expect(container.innerHTML).toBe('');
  });
  it('shows concrete blockers before permission notes and provides corrective links', () => {
    const navigate = vi.fn();
    renderAlert({ providerProfiles: [makeProfile({ secret_refs: { key: 'db://api-key' } })],
      secrets: [{ slug: 'api-key', status: 'disabled' }], canWriteProviderProfiles: false, onNavigate: navigate });
    const alert = screen.getByRole('alert');
    expect(alert.className).toContain('rose');
    const lines = screen.getAllByRole('listitem');
    expect(lines[0]!.textContent).toMatch(/api-key is disabled/);
    expect(lines.at(-1)?.textContent).toMatch(/provider_profiles.write/);
    const link = screen.getByRole('link', { name: 'Show secret' });
    expect(link.getAttribute('href')).toBe('#managed-secret-api-key');
    fireEvent.click(link);
    expect(navigate).toHaveBeenCalledWith({ kind: 'secret', id: 'api-key' });
  });
  it('renders a permission-only warning without healthy metrics', () => {
    renderAlert({ canRunGithubTokenProbe: false });
    expect(screen.getByRole('alert').className).toContain('amber');
    expect(screen.getByText(/settings.effective.read/)).toBeTruthy();
    expect(screen.queryByText('Default profile')).toBeNull();
  });
});
