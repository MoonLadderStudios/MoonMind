import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync } from 'react-dom';
import { createRoot, type Root } from 'react-dom/client';

import { Button, type ButtonVariant } from '../components/dashboard/Button';
import {
  contrastRatio,
  effectiveBackground,
  hover,
  keyboardFocus,
  outlineContrast,
  relativeLuminance,
  sameColor,
  setTheme,
  settle,
  textContrast,
  toRgba,
  unhover,
  whilePressed,
  type Theme,
} from './support/computedColor';
import '../styles/dashboard.css';

// Real-browser contract for the shared Button: every variant in every state and
// both themes. jsdom cannot resolve the cascade, custom properties or :hover /
// :active / :focus-visible, so these read computed styles from a real engine.

const VARIANTS: ButtonVariant[] = ['primary', 'secondary', 'ghost', 'danger', 'icon'];
const FILLED: ButtonVariant[] = ['primary', 'danger'];
const THEMES: Theme[] = ['light', 'dark'];

let host: HTMLElement;
let root: Root;

function render(ui: React.ReactNode): void {
  flushSync(() => root.render(ui));
}

function button(name: string): HTMLButtonElement {
  const found = Array.from(host.querySelectorAll('button')).find(
    (candidate) => (candidate.getAttribute('aria-label') ?? candidate.textContent) === name,
  );
  if (!found) throw new Error(`No button named ${name}`);
  return found;
}

function label(variant: ButtonVariant): string {
  return variant === 'icon' ? 'Close' : `${variant} action`;
}

function renderVariants(extra: Partial<React.ComponentProps<typeof Button>> = {}, suffix = ''): void {
  render(
    <div className="button-row">
      {VARIANTS.map((variant) => (
        <Button
          key={variant}
          variant={variant}
          aria-label={variant === 'icon' ? `Close${suffix}` : undefined}
          {...extra}
        >
          {variant === 'icon' ? <span aria-hidden="true">×</span> : `${variant} action${suffix}`}
        </Button>
      ))}
    </div>,
  );
}

async function unhoverAll(): Promise<void> {
  await unhover(host);
  for (const element of Array.from(host.querySelectorAll('button'))) await settle(element);
}

beforeEach(() => {
  host = document.createElement('div');
  // A panel surface, like the cards buttons sit on.
  host.style.background = 'rgb(var(--mm-panel))';
  host.style.padding = '24px';
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  root.unmount();
  host.remove();
  setTheme('light');
});

