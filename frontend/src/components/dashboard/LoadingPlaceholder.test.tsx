import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  LoadingPlaceholder,
  type LoadingPlaceholderVariant,
} from './LoadingPlaceholder';

const falseContent = [
  /completed/i,
  /healthy/i,
  /success/i,
  /configured/i,
  /workflow-[a-z0-9-]+/i,
  /\d{4}-\d{2}-\d{2}/,
];

describe('LoadingPlaceholder', () => {
  it('renders distinct page-matched structures for materially different surfaces', () => {
    const variants: LoadingPlaceholderVariant[] = [
      'list',
      'detail',
      'settings',
      'catalog',
      'table',
      'compact-controls',
      'form-controls',
      'metric-strip',
      'operations',
    ];

    render(
      <>
        {variants.map((variant) => (
          <LoadingPlaceholder
            key={variant}
            surface="workflow-list"
            region={variant}
            variant={variant}
            density={variant === 'detail' ? 'detail-heavy' : 'normal'}
          />
        ))}
      </>,
    );

    for (const variant of variants) {
      const region = screen.getByTestId(`loading-placeholder-${variant}`);
      expect(region.getAttribute('aria-busy')).toBe('true');
      expect(region.getAttribute('data-variant')).toBe(variant);
      expect(within(region).getAllByTestId('loading-placeholder-block').length).toBeGreaterThan(1);
    }
  });

  it('marks scoped pending regions while preserving reliable surrounding context', () => {
    render(
      <LoadingPlaceholder
        surface="settings"
        region="managed secrets"
        variant="table"
        density="compact"
        preserveContext
      />,
    );

    const region = screen.getByRole('status', {
      name: 'Settings managed secrets loading placeholder',
    });
    expect(region.getAttribute('data-preserve-context')).toBe('true');
    expect(region.getAttribute('data-density')).toBe('compact');
    expect(within(region).getAllByTestId('loading-placeholder-row').length).toBeGreaterThanOrEqual(3);
  });

  it('keeps decorative blocks from exposing fabricated values or operational status', () => {
    render(
      <LoadingPlaceholder
        surface="workflow-detail"
        region="summary"
        variant="detail"
        density="detail-heavy"
      />,
    );

    const region = screen.getByRole('status', {
      name: 'Workflow detail summary loading placeholder',
    });
    for (const pattern of falseContent) {
      expect(region.textContent || '').not.toMatch(pattern);
    }
    expect(region.querySelectorAll('[aria-hidden="true"]').length).toBeGreaterThan(0);
  });

  it('supports compact control and table-like density without oversized generic cards', () => {
    render(
      <>
        <LoadingPlaceholder
          surface="workflow-start"
          region="controls"
          variant="compact-controls"
          density="compact"
        />
        <LoadingPlaceholder
          surface="schedules"
          region="runs"
          variant="table"
          density="compact"
        />
      </>,
    );

    expect(screen.getByTestId('loading-placeholder-compact-controls').getAttribute('data-density')).toBe('compact');
    expect(screen.getByTestId('loading-placeholder-table').querySelectorAll('.loading-placeholder__cell')).toHaveLength(12);
  });
});
