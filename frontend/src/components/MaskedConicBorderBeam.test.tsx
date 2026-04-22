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
    expect(wrapper.style.getPropertyValue('--beam-inner-inset')).toBe('');
    expect(wrapper.style.getPropertyValue('--beam-inner-radius')).toBe('');
    const contentWrapper = screen.getByText('Surface content').closest('.masked-conic-border-beam__content');
    expect(contentWrapper).toBeTruthy();
    expect(contentWrapper?.tagName).toBe('DIV');
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

  it('keeps MM-465 and MM-466 traceability on the exported contract', () => {
    expect(MASKED_CONIC_BORDER_BEAM_TRACEABILITY).toEqual({
      jiraIssueKeys: ['MM-465', 'MM-466'],
      designRequirements: [
        'DESIGN-REQ-001',
        'DESIGN-REQ-002',
        'DESIGN-REQ-003',
        'DESIGN-REQ-004',
        'DESIGN-REQ-005',
        'DESIGN-REQ-006',
        'DESIGN-REQ-010',
        'DESIGN-REQ-011',
        'DESIGN-REQ-016',
      ],
    });
  });

  it('exposes MM-466 geometry defaults for the border-ring mask and beam footprint', () => {
    render(
      <MaskedConicBorderBeam data-testid="beam">
        <span>Geometry defaults</span>
      </MaskedConicBorderBeam>,
    );

    const wrapper = screen.getByTestId('beam');
    expect(wrapper.style.getPropertyValue('--beam-head-arc')).toBe('12deg');
    expect(wrapper.style.getPropertyValue('--beam-tail-arc')).toBe('28deg');
    expect(wrapper.style.getPropertyValue('--beam-inner-inset')).toBe('');
    expect(wrapper.style.getPropertyValue('--beam-inner-radius')).toBe('');
  });

  it('keeps active layered geometry separate from nested content', () => {
    render(
      <MaskedConicBorderBeam data-testid="beam">
        <button type="button">Retry execution</button>
        <span>Readable child copy</span>
      </MaskedConicBorderBeam>,
    );

    const wrapper = screen.getByTestId('beam');
    const layer = screen.getByTestId('masked-conic-border-beam-layer');
    const glow = screen.getByTestId('masked-conic-border-beam-glow');
    const contentWrapper = screen.getByText('Readable child copy').closest('.masked-conic-border-beam__content');

    expect(wrapper.firstElementChild).toBe(layer);
    expect(layer.nextElementSibling).toBe(glow);
    expect(glow.nextElementSibling).toBe(contentWrapper);
    expect(layer.getAttribute('aria-hidden')).toBe('true');
    expect(glow.getAttribute('aria-hidden')).toBe('true');
    expect(screen.getByRole('button', { name: 'Retry execution' })).toBeTruthy();
  });

  it('defines the conic beam as a masked border-ring layer instead of content animation', () => {
    const rootBlock = cssRuleBlock('.masked-conic-border-beam');
    const layerBlock = cssRuleBlock('.masked-conic-border-beam__layer');
    const glowBlock = cssRuleBlock('.masked-conic-border-beam__glow');
    const contentBlock = cssRuleBlock('.masked-conic-border-beam__content');

    expect(rootBlock).toContain('--beam-head-arc: 12deg;');
    expect(rootBlock).toContain('--beam-tail-arc: 28deg;');
    expect(rootBlock).toContain('--beam-inner-inset: var(--beam-border-width);');
    expect(rootBlock).toContain('--beam-inner-radius: calc(var(--beam-border-radius) - var(--beam-border-width));');
    expect(layerBlock).toContain('position: absolute;');
    expect(layerBlock).toContain('border-radius: inherit;');
    expect(layerBlock).toContain('padding: var(--beam-inner-inset);');
    expect(layerBlock).not.toContain('border-radius: var(--beam-inner-radius);');
    expect(layerBlock).toContain('conic-gradient');
    expect(layerBlock).toContain('var(--beam-tail-start)');
    expect(layerBlock).toContain('var(--beam-tail-end)');
    expect(layerBlock).toContain('var(--beam-head-end)');
    expect(layerBlock).toMatch(/mask-composite:\s*exclude/);
    expect(layerBlock).toMatch(/-webkit-mask-composite:\s*xor/);
    expect(glowBlock).toContain('border-radius: inherit;');
    expect(glowBlock).toContain('padding: var(--beam-inner-inset);');
    expect(glowBlock).not.toContain('border-radius: var(--beam-inner-radius);');
    expect(glowBlock).toContain('conic-gradient');
    expect(glowBlock).toContain('filter: blur(5px);');
    expect(glowBlock).toContain('opacity: var(--beam-glow-opacity);');
    expect(glowBlock).toMatch(/mask-composite:\s*exclude/);
    expect(glowBlock).toMatch(/-webkit-mask-composite:\s*xor/);
    expect(contentBlock).toContain('border-radius: var(--beam-inner-radius);');
    expect(contentBlock).not.toMatch(/animation:/);
    expect(contentBlock).not.toContain('mask');
  });

  it('keeps trail variants on the beam footprint without changing orbital speed', () => {
    const layerBlock = cssRuleBlock('.masked-conic-border-beam__layer');
    const noTrailBlock = cssRuleBlock('.masked-conic-border-beam[data-trail="none"] .masked-conic-border-beam__layer');
    const definedTrailBlock = cssRuleBlock('.masked-conic-border-beam[data-trail="defined"] .masked-conic-border-beam__layer');

    expect(layerBlock).toContain('animation: masked-conic-border-beam-orbit var(--beam-speed) linear infinite;');
    expect(noTrailBlock).toContain('background: conic-gradient');
    expect(noTrailBlock).toContain('var(--beam-head-color) var(--beam-head-start) var(--beam-head-end)');
    expect(definedTrailBlock).toContain('background: conic-gradient');
    expect(noTrailBlock).not.toContain('animation:');
    expect(definedTrailBlock).not.toContain('animation:');
    expect(noTrailBlock).not.toContain('--beam-speed');
    expect(definedTrailBlock).not.toContain('--beam-speed');
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
