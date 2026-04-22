import { readFileSync } from 'node:fs';

import postcss from 'postcss';
import type { Root, Rule } from 'postcss';
import { describe, expect, it } from 'vitest';

import { render, screen } from '../utils/test-utils';
import { MaskedConicBorderBeam, MASKED_CONIC_BORDER_BEAM_TRACEABILITY } from './MaskedConicBorderBeam';

const missionControlCss = readFileSync(
  `${process.cwd()}/frontend/src/styles/mission-control.css`,
  'utf8',
);

let parsedCss: Root | null = null;

function cssRoot(): Root {
  parsedCss ??= postcss.parse(missionControlCss);
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

function cssRuleBlockMatching(matches: (rule: Rule) => boolean): string {
  let block = '';
  cssRoot().walkRules((rule: Rule) => {
    if (!block && matches(rule)) {
      block = rule.nodes.map((node) => `${node.toString()};`).join('\n');
    }
  });
  return block;
}

describe('MaskedConicBorderBeam', () => {
  it('renders a standalone active border-beam wrapper with deterministic defaults', () => {
    render(
      <MaskedConicBorderBeam data-testid="beam">
        <button type="button">Executing</button>
      </MaskedConicBorderBeam>,
    );

    const wrapper = screen.getByTestId('beam');
    expect(wrapper.classList.contains('masked-conic-border-beam')).toBe(true);
    expect(wrapper.getAttribute('data-active')).toBe('true');
    expect(wrapper.getAttribute('data-intensity')).toBe('normal');
    expect(wrapper.getAttribute('data-theme')).toBe('neutral');
    expect(wrapper.getAttribute('data-direction')).toBe('clockwise');
    expect(wrapper.getAttribute('data-trail')).toBe('soft');
    expect(wrapper.getAttribute('data-glow')).toBe('low');
    expect(wrapper.getAttribute('data-reduced-motion')).toBe('auto');
    expect(wrapper.style.getPropertyValue('--beam-border-radius')).toBe('16px');
    expect(wrapper.style.getPropertyValue('--beam-border-width')).toBe('1.5px');
    expect(wrapper.style.getPropertyValue('--beam-speed')).toBe('3.6s');
    expect(screen.getByTestId('masked-conic-border-beam-layer').getAttribute('aria-hidden')).toBe('true');
    expect(screen.getByTestId('masked-conic-border-beam-glow').getAttribute('aria-hidden')).toBe('true');
    expect(screen.getByRole('button', { name: 'Executing' })).toBeTruthy();
  });

  it('reflects custom inputs through stable data attributes and style variables', () => {
    render(
      <MaskedConicBorderBeam
        active
        borderRadius={12}
        borderWidth="2px"
        speed="fast"
        intensity="vivid"
        theme="success"
        direction="counterclockwise"
        trail="defined"
        glow="medium"
        reducedMotion="minimal"
        data-testid="beam"
      >
        <span>Surface content</span>
      </MaskedConicBorderBeam>,
    );

    const wrapper = screen.getByTestId('beam');
    expect(wrapper.getAttribute('data-intensity')).toBe('vivid');
    expect(wrapper.getAttribute('data-theme')).toBe('success');
    expect(wrapper.getAttribute('data-direction')).toBe('counterclockwise');
    expect(wrapper.getAttribute('data-trail')).toBe('defined');
    expect(wrapper.getAttribute('data-glow')).toBe('medium');
    expect(wrapper.getAttribute('data-reduced-motion')).toBe('minimal');
    expect(wrapper.style.getPropertyValue('--beam-border-radius')).toBe('12px');
    expect(wrapper.style.getPropertyValue('--beam-border-width')).toBe('2px');
    expect(wrapper.style.getPropertyValue('--beam-speed')).toBe('2.8s');
    expect(screen.getByText('Surface content').closest('.masked-conic-border-beam__content')).toBeTruthy();
  });

  it('does not render moving beam or glow layers when inactive', () => {
    render(
      <MaskedConicBorderBeam active={false} data-testid="beam">
        <span>Idle content</span>
      </MaskedConicBorderBeam>,
    );

    expect(screen.getByTestId('beam').getAttribute('data-active')).toBe('false');
    expect(screen.queryByTestId('masked-conic-border-beam-layer')).toBeNull();
    expect(screen.queryByTestId('masked-conic-border-beam-glow')).toBeNull();
    expect(screen.getByText('Idle content')).toBeTruthy();
  });

  it('keeps MM-465 traceability on the exported contract', () => {
    expect(MASKED_CONIC_BORDER_BEAM_TRACEABILITY).toEqual({
      jiraIssueKey: 'MM-465',
      designRequirements: [
        'DESIGN-REQ-001',
        'DESIGN-REQ-002',
        'DESIGN-REQ-003',
        'DESIGN-REQ-010',
        'DESIGN-REQ-016',
      ],
    });
  });

  it('defines the conic beam as a masked border-ring layer instead of content animation', () => {
    const layerBlock = cssRuleBlock('.masked-conic-border-beam__layer');
    const contentBlock = cssRuleBlock('.masked-conic-border-beam__content');

    expect(layerBlock).toContain('position: absolute;');
    expect(layerBlock).toContain('padding: var(--beam-border-width);');
    expect(layerBlock).toContain('conic-gradient');
    expect(layerBlock).toMatch(/mask-composite:\s*exclude/);
    expect(layerBlock).toMatch(/-webkit-mask-composite:\s*xor/);
    expect(contentBlock).not.toMatch(/animation:/);
    expect(contentBlock).not.toContain('mask');
  });

  it('defines reduced-motion behavior and excludes shimmer, spinner, and completion effects', () => {
    const minimalBlock = cssRuleBlock('.masked-conic-border-beam[data-reduced-motion="minimal"] .masked-conic-border-beam__layer');
    const reducedMotionBlock = cssRuleBlockMatching((rule) =>
      rule.selector.includes('.masked-conic-border-beam[data-reduced-motion="auto"] .masked-conic-border-beam__layer') &&
      Boolean(rule.parent?.toString().includes('prefers-reduced-motion: reduce')),
    );
    const allBeamCss = missionControlCss
      .split('\n')
      .filter((line) => line.includes('masked-conic-border-beam'))
      .join('\n')
      .toLowerCase();

    expect(minimalBlock).toContain('animation: none;');
    expect(reducedMotionBlock).toContain('animation: none;');
    expect(allBeamCss).not.toContain('spinner');
    expect(allBeamCss).not.toContain('shimmer');
    expect(allBeamCss).not.toContain('success-burst');
    expect(allBeamCss).not.toContain('completion-pulse');
  });
});
