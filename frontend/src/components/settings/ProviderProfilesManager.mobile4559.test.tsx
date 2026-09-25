import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';

import type { ProviderProfile } from './ProviderProfilesManager';
import { ProviderProfilesManager } from './ProviderProfilesManager';
import { renderWithClient } from '../../utils/test-utils';
import {
  buildProviderProfileTierPayload,
  normalizeProviderProfileTiers,
} from '../../utils/providerProfileTiers';

// MoonLadderStudios/MoonMind#4559: production-component journey for the
// Providers & Secrets responsive repair. Mounts the real
// ProviderProfilesManager with synthetic credential-free fixtures (long
// unbroken identity, long tier labels, several tiers, non-first default) and
// pins both the normalized UI state and the canonical save payload so the
// layout refactor cannot silently alter behavior. Geometry at 320px is proven
// by frontend/src/browser/mobileOverflow4559.browser.test.tsx in the browser
// matrix; this suite runs in jsdom and guards the markup/behavior contract.
const LONG_ID =
  'agent-profile-with-a-very-long-unbroken-identity-that-must-wrap-on-mobile-0123456789';

function syntheticProfiles(): ProviderProfile[] {
  return [
    {
      profile_id: LONG_ID,
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      credential_source: 'secret_ref',
      runtime_materialization_mode: 'api_key_env',
      secret_refs: {},
      max_parallel_runs: 1,
      cooldown_after_429_seconds: 300,
      rate_limit_policy: 'backoff',
      enabled: true,
      is_default: true,
      model_tiers: [
        { label: 'Plan and verify with a very long tier label that must wrap', model: 'gpt-5.5', effort: 'medium' },
        { label: 'Implementation', model: 'gpt-5.5', effort: 'xhigh' },
        { label: 'Docs and follow-through', model: null, effort: null },
      ],
      default_model_tier: 2,
    },
    {
      profile_id: 'short-id',
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      credential_source: 'secret_ref',
      runtime_materialization_mode: 'api_key_env',
      secret_refs: {},
      max_parallel_runs: 1,
      cooldown_after_429_seconds: 300,
      rate_limit_policy: 'backoff',
      enabled: true,
      is_default: false,
    },
  ];
}

describe('ProviderProfilesManager mobile journey (MoonMind#4559)', () => {
  it('renders saved profiles with normalized tier mapping and full-width action area', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithClient(
      <ProviderProfilesManager
        profiles={syntheticProfiles()}
        secretSlugs={[]}
        onNotice={() => undefined}
        queryClient={queryClient}
        defaultTaskModelByRuntime={{}}
      />,
    );

    // Normalized UI state: tiers keep order, default marker stays on Tier 2.
    const mapping = screen.getByLabelText(`${LONG_ID} model tier mapping`);
    expect(mapping.textContent).toContain('Tier 1 · Plan and verify');
    expect(mapping.textContent).toContain('Tier 2 default · Implementation');
    expect(mapping.textContent).toContain('Tier 3 · Docs and follow-through');

    // Saved-profiles table keeps the shared responsive hook for the
    // single-column mobile cards.
    const table = document.querySelector('.provider-profiles-table');
    expect(table).toBeTruthy();

    // Tier editor legend holds the group name only; Duplicate/Remove live in
    // their own wrapping action area, not inside the legend.
    const editor = document.querySelector('.provider-tier-editor');
    expect(editor).toBeTruthy();
    const legend = editor!.querySelector('legend');
    expect(legend!.textContent).toBe('Model & effort tiers');
    expect(legend!.textContent).not.toContain('Duplicate');
    expect(legend!.textContent).not.toContain('Remove');
    const tierActions = editor!.querySelector('[aria-label="Tier 1 actions"]');
    expect(tierActions).toBeTruthy();
    expect(within(tierActions as HTMLElement).getByRole('button', { name: /duplicate tier 1/i })).toBeTruthy();
    expect(within(tierActions as HTMLElement).getByRole('button', { name: /remove tier 1/i })).toBeTruthy();

    // Shared-cause fix stays present: editor fieldsets never force intrinsic width.
    for (const fieldset of Array.from(editor!.querySelectorAll('fieldset'))) {
      expect(fieldset.className).toContain('min-w-0');
      expect(fieldset.className).toContain('max-w-full');
    }
  });

  it('keeps the canonical tier save payload for the journey fixtures', () => {
    const profiles = syntheticProfiles();
    const saved = profiles[0]!;
    const normalized = normalizeProviderProfileTiers(saved.model_tiers, saved.default_model_tier);
    expect(normalized.isRepair).toBe(false);
    expect(normalized.tiers).toHaveLength(3);
    const payload = buildProviderProfileTierPayload(normalized.tiers, normalized.defaultTierClientId);
    expect(payload.default_model_tier).toBe(2);
    expect(payload.model_tiers.map((tier) => tier.label)).toEqual([
      'Plan and verify with a very long tier label that must wrap',
      'Implementation',
      'Docs and follow-through',
    ]);
    // No legacy mirrors leak into the canonical payload.
    expect(payload).not.toHaveProperty('default_model');
    expect(payload).not.toHaveProperty('default_effort');
    expect(payload).not.toHaveProperty('clientId');
    expect(JSON.stringify(payload)).not.toContain('clientId');
  });
});
