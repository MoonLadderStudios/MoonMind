import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { userEvent } from 'vitest/browser';

import {
  hover,
  keyboardFocus,
  outlineContrast,
  sameColor,
  setTheme,
  toRgba,
  unhover,
  whilePressed,
  type Theme,
} from './support/computedColor';
import '../styles/dashboard.css';

// Computed-style checks for the dashboard's bespoke controls (masthead nav,
// System menu, list display toggle, filter triggers, skill chevron, Create
// CTA, toast close, disabled menu rows). They replace CSS-source pins: the
// contract is what the browser renders in each state, not the rule text.

const MARKUP = `
  <header class="masthead">
    <nav class="route-nav" aria-label="MoonMind navigation">
      <div class="route-nav-primary">
        <a href="/workflows" class="active">Workflows</a>
        <a href="/workflows/new" data-test="nav-link">Create</a>
      </div>
      <div class="dashboard-system-menu">
        <button type="button" class="dashboard-system-trigger" aria-expanded="false">System</button>
        <div class="dashboard-system-popover" role="menu" style="display: none">
          <a href="/settings" role="menuitem" class="active" data-test="active-row">Settings</a>
          <a href="/secrets" role="menuitem">Secrets</a>
        </div>
      </div>
    </nav>
  </header>
  <div class="panel" style="background: rgb(var(--mm-panel)); padding: 16px">
    <div class="workflow-list-display-control" role="radiogroup" aria-label="Display">
      <button type="button" role="radio" class="workflow-list-display-option" aria-checked="false" data-test="display-off">A</button>
      <button type="button" role="radio" class="workflow-list-display-option workflow-list-display-option--selected" aria-checked="true" data-test="display-on">B</button>
    </div>
    <button type="button" class="workflow-list-column-filter-button" aria-expanded="false" data-test="filter-idle">F</button>
    <button type="button" class="workflow-list-column-filter-button" aria-expanded="true" data-test="filter-open">F</button>
    <button type="button" class="workflow-list-column-filter-button is-active" aria-expanded="false" data-test="filter-applied">F</button>
    <button type="button" class="workflow-list-filter-trigger" aria-expanded="false" data-test="trigger-idle">Filters</button>
    <button type="button" class="workflow-list-filter-trigger" aria-expanded="true" data-test="trigger-open">Filters</button>
    <button type="button" class="workflow-list-filter-trigger is-active" aria-expanded="false" data-test="trigger-applied">Filters</button>
    <div class="skill-combobox" style="position: relative; width: 14rem; height: 2.75rem">
      <button type="button" class="skill-combobox-toggle" data-test="chevron">v</button>
    </div>
    <button type="button" class="queue-submit-primary queue-submit-primary--icon" data-test="cta">Go</button>
    <button type="button" class="queue-submit-primary queue-submit-primary--icon queue-submit-primary--arrow-exit" data-test="cta-sent">Go</button>
    <div class="dashboard-toast"><button type="button" class="dashboard-toast__close" aria-label="Dismiss" data-test="toast-close">x</button></div>
    <div class="representative-focus">
      <input type="text" aria-label="Text" data-focus="input" />
      <select aria-label="Choice" data-focus="select"><option>One</option></select>
      <textarea aria-label="Notes" data-focus="textarea"></textarea>
      <button type="button" class="table-sort-button" data-focus="table-sort">Name</button>
      <button type="button" class="queue-step-attachment-add-button" aria-label="Add" data-focus="attachment-add">+</button>
      <a href="/artifact" class="live-logs-artifact-link" data-focus="artifact-link">Artifact</a>
      <button type="button" class="td-instructions-toggle" data-focus="instructions-toggle">Instructions</button>
      <button type="button" class="queue-submit-primary" data-focus="submit">Create</button>
      <button type="button" class="step-tl-toggle" aria-expanded="false" data-focus="step-toggle">Plan work · Completed</button>
    </div>
    <div data-test="steps-container" style="display: flex; width: 20rem; overflow: visible">
      <div class="queue-step-controls">
        <button type="button" class="queue-step-extension-button" data-test="step-extension">Add</button>
        <button type="button" class="queue-step-icon-button" aria-label="Move up" data-test="step-icon">^</button>
        <button type="button" class="queue-step-icon-button destructive" aria-label="Remove" data-test="step-remove">x</button>
      </div>
    </div>
    <span class="queue-step-capability-chip">
      <button type="button" class="queue-step-icon-button destructive queue-step-capability-chip-remove" aria-label="Remove capability" data-test="chip-remove">x</button>
    </span>
    <span class="queue-submit-primary-ripple" data-test="ripple" style="animation: none"></span>
    <div class="td-workflow-actions-popover" style="position: static">
      <button type="button" class="td-workflow-actions-item td-workflow-actions-item-disabled" aria-disabled="true" data-test="menu-disabled">Unavailable</button>
    </div>
  </div>
`;

