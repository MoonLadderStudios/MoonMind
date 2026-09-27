import { QueryClient } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { renderWithClient, screen } from '../../utils/test-utils';
import { ProviderProfilesManager } from './ProviderProfilesManager';

// Structural guardrails for MoonLadderStudios/MoonMind#4559. Geometry is
// proven by the real-browser suite
// (`frontend/src/browser/mobileOverflow.browser.test.tsx`); these jsdom
// cases pin the production DOM contract the responsive CSS depends on:
// tier actions live in their own wrapping area outside the legend, and the
// shared layout hooks exist exactly once per feature (no parallel mobile
// form with separate business logic).

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function renderManager() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: false,
      status: 404,
      json: async () => ({}),
    })),
  );
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithClient(
    <ProviderProfilesManager
      profiles={[]}
      secretSlugs={[]}
      onNotice={() => undefined}
      queryClient={queryClient}
      canWriteProviderProfiles
    />,
  );
}

describe('provider mobile layout contract (MoonMind#4559)', () => {
  it('keeps tier actions out of the legend in a separate wrapping area', async () => {
    renderManager();
    const tierGroup = await screen.findByRole('list', { name: 'Model and effort tiers' });
    expect(tierGroup.classList.contains('provider-tier-list')).toBe(true);

    const tierCard = tierGroup.querySelector('[data-tier-client-id]');
    expect(tierCard).toBeTruthy();
    expect(tierCard!.classList.contains('provider-tier-card')).toBe(true);

    // The legend names the group only; Duplicate/Remove compete nowhere
    // inside it.
    const legend = tierCard!.querySelector(':scope > fieldset > legend');
    expect(legend).toBeTruthy();
    expect(legend!.querySelector('button')).toBeNull();
    expect(legend!.textContent).toMatch(/Tier 1/);

    // The separate action area preserves the accessible names while the
    // visible wording stays short enough to wrap at word boundaries.
    const actions = tierCard!.querySelector('.tier-card__actions');
    expect(actions).toBeTruthy();
    const duplicate = actions!.querySelector('button[aria-label="Duplicate Tier 1 as new last tier"]');
    expect(duplicate?.textContent).toBe('Duplicate tier');
    expect(actions!.querySelector('button[aria-label="Remove Tier 1"]')?.textContent).toBe('Remove tier');
  });

  it('owns one form, one record table, and one action area per feature', async () => {
    const { container } = renderManager();
    await screen.findByRole('list', { name: 'Model and effort tiers' });

    // No parallel mobile form: exactly one create/edit form owns the draft.
    expect(container.querySelectorAll('form.provider-profile-form').length).toBe(1);
    // The record table owns saved profiles; the responsive presentation is
    // CSS on this same table, not a second stateful list.
    expect(container.querySelectorAll('table.provider-profiles-table').length).toBe(1);
    // Tier order/identity is one native list, not a duplicated mobile tree.
    expect(container.querySelectorAll('ol.provider-tier-list').length).toBe(1);
    // Fieldsets keep native grouping semantics for assistive tech.
    const identityLegend = screen.getByText(/Identity/, { selector: 'legend' });
    expect(identityLegend.closest('fieldset')?.classList.contains('provider-profile-fieldset')).toBe(true);
  });
});
