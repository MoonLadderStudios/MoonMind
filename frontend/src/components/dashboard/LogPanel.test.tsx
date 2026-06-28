import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { LogPanel } from './LogPanel';

describe('LogPanel (MM-959)', () => {
  let writeText: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('copies the text content to the clipboard', async () => {
    render(<LogPanel title="Stdout" text="hello logs" collapsible={false} />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    expect(writeText).toHaveBeenCalledWith('hello logs');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Copied' })).toBeTruthy());
  });

  it('toggles line wrapping via the wrap control', () => {
    render(<LogPanel title="Stdout" text="hello logs" collapsible={false} ariaLabel="Stdout output" />);
    const pre = screen.getByLabelText('Stdout output');
    expect(pre.getAttribute('data-wrap')).toBe('on');
    fireEvent.click(screen.getByRole('checkbox', { name: /Wrap lines/ }));
    expect(pre.getAttribute('data-wrap')).toBe('off');
  });

  it('renders a download link when a download URL is provided', () => {
    render(
      <LogPanel
        title="Stdout"
        text="hello logs"
        collapsible={false}
        downloadUrl="/agent-runs/x/logs/stdout"
        downloadFileName="stdout.log"
      />,
    );
    const link = screen.getByRole('link', { name: 'Download' });
    expect(link.getAttribute('href')).toBe('/agent-runs/x/logs/stdout');
    expect(link.getAttribute('download')).toBe('stdout.log');
  });

  it('shows empty and error states without inline color styles', () => {
    const { rerender } = render(<LogPanel title="Stdout" text="" collapsible={false} emptyMessage="(no stdout)" />);
    let pre = screen.getByText('(no stdout)');
    expect(pre.className).toContain('log-panel__output--empty');
    expect(pre.getAttribute('style')).toBeNull();

    rerender(<LogPanel title="Stdout" isError collapsible={false} errorMessage="Error loading stdout" />);
    pre = screen.getByText('Error loading stdout');
    expect(pre.className).toContain('log-panel__output--error');
  });

  it('reports expansion so callers can lazily fetch content', () => {
    const onExpandedChange = vi.fn();
    render(<LogPanel title="Stdout" text="hello" onExpandedChange={onExpandedChange} />);
    // Collapsed initially: controls are not rendered.
    expect(screen.queryByRole('button', { name: 'Copy' })).toBeNull();
    fireEvent.click(screen.getByText('Stdout'));
    expect(onExpandedChange).toHaveBeenCalledWith(true);
    expect(screen.getByRole('button', { name: 'Copy' })).toBeTruthy();
  });
});
