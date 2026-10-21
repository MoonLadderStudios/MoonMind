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
    const selectors = rule.selector
      .split(',')
      .map((item) => item.trim().replace(/\s+/g, ' '));
    if (selectors.includes(selector)) {
      blocks.push(rule.nodes.map((node) => `${node.toString()};`).join('\n'));
    }
  });
  return blocks.join('\n');
}

describe('dashboard page layout styles', () => {
  it('centers the create page title', () => {
    expect(cssRuleBlock('.workflow-start-heading')).toContain('text-align: center;');
  });

  it('outlines the create workflow button after it is clicked while retaining its icon color', () => {
    const clickedSubmit = cssRuleBlock(
      '.queue-submit-primary--icon.queue-submit-primary--arrow-exit',
    );
    expect(clickedSubmit).toContain(
      'background-color: rgb(var(--mm-action-primary) / 0.12);',
    );
    expect(clickedSubmit).toContain('background-image: none;');
    expect(clickedSubmit).toContain('border-color: rgb(var(--mm-action-primary));');
    expect(clickedSubmit).toContain('color: rgb(var(--mm-action-primary));');
  });

  it('morphs the clicked create workflow arrow into a check in the primary color', () => {
    expect(
      cssRuleBlock(
        '.queue-submit-primary--icon.queue-submit-primary--arrow-exit .queue-submit-primary-arrow-glyph[data-submit-icon="arrow"]',
      ),
    ).toContain('animation: queue-submit-primary-arrow-exit');
    expect(
      cssRuleBlock(
        '.queue-submit-primary--icon.queue-submit-primary--arrow-exit .queue-submit-primary-arrow-glyph[data-submit-icon="check"]',
      ),
    ).toContain('animation: queue-submit-primary-check-pop');
    expect(
      cssRuleBlock('.queue-submit-primary--icon .queue-submit-primary-arrow-glyph--check'),
    ).toContain('opacity: 0;');
    // The check inherits the button's clicked color, which is the primary action token.
    expect(cssRuleBlock('.queue-submit-primary-arrow svg')).toContain(
      'stroke: currentcolor;',
    );
  });
  // Rendered coverage: browser/controlPresentation.browser.test.tsx and buttonAccessibility.browser.test.tsx.

});

describe('dashboard masthead brand styles', () => {
  // Rendered coverage: browser/controlPresentation.browser.test.tsx and buttonAccessibility.browser.test.tsx.

  it('MM-1192 layers the compact navigation without overlap and disables large reduced-motion transitions', () => {
    expect(dashboardCss).toMatch(
      /@media \(max-width: 1180px\)\s*\{[\s\S]*\.route-nav\s*\{[^}]*position:\s*fixed;[^}]*z-index:\s*51;[\s\S]*\.dashboard-nav-backdrop\s*\{[^}]*position:\s*fixed;[^}]*z-index:\s*50;/s,
    );
    expect(dashboardCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*\.route-nav,[\s\S]*\.dashboard-nav-backdrop\s*\{[^}]*animation:\s*none !important;[^}]*transition:\s*none !important;[^}]*transform:\s*none !important;/s,
    );
  });
  // Rendered coverage: browser/controlPresentation.browser.test.tsx and buttonAccessibility.browser.test.tsx.

  // Active selection and simultaneous focus are covered in controlPresentation.browser.test.tsx.

});