for (const theme of THEMES) {
  describe(`shared Button variants (${theme} theme)`, () => {
    beforeEach(() => setTheme(theme));

    it('renders each variant deliberately with readable text, and only primary carries the brand fill', () => {
      renderVariants();
      const primaryFill = getComputedStyle(button(label('primary'))).backgroundColor;
      for (const variant of VARIANTS) {
        const element = button(label(variant));
        const style = getComputedStyle(element);
        // inline-flex, or flex once blockified as a flex item of the row.
        expect(style.display, variant).toMatch(/^(inline-)?flex$/);
        expect(style.cursor, variant).toBe('pointer');
        expect(textContrast(element), `${variant} text contrast`).toBeGreaterThanOrEqual(4.5);
        if (variant !== 'primary') {
          expect(sameColor(style.backgroundColor, primaryFill), `${variant} is not a purple pill`).toBe(false);
        }
        if (!FILLED.includes(variant)) {
          // Outline-style variants sit on the panel instead of painting a fill of their own.
          expect(toRgba(style.backgroundColor)[3] === 0 || variant === 'secondary', variant).toBe(true);
        }
      }
    });

    it('hovers with a small tint and no scale, glow or brightness change', async () => {
      renderVariants();
      await unhoverAll();
      for (const variant of VARIANTS) {
        const element = button(label(variant));
        const resting = getComputedStyle(element).backgroundColor;
        await hover(element);
        const hovered = getComputedStyle(element);
        expect(sameColor(hovered.backgroundColor, resting), `${variant} hover changes the tint`).toBe(false);
        expect(hovered.transform, variant).toBe('none');
        expect(hovered.filter, variant).toBe('none');
        expect(hovered.boxShadow, variant).toBe('none');
        expect(textContrast(element), `${variant} hovered text contrast`).toBeGreaterThanOrEqual(4.5);
        await unhoverAll();
      }
    });

    it('presses distinctly (darker or inset) and never louder than hover', async () => {
      renderVariants();
      for (const variant of VARIANTS) {
        const element = button(label(variant));
        await hover(element);
        const hoverBackground = toRgba(getComputedStyle(element).backgroundColor);
        const pressed = await whilePressed(element, () => {
          const style = getComputedStyle(element);
          return {
            background: toRgba(style.backgroundColor),
            boxShadow: style.boxShadow,
            filter: style.filter,
            transform: style.transform,
            contrast: textContrast(element),
          };
        });
        expect(pressed.boxShadow, `${variant} press is inset`).toContain('inset');
        // No bright flash or white ring.
        expect(pressed.boxShadow, variant).not.toMatch(/rgba?\(255, 255, 255/);
        expect(pressed.filter, variant).toBe('none');
        expect(pressed.transform, variant).toBe('none');
        expect(pressed.background.join(), `${variant} press differs from hover`).not.toBe(hoverBackground.join());
        if (FILLED.includes(variant)) {
          expect(relativeLuminance(pressed.background), `${variant} press is darker than hover`).toBeLessThan(
            relativeLuminance(hoverBackground),
          );
        }
        expect(pressed.contrast, `${variant} pressed text contrast`).toBeGreaterThanOrEqual(4.5);
        await unhoverAll();
      }
    });

    it('shows a 3:1 keyboard focus outline that hover cannot hide', async () => {
      renderVariants();
      for (const variant of VARIANTS) {
        const element = button(label(variant));
        await keyboardFocus(element);
        expect(element.matches(':focus-visible'), variant).toBe(true);
        expect(outlineContrast(element), `${variant} focus outline contrast`).toBeGreaterThanOrEqual(3);
        expect(Number.parseFloat(getComputedStyle(element).outlineWidth), variant).toBeGreaterThanOrEqual(2);

        await hover(element);
        expect(element.matches(':hover'), variant).toBe(true);
        expect(outlineContrast(element), `${variant} focus outline while hovered`).toBeGreaterThanOrEqual(3);
        await unhoverAll();
        element.blur();
      }
    });

    it('does not react to hover or press while disabled', async () => {
      renderVariants({ disabled: true });
      for (const variant of VARIANTS) {
        const element = button(label(variant));
        const resting = getComputedStyle(element);
        const restingBackground = resting.backgroundColor;
        const restingShadow = resting.boxShadow;
        expect(resting.cursor, variant).toBe('not-allowed');
        expect(Number.parseFloat(resting.opacity), variant).toBeLessThan(1);
        await hover(element);
        const hovered = getComputedStyle(element);
        expect(sameColor(hovered.backgroundColor, restingBackground), `${variant} disabled hover`).toBe(true);
        expect(hovered.boxShadow, variant).toBe(restingShadow);
        await unhoverAll();
      }
    });

    it('marks aria-pressed and .active buttons as visibly selected', () => {
      render(
        <div className="button-row">
          <Button variant="secondary" aria-pressed={false}>Unselected</Button>
          <Button variant="secondary" aria-pressed>Pressed</Button>
          <Button variant="ghost" aria-pressed>Ghost pressed</Button>
          <Button variant="secondary" className="active">Active class</Button>
        </div>,
      );
      const unselected = getComputedStyle(button('Unselected'));
      for (const name of ['Pressed', 'Ghost pressed', 'Active class']) {
        const element = button(name);
        const style = getComputedStyle(element);
        expect(sameColor(style.backgroundColor, unselected.backgroundColor), `${name} background`).toBe(false);
        expect(sameColor(style.color, unselected.color), `${name} text colour`).toBe(false);
        expect(style.boxShadow, `${name} ring`).toContain('inset');
        expect(textContrast(element), `${name} text contrast`).toBeGreaterThanOrEqual(4.5);
        const ring = toRgba(style.borderTopColor);
        expect(contrastRatio(ring, effectiveBackground(element, { includeSelf: false })), `${name} border`).toBeGreaterThanOrEqual(3);
      }
    });
  });
}

describe('neutral button reset', () => {
  it('leaves an unclassed button transparent, borderless and inheriting text colour', () => {
    host.style.color = 'rgb(1, 2, 3)';
    render(<button type="button">Plain</button>);
    const style = getComputedStyle(button('Plain'));
    expect(toRgba(style.backgroundColor)[3]).toBe(0);
    expect(style.backgroundImage).toBe('none');
    expect(style.borderTopWidth).toBe('0px');
    expect(style.paddingLeft).toBe('0px');
    expect(sameColor(style.color, 'rgb(1, 2, 3)')).toBe(true);
    expect(style.boxShadow).toBe('none');
    // Button-backed rows and other bespoke controls start flat: no pill radius or scale.
    expect(style.borderTopLeftRadius).toBe('0px');
    expect(style.transform).toBe('none');
  });

  it('lets Tailwind-only buttons keep their own look instead of a purple fill', () => {
    render(
      <button type="button" className="rounded-full border border-slate-300 px-3 py-1.5 text-xs text-slate-700">
        Utility
      </button>,
    );
    const style = getComputedStyle(button('Utility'));
    expect(toRgba(style.backgroundColor)[3]).toBe(0);
    expect(textContrast(button('Utility'))).toBeGreaterThanOrEqual(4.5);
  });
});

describe('touch screens', () => {
  it('only applies :hover styles on devices that can hover, so taps never leave a sticky hover', () => {
    const ungated: string[] = [];
    const walk = (rules: CSSRuleList, gated: boolean) => {
      for (const rule of Array.from(rules)) {
        if (rule instanceof CSSStyleRule) {
          if (rule.selectorText.includes(':hover') && !gated) ungated.push(rule.selectorText);
          if (rule.cssRules?.length) walk(rule.cssRules, gated);
        } else if (rule instanceof CSSMediaRule) {
          walk(rule.cssRules, gated || /\(hover:\s*hover\)/.test(rule.conditionText));
        } else if ('cssRules' in rule && (rule as CSSGroupingRule).cssRules) {
          walk((rule as CSSGroupingRule).cssRules, gated);
        }
      }
    };
    let sheets = 0;
    for (const sheet of Array.from(document.styleSheets)) {
      let rules: CSSRuleList;
      try {
        rules = sheet.cssRules;
      } catch {
        continue;
      }
      sheets += 1;
      walk(rules, false);
    }
    expect(sheets).toBeGreaterThan(0);
    expect(ungated).toEqual([]);
  });
});
