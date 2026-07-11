import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import '../styles/dashboard.css';

// Real-browser guardrail for the desktop masthead nav. An overflow-x: auto on
// .route-nav-primary coerces overflow-y to auto, which surfaces a vertical
// scrollbar inside the nav and clips the active-route underline that is
// positioned below the links to sit on the masthead's bottom border. jsdom
// cannot compute layout, so this must be asserted in a real browser.

const MASTHEAD_MARKUP = `
  <header class="masthead">
    <div class="masthead-brand-group">
      <a class="masthead-brand" href="/workflows" aria-label="MoonMind workflows">
        <h1>
          <span class="masthead-brand-moon">Moon</span>
          <span class="masthead-brand-mind">Mind</span>
        </h1>
      </a>
    </div>
    <div class="masthead-nav">
      <nav class="route-nav" id="dashboard-nav" aria-label="MoonMind navigation">
        <div class="route-nav-primary">
          <a href="/workflows" class="active">Workflows</a>
          <a href="/workflows/new">Create</a>
          <a href="/schedules">Recurring</a>
          <a href="/skills">Skills</a>
        </div>
        <div class="dashboard-system-menu">
          <button type="button" class="dashboard-system-trigger">System</button>
        </div>
      </nav>
    </div>
    <div class="masthead-title-meta">
      <div class="version-badge"><span class="version-badge-value">v2026.07.11</span></div>
    </div>
  </header>
`;

let container: HTMLElement;

beforeEach(() => {
  container = document.createElement('div');
  container.innerHTML = MASTHEAD_MARKUP;
  document.body.appendChild(container);
});

afterEach(() => {
  container.remove();
});

describe('desktop masthead nav layout', () => {
  it('does not create a scroll container around the primary nav links', () => {
    const primary = container.querySelector<HTMLElement>('.route-nav-primary')!;
    const style = getComputedStyle(primary);

    // Any non-visible overflow on one axis coerces the other axis to auto and
    // reintroduces the vertical scrollbar plus underline clipping. Only
    // visible/visible guarantees no scrollbar and no clipping.
    expect(style.overflowX).toBe('visible');
    expect(style.overflowY).toBe('visible');

    const nav = container.querySelector<HTMLElement>('.masthead-nav')!;
    const navStyle = getComputedStyle(nav);
    expect(navStyle.overflowX).toBe('visible');
    expect(navStyle.overflowY).toBe('visible');
  });

  it('places the active-route underline on the masthead bottom border', () => {
    const masthead = container.querySelector<HTMLElement>('.masthead')!;
    const active = container.querySelector<HTMLElement>('.route-nav a.active')!;

    const underline = getComputedStyle(active, '::after');
    expect(underline.height).toBe('3px');

    // bottom: calc(-1 * var(--masthead-padding-block-end)) must land the
    // underline's bottom edge exactly on the masthead's bottom edge, where the
    // masthead::after border line renders.
    const bottomOffset = Number.parseFloat(underline.bottom);
    expect(Number.isNaN(bottomOffset)).toBe(false);
    expect(bottomOffset).toBeLessThan(0);

    const mastheadStyle = getComputedStyle(masthead);
    expect(Math.abs(bottomOffset + Number.parseFloat(mastheadStyle.paddingBottom))).toBeLessThan(0.51);

    const underlineBottomY = active.getBoundingClientRect().bottom - bottomOffset;
    expect(Math.abs(underlineBottomY - masthead.getBoundingClientRect().bottom)).toBeLessThan(1);

    // The underline is painted (accent), not clipped away by a scroll container.
    expect(underline.backgroundColor).not.toBe('rgba(0, 0, 0, 0)');
  });
});
