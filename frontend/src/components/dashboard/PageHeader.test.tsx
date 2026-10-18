import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { PageHeader, documentTitleFor } from './PageHeader';

describe('PageHeader', () => {
  afterEach(() => {
    document.title = '';
  });

  it('renders the title as the page h1 and sets the tab title', () => {
    render(<PageHeader title="Recurring" description="Scheduled workflows." />);

    expect(screen.getByRole('heading', { level: 1, name: 'Recurring' })).toBeTruthy();
    expect(screen.getByText('Scheduled workflows.')).toBeTruthy();
    expect(document.title).toBe('Recurring | MoonMind');
  });

  it('uses documentTitle when the title is not plain text', () => {
    render(<PageHeader title={<span>Run 42</span>} documentTitle="Run 42" />);

    expect(document.title).toBe('Run 42 | MoonMind');
  });

  it('renders the primary action and a refresh button', () => {
    const onRefresh = vi.fn();
    render(
      <PageHeader
        title="Skills"
        actions={<button type="button">Add skill</button>}
        onRefresh={onRefresh}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Add skill' })).toBeTruthy();
  });

  it('disables refresh while refreshing', () => {
    render(<PageHeader title="Agents" onRefresh={() => undefined} refreshing />);

    expect((screen.getByRole('button', { name: 'Refreshing…' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('falls back to the product name for an empty title', () => {
    expect(documentTitleFor('  ')).toBe('MoonMind');
  });
});
