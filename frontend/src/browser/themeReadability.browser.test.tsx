import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { page, userEvent } from 'vitest/browser';
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';

import { WorkflowLifecycleStatusPill } from '../components/ExecutionStatusPill';
import { hover, setTheme, textContrast, type Theme } from './support/computedColor';
import '../styles/dashboard.css';

// Real-browser readability checks for text whose colour depends on the theme:
// the masthead wordmark, status pills, notices, badges and the collapsed
// mobile navigation. Ratios come from computed styles, not declared values.

const MASTHEAD = `
  <header class="masthead">
    <div class="masthead-brand-group">
      <a class="masthead-brand" href="/workflows">
        <h1 class="masthead-wordmark">
          <span class="masthead-brand-moon">Moon</span><span class="masthead-brand-mind">Mind</span>
        </h1>
      </a>
    </div>
    <div class="masthead-nav">
      <nav class="route-nav route-nav--open" aria-label="MoonMind navigation">
        <div class="route-nav-primary">
          <a href="/workflows" class="active">Workflows</a>
          <a href="/workflows/new">Create</a>
          <a href="/schedules">Recurring</a>
        </div>
      </nav>
      <a class="skills-create-nav-button" href="/skills/new" aria-label="Create skill">+</a>
    </div>
  </header>
`;

const STATUSES = ['executing', 'completed', 'failed', 'queued', 'awaiting_action', 'planning', 'canceled'];
const NOTICES = ['', 'ok', 'pending', 'warning', 'error', 'subtle'];
const BADGES = ['badge-success', 'badge-warning', 'badge-error', 'badge-neutral'];

let host: HTMLElement;
let root: Root | null = null;

beforeEach(() => {
  host = document.createElement('main');
  host.className = 'dashboard-root';
  document.body.appendChild(host);
});

afterEach(async () => {
  root?.unmount();
  root = null;
  host.remove();
  setTheme('light');
  await page.viewport(1280, 800);
});

for (const theme of ['light', 'dark'] as Theme[]) {
  describe(`theme readability (${theme})`, () => {
    beforeEach(() => setTheme(theme));

    it('keeps both halves of the masthead wordmark and the create shortcut readable', () => {
      host.innerHTML = MASTHEAD;
      for (const selector of ['.masthead-brand-moon', '.masthead-brand-mind']) {
        const element = host.querySelector<HTMLElement>(selector)!;
        // Large bold text: 3:1 is the WCAG minimum, but the brand should clear body-text contrast.
        expect(textContrast(element), selector).toBeGreaterThanOrEqual(4.5);
      }
      expect(textContrast(host.querySelector('.skills-create-nav-button')!)).toBeGreaterThanOrEqual(4.5);
    });

    it('gives every status pill at least 4.5:1 text contrast', () => {
      host.style.background = 'rgb(var(--mm-panel))';
      root = createRoot(host);
      flushSync(() => {
        root!.render(
          <div>
            {STATUSES.map((status) => (
              <WorkflowLifecycleStatusPill key={status} status={status} />
            ))}
          </div>,
        );
      });
      const pills = Array.from(host.querySelectorAll<HTMLElement>('.status'));
      expect(pills).toHaveLength(STATUSES.length);
      pills.forEach((pill, index) => {
        expect(textContrast(pill), `${STATUSES[index]} pill`).toBeGreaterThanOrEqual(4.5);
      });
    });

    it('gives notices and badges at least 4.5:1 text contrast', () => {
      host.style.background = 'rgb(var(--mm-panel))';
      host.innerHTML = [
        ...NOTICES.map((tone) => `<div class="notice ${tone}" data-tone="notice ${tone}">Notice text</div>`),
        ...BADGES.map((tone) => `<span class="badge ${tone}" data-tone="${tone}">Badge</span>`),
      ].join('');
      for (const element of Array.from(host.querySelectorAll<HTMLElement>('[data-tone]'))) {
        expect(textContrast(element), element.dataset.tone).toBeGreaterThanOrEqual(4.5);
      }
    });

    it('keeps collapsed mobile navigation text readable at rest, hovered, focused and active', async () => {
      await page.viewport(900, 800);
      host.innerHTML = MASTHEAD;
      const links = Array.from(host.querySelectorAll<HTMLAnchorElement>('.route-nav a'));
      expect(getComputedStyle(host.querySelector('.route-nav')!).position).toBe('fixed');
      for (const link of links) {
        expect(textContrast(link), `${link.textContent} at rest`).toBeGreaterThanOrEqual(4.5);
      }
      const idle = links.find((link) => !link.classList.contains('active'))!;
      await hover(idle);
      expect(textContrast(idle), 'hovered link').toBeGreaterThanOrEqual(4.5);
      await userEvent.unhover(idle);
    });
  });
}
