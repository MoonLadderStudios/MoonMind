import { readFileSync } from 'node:fs';
import postcss from 'postcss';
import type { AtRule, Root, Rule } from 'postcss';
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
  let block = '';
  cssRoot().walkRules((rule: Rule) => {
    if (rule.selector === selector) {
      block += rule.toString();
    }
  });
  return block;
}

function cssRuleBlockMatching(matches: (rule: Rule) => boolean): string {
  let block = '';
  cssRoot().walkRules((rule: Rule) => {
    if (matches(rule)) {
      block += rule.toString();
    }
  });
  return block;
}

describe('dashboard loading placeholder styles', () => {
  it('uses stable responsive dimensions that do not force narrow viewport overflow', () => {
    expect(cssRuleBlock('.loading-placeholder')).toContain('max-width: 100%;');
    expect(cssRuleBlock('.loading-placeholder')).toContain('overflow: hidden;');
    expect(cssRuleBlock('.loading-placeholder__grid')).toContain('minmax(0, 1fr)');
    expect(cssRuleBlock('.loading-placeholder__block')).toContain('max-width: 100%;');
  });

  it('keeps reduced-motion placeholders static while preserving structure', () => {
    const reducedMotionBlock = cssRuleBlockMatching((rule) => {
      const parent = rule.parent as AtRule | undefined;
      return parent?.type === 'atrule'
        && parent.name === 'media'
        && parent.params.includes('prefers-reduced-motion: reduce')
        && rule.selector.includes('.loading-placeholder__block');
    });
    expect(reducedMotionBlock).toContain('animation: none');
    expect(reducedMotionBlock).toContain('background-position: 50% 50%');
  });

  it('defines compact rows and controls-shaped blocks for operational loading regions', () => {
    expect(cssRuleBlock('.loading-placeholder[data-density="compact"]')).toContain('--loading-placeholder-row-height: 0.75rem;');
    expect(cssRuleBlock('.loading-placeholder[data-variant="compact-controls"] .loading-placeholder__block')).toContain('border-radius: 999px;');
  });
});
