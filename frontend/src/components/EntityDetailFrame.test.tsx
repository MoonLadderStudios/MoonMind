import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { EntityDetailFrame } from './EntityDetailFrame';

describe('EntityDetailFrame', () => {
  it('MM-1188 renders neutral detail slots without owning a collection sidebar', () => {
    const { container } = render(
      <EntityDetailFrame
        entity="workflow"
        context={<a href="/workflows">Workflows</a>}
        identityStatus={<h1>Build release</h1>}
        actions={<button type="button">Rerun</button>}
        facts={<span>Running</span>}
        tabs={<a href="/workflows/123/steps">Steps</a>}
        main={<p>Evidence</p>}
        factsRail={<p>Created today</p>}
        factsRailLabel="Workflow facts"
      />,
    );

    const frame = container.querySelector('[data-entity-detail-frame="workflow"]');
    expect(frame).not.toBeNull();
    expect(within(frame as HTMLElement).getByRole('heading', { name: 'Build release' })).toBeTruthy();
    expect(within(frame as HTMLElement).getByRole('navigation', { name: 'workflow detail sections' })).toBeTruthy();
    expect(within(frame as HTMLElement).getByRole('complementary', { name: 'Workflow facts' })).toBeTruthy();
    expect(frame?.querySelector('.collection-sidebar')).toBeNull();
  });

  it('localizes page state to the main slab while retaining successful regions', () => {
    render(
      <EntityDetailFrame
        entity="recurring"
        identityStatus={<h1>Nightly</h1>}
        facts={<span>Daily</span>}
        main={<p>Schedule content</p>}
        factsRail={<p>UTC</p>}
        state="error"
        stateContent="Schedule details are unavailable."
      />,
    );

    expect(screen.getByRole('heading', { name: 'Nightly' })).toBeTruthy();
    expect(screen.getByText('Daily')).toBeTruthy();
    expect(screen.getByText('UTC')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('Schedule details are unavailable.');
    expect(screen.queryByText('Schedule content')).toBeNull();
  });
});
