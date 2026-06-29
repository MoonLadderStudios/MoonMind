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
    expect(cssRuleBlock('.masthead-brand-mind')).toContain('color: rgb(var(--mm-ink));');
    expect(cssRuleBlock('.masthead-brand h1')).toContain(
      'font-size: clamp(1.12rem, 1.7vw, 1.52rem);',
    );
  });
});
