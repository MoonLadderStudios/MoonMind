import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DashboardSurface, type DashboardSurfaceVariant } from './DashboardSurface';

const VARIANTS: DashboardSurfaceVariant[] = [
  'page',
  'controlDeck',
  'dataSlab',
  'formSlab',
  'evidenceSlab',
  'floatingRail',
  'debugDrawer',
];

describe('DashboardSurface (MM-959)', () => {
  it.each(VARIANTS)('renders the %s variant class', (variant) => {
    render(
      <DashboardSurface variant={variant} aria-label={`surface-${variant}`}>
        body
      </DashboardSurface>,
    );
    const surface = screen.getByLabelText(`surface-${variant}`);
    expect(surface.classList.contains('dashboard-surface')).toBe(true);
    expect(surface.classList.contains(`dashboard-surface--${variant}`)).toBe(true);
    expect(surface.getAttribute('data-surface')).toBe(variant);
  });

  it('defaults to a section element and merges custom class names', () => {
    render(
      <DashboardSurface variant="dataSlab" className="extra" aria-label="merged">
        body
      </DashboardSurface>,
    );
    const surface = screen.getByLabelText('merged');
    expect(surface.tagName).toBe('SECTION');
    expect(surface.classList.contains('extra')).toBe(true);
  });

  it('honors the as prop for the rendered element', () => {
    render(
      <DashboardSurface variant="page" as="div" aria-label="as-div">
        body
      </DashboardSurface>,
    );
    expect(screen.getByLabelText('as-div').tagName).toBe('DIV');
  });
});
