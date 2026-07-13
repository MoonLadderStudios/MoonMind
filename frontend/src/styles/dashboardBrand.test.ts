import { readFileSync } from 'node:fs';

import postcss from 'postcss';
import type { Root, Rule } from 'postcss';
import { describe, expect, it } from 'vitest';

const dashboardCss = readFileSync(
  `${process.cwd()}/frontend/src/styles/dashboard.css`,
  'utf8',
);

let parsedCss: Root | null = null;

function cssRoot(): Root {
  parsedCss ??= postcss.parse(dashboardCss);
  return parsedCss;
}

function cssRuleBlock(selector: string): string {
  const blocks: string[] = [];
  cssRoot().walkRules((rule: Rule) => {
    const selectors = rule.selector.split(',').map((item) => item.trim());
    if (selectors.includes(selector)) {
      blocks.push(rule.nodes.map((node) => `${node.toString()};`).join('\n'));
    }
  });
  return blocks.join('\n');
}

describe('dashboard masthead brand styles', () => {
  it('keeps Moon white and renders the MoonMind header at the compact size', () => {
    expect(cssRuleBlock('.masthead-brand')).toContain('color: rgb(var(--mm-ink));');
    expect(cssRuleBlock('.masthead-brand')).toContain('text-decoration: none;');
    expect(cssRuleBlock('.masthead-brand:hover')).toContain('text-decoration: none;');
    expect(cssRuleBlock('.masthead-brand:focus-visible')).toContain('text-decoration: none;');
    expect(cssRuleBlock('.masthead-brand-moon')).toContain('color: rgb(255 255 255);');
    expect(cssRuleBlock('.masthead-brand-mind')).toContain('color: inherit;');
    expect(cssRuleBlock('.masthead-brand h1')).toContain(
      'font-size: clamp(1.12rem, 1.7vw, 1.52rem);',
    );
  });

  it('MM-1114 hides the workflow list display control on mobile and preserves accessible focus tokens', () => {
    expect(dashboardCss).toMatch(
      /@media \(max-width: 767px\)\s*\{[\s\S]*\.workflow-list-display-control\s*\{[^}]*display:\s*none/s,
    );
    expect(cssRuleBlock('.workflow-list-display-option:focus-visible')).toContain(
      'box-shadow: var(--mm-control-focus-ring);',
    );
    expect(cssRuleBlock('.workflow-list-display-option[aria-checked="true"]')).toContain(
      'color: rgb(var(--mm-accent));',
    );
    expect(cssRuleBlock('.workflow-list-display-option[aria-checked="true"]:hover')).toContain(
      'border-color: rgb(var(--mm-accent-2) / 0.72);',
    );
    expect(cssRuleBlock('.workflow-list-display-option[aria-checked="true"]:hover')).not.toContain(
      'color: rgb(var(--mm-ink));',
    );
  });

  it('MM-1192 layers the compact navigation without overlap and disables large reduced-motion transitions', () => {
    expect(dashboardCss).toMatch(
      /@media \(max-width: 1180px\)\s*\{[\s\S]*\.route-nav\s*\{[^}]*position:\s*fixed;[^}]*z-index:\s*51;[\s\S]*\.dashboard-nav-backdrop\s*\{[^}]*position:\s*fixed;[^}]*z-index:\s*50;/s,
    );
    expect(dashboardCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*\.route-nav,[\s\S]*\.dashboard-nav-backdrop\s*\{[^}]*animation:\s*none !important;[^}]*transition:\s*none !important;[^}]*transform:\s*none !important;/s,
    );
  });

  it('MM-1200 preserves System popover geometry, focus, active, mobile, and motion contracts', () => {
    const popover = cssRuleBlock('.dashboard-system-popover');
    expect(popover).toContain('position: absolute;');
    expect(popover).toContain('right: 0;');
    expect(popover).toContain('z-index: 60;');
    expect(popover).toContain('background: rgb(var(--mm-panel));');
    expect(popover).toContain('box-shadow: var(--mm-elevation-panel);');
    expect(dashboardCss).not.toMatch(
      /@media \(min-width: 1181px\)\s*\{[\s\S]*\.route-nav-primary\s*\{[^}]*overflow-[xy]:\s*(?:auto|hidden|scroll);/s,
    );
    expect(dashboardCss).not.toMatch(
      /@media \(min-width: 1181px\)\s*\{[\s\S]*\.masthead-nav\s*\{[^}]*overflow-[xy]:\s*(?:auto|hidden|scroll);/s,
    );

    expect(cssRuleBlock('.dashboard-system-trigger:focus-visible')).toContain(
      'box-shadow: var(--mm-control-focus-ring);',
    );
    expect(cssRuleBlock('.dashboard-system-trigger.active')).toContain('color: rgb(var(--mm-ink));');
    expect(cssRuleBlock('.dashboard-system-trigger.active::after')).toContain(
      'background: rgb(var(--mm-accent));',
    );
    expect(dashboardCss).toMatch(
      /@media \(max-width: 1180px\)\s*\{[\s\S]*\.dashboard-system-menu\s*\{[^}]*display:\s*none;[\s\S]*\.dashboard-system-inline\s*\{[^}]*display:\s*block;/s,
    );
    expect(dashboardCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*\.dashboard-system-trigger,[\s\S]*\.dashboard-system-popover\s*\{[^}]*animation:\s*none !important;[^}]*transition:\s*none !important;[^}]*transform:\s*none !important;/s,
    );
  });

  it('gives the nav buttons and list display control the highlight-edge look with a sliding thumb', () => {
    // The shared liquid-glass treatment: faint uniform edge lines plus
    // diagonally offset corner boosts (top-left white, bottom-right violet),
    // and a quite faint accent glow kept almost entirely on the bottom edge —
    // the negative spread pulls the blur off the sides. The sides stay open —
    // no perimeter ring. The hover variant swaps the under-glow to the cyan
    // accent (executing-pill hue).
    const highlightShadow =
      /box-shadow:\s*var\(--mm-shadow-highlight-edge\);/;
    const asymmetricInsets =
      String.raw`inset 0 1px 0 rgb\(255 255 255 \/ 0\.12\),\s*inset 2px 2px 3px -2px rgb\(255 255 255 \/ 0\.3\),\s*inset 0 -1px 0 rgb\(167 139 250 \/ 0\.18\),\s*inset -2px -2px 3px -2px rgb\(167 139 250 \/ 0\.55\)`;
    expect(dashboardCss).toMatch(
      new RegExp(
        String.raw`--mm-shadow-highlight-edge:\s*${asymmetricInsets},\s*0 5px 9px -5px rgb\(var\(--mm-accent\) \/ 0\.45\);`,
      ),
    );
    expect(dashboardCss).toMatch(
      new RegExp(
        String.raw`--mm-shadow-highlight-edge-hover:\s*${asymmetricInsets},\s*0 5px 9px -5px rgb\(var\(--mm-accent-2\) \/ 0\.55\);`,
      ),
    );
    // The tokens must stay side-open: no perimeter ring, so the buttons read
    // as corner-lit glass instead of an outline.
    expect(dashboardCss).not.toMatch(
      /--mm-shadow-highlight-edge(?:-hover)?:[^;]*0 0 0 1px/s,
    );

    // Radio group: highlight-edge chrome, tightened enough (option < 2rem,
    // padding < 0.18rem) that it does not out-size the nav buttons, plus a
    // segmented-control-style sliding thumb driven by the checked option.
    const control = cssRuleBlock('.workflow-list-display-control');
    expect(control).toContain('border: 0;');
    expect(control).toContain('background: transparent;');
    expect(control).toMatch(highlightShadow);
    expect(control).toContain('padding: 0.12rem;');
    const thumb = cssRuleBlock('.workflow-list-display-control::before');
    expect(thumb).toContain(
      'transform: translateX(calc(var(--list-display-active-index) * 1.8rem));',
    );
    expect(thumb).toContain('box-shadow: inset 0 0 0 1px rgb(var(--mm-accent) / 0.6);');
    expect(thumb).toMatch(/transition: transform \d+ms/);
    expect(dashboardCss).toMatch(
      /\.workflow-list-display-control:has\(\.workflow-list-display-option:nth-child\(2\)\[aria-checked="true"\]\)\s*\{[^}]*--list-display-active-index: 1;/s,
    );
    expect(dashboardCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*\.workflow-list-display-control::before\s*\{[^}]*transition:\s*none;/s,
    );
    expect(cssRuleBlock('.workflow-list-display-option')).toContain('width: 1.6rem;');
    // Icons keep their size regardless of the tighter borders.
    expect(cssRuleBlock('.workflow-list-display-option svg')).toContain('width: 0.95rem;');

    // Workflows/Create and the System trigger share the same highlight-edge
    // look. The shadows are non-layout-affecting so the active underline
    // geometry is unchanged.
    const primary = cssRuleBlock('.route-nav-primary a');
    expect(primary).not.toContain('var(--mm-glass-fill)');
    expect(primary).toMatch(highlightShadow);
    const trigger = cssRuleBlock('.dashboard-system-trigger');
    expect(trigger).toContain('background: transparent;');
    expect(trigger).toMatch(highlightShadow);

    // Hover: all three buttons brighten, grow slightly, and swap the purple
    // under-glow for the cyan accent. The trigger states these explicitly so
    // the filled-CTA global button:hover shadow cannot leak onto it.
    for (const hover of [
      cssRuleBlock('.route-nav-primary a:hover'),
      cssRuleBlock('.dashboard-system-trigger:hover'),
    ]) {
      expect(hover).toContain('box-shadow: var(--mm-shadow-highlight-edge-hover);');
      expect(hover).toContain('transform: scale(var(--mm-control-hover-scale));');
      expect(hover).toMatch(/filter: brightness\([\d.]+\)/);
    }

    // Opening the System popover keeps its highlighted surface without
    // leaving the trigger scaled or brightened after hover ends.
    const expandedTrigger = cssRuleBlock('.dashboard-system-trigger[aria-expanded="true"]');
    expect(expandedTrigger).toContain('box-shadow: var(--mm-shadow-highlight-edge-hover);');
    expect(expandedTrigger).not.toContain('transform:');
    expect(expandedTrigger).not.toContain('filter:');
  });

  it('marks the open System selection like the sidebar instead of the trigger underline', () => {
    // The trigger keeps its purple underline when a System destination is
    // active (asserted above); the selected row inside the open popover must
    // not repeat it, and instead gets the sidebar-style left accent bar + fill.
    const underlineSuppression = cssRuleBlock('.dashboard-system-popover a.active::after');
    expect(underlineSuppression).toContain('content: none;');
    const activeRow = cssRuleBlock('.dashboard-system-popover a.active');
    expect(activeRow).toContain('box-shadow: inset 3px 0 0 rgb(var(--mm-accent));');
    expect(activeRow).toContain('background: rgb(var(--mm-accent) / 0.12);');

    // The active-row rule shares specificity with the earlier
    // `a:focus-visible` rule, so a later, more specific rule must combine the
    // focus ring with the active inset shadow; otherwise a keyboard-focused
    // active row loses its distinct focus indicator.
    const activeFocus = cssRuleBlock('.dashboard-system-popover a.active:focus-visible');
    expect(activeFocus).toContain(
      'box-shadow: var(--mm-control-focus-ring), inset 3px 0 0 rgb(var(--mm-accent));',
    );
  });

  it('keeps the masthead-nav skills create button green and offset below the nav height', () => {
    const button = cssRuleBlock('.skills-create-nav-button');
    expect(button).toContain('color: rgb(var(--mm-ok));');
    expect(button).toContain('background: rgb(var(--mm-ok) / 0.16);');
    expect(button).toMatch(/box-shadow:\s*inset 0 0 0 1px rgb\(var\(--mm-ok\) \/ 0\.55\),\s*inset 0 1px 0 var\(--mm-glass-edge\);/);
  });
});