let host: HTMLElement;

function el(name: string): HTMLElement {
  const found = host.querySelector<HTMLElement>(`[data-test="${name}"]`);
  if (!found) throw new Error(`missing ${name}`);
  return found;
}

beforeEach(() => {
  host = document.createElement('main');
  host.className = 'dashboard-root';
  host.innerHTML = MARKUP;
  document.body.appendChild(host);
});

afterEach(async () => {
  await userEvent.unhover(document.body);
  host.remove();
  setTheme('light');
});

for (const theme of ['light', 'dark'] as Theme[]) {
  describe(`bespoke control states (${theme} theme)`, () => {
    beforeEach(() => setTheme(theme));

    it('swaps the masthead nav glow on hover without scaling or brightening', async () => {
      for (const element of [el('nav-link'), host.querySelector<HTMLElement>('.dashboard-system-trigger')!]) {
        const resting = getComputedStyle(element).boxShadow;
        await hover(element);
        const hovered = getComputedStyle(element);
        expect(hovered.boxShadow).not.toBe(resting);
        expect(hovered.transform).toBe('none');
        expect(hovered.filter).toBe('none');
        await unhover(element);
      }
    });

    it('gives masthead, System menu and list controls the shared 3:1 focus outline', async () => {
      const trigger = host.querySelector<HTMLElement>('.dashboard-system-trigger')!;
      for (const element of [el('nav-link'), trigger, el('display-on'), el('toast-close'), el('menu-disabled')]) {
        await keyboardFocus(element);
        expect(outlineContrast(element), element.outerHTML.slice(0, 80)).toBeGreaterThanOrEqual(3);
        element.blur();
      }
      // The keyboard-focused active System row keeps its selected inset bar too.
      host.querySelector<HTMLElement>('.dashboard-system-popover')!.style.display = '';
      const activeRow = el('active-row');
      await keyboardFocus(activeRow);
      expect(outlineContrast(activeRow)).toBeGreaterThanOrEqual(3);
      expect(getComputedStyle(activeRow).boxShadow).toContain('inset');
    });

    it('applies the same 3:1 focus outline to fields, links and component buttons', async () => {
      for (const element of Array.from(host.querySelectorAll<HTMLElement>('[data-focus]'))) {
        await keyboardFocus(element);
        expect(outlineContrast(element), element.dataset.focus).toBeGreaterThanOrEqual(3);
        element.blur();
      }
    });

    it('hovers and presses Create step controls without scaling, brightening or a white ring', async () => {
      for (const name of ['step-icon', 'step-remove', 'step-extension']) {
        const control = el(name);
        await hover(control);
        expect(getComputedStyle(control).transform, name).toBe('none');
        expect(getComputedStyle(control).filter, name).toBe('none');
        const pressed = await whilePressed(control, () => getComputedStyle(control));
        expect(pressed.transform, name).toBe('none');
        expect(pressed.filter, name).toBe('none');
        expect(pressed.boxShadow, name).toContain('inset');
        expect(pressed.boxShadow, name).not.toMatch(/rgba?\(255, 255, 255/);
        await unhover(control);
      }
      // A hovered trailing control stays inside the steps container.
      const trailing = el('step-remove');
      await hover(trailing);
      expect(trailing.getBoundingClientRect().right).toBeLessThanOrEqual(el('steps-container').getBoundingClientRect().right + 0.5);
    });

    it('keeps context chip remove buttons compact and chromeless on hover and press', async () => {
      const remove = el('chip-remove');
      const check = () => {
        const style = getComputedStyle(remove);
        expect(toRgba(style.backgroundColor)[3]).toBe(0);
        expect(style.backgroundImage).toBe('none');
        expect(style.borderTopWidth).toBe('0px');
        expect(style.boxShadow).toBe('none');
        expect(Number.parseFloat(style.width)).toBeLessThan(16);
      };
      check();
      await hover(remove);
      check();
      await whilePressed(remove, check);
    });

    it('keeps the selected list display option marked while hovered', async () => {
      const selected = el('display-on');
      const idleColor = getComputedStyle(el('display-off')).color;
      expect(sameColor(getComputedStyle(selected).color, idleColor)).toBe(false);
      await hover(selected);
      expect(sameColor(getComputedStyle(selected).color, idleColor)).toBe(false);
    });

    it('distinguishes filter trigger hover, open and filter-applied states', async () => {
      for (const [idle, open, applied] of [
        ['filter-idle', 'filter-open', 'filter-applied'],
        ['trigger-idle', 'trigger-open', 'trigger-applied'],
      ] as const) {
        const look = (element: HTMLElement) => {
          const style = getComputedStyle(element);
          return [style.backgroundColor, style.borderTopColor, style.color, style.boxShadow].join(' | ');
        };
        const resting = look(el(idle));
        await hover(el(idle));
        const hovered = look(el(idle));
        await unhover(el(idle));
        const states = { resting, hovered, open: look(el(open)), applied: look(el(applied)) };
        expect(new Set(Object.values(states)).size, JSON.stringify(states)).toBe(4);
        // Keyboard focus is the outline; it is not another background/border look.
        await keyboardFocus(el(idle));
        expect(look(el(idle))).toBe(resting);
        expect(outlineContrast(el(idle))).toBeGreaterThanOrEqual(3);
        el(idle).blur();
      }
    });

    it('keeps the skill combobox chevron chromeless at rest, on hover and while pressed', async () => {
      const chevron = el('chevron');
      const check = () => {
        const style = getComputedStyle(chevron);
        expect(toRgba(style.backgroundColor)[3]).toBe(0);
        expect(style.backgroundImage).toBe('none');
        expect(style.boxShadow).toBe('none');
        expect(style.borderTopWidth).toBe('0px');
        expect(style.transform).toBe('none');
        return style.color;
      };
      const restingColor = check();
      await hover(chevron);
      expect(sameColor(check(), restingColor)).toBe(true);
      const pressedColor = await whilePressed(chevron, check);
      expect(sameColor(pressedColor, restingColor)).toBe(true);
    });

    it('fills the Create CTA with the primary colour, presses quieter than hover, and outlines it once sent', async () => {
      const cta = el('cta');
      const sent = el('cta-sent');
      const primary = getComputedStyle(cta).backgroundColor;
      await hover(cta);
      const hoverShadow = getComputedStyle(cta).boxShadow;
      const pressed = await whilePressed(cta, () => getComputedStyle(cta));
      expect(pressed.filter).toBe('none');
      expect(pressed.transform).toBe('none');
      expect(pressed.boxShadow).toContain('inset');
      expect(pressed.boxShadow).not.toBe(hoverShadow);
      expect(pressed.boxShadow).not.toMatch(/rgba?\(255, 255, 255/);

      // Hover keeps the CTA's glow; it is the louder state.
      expect(hoverShadow).not.toBe('none');

      const sentStyle = getComputedStyle(sent);
      expect(sentStyle.backgroundImage).toBe('none');
      expect(sameColor(sentStyle.borderTopColor, primary)).toBe(true);
      expect(sameColor(sentStyle.color, primary)).toBe(true);

      // The click shockwave uses the same primary colour, unblurred.
      const ripple = getComputedStyle(el('ripple'));
      const rippleBorder = toRgba(ripple.borderTopColor);
      const primaryRgb = toRgba(primary);
      expect(rippleBorder.slice(0, 3).every((value, i) => Math.abs(value - (primaryRgb[i] ?? 0)) <= 3)).toBe(true);
      expect(ripple.backgroundImage).toContain('radial-gradient');
      expect(ripple.filter).toBe('none');
    });
  });
}
