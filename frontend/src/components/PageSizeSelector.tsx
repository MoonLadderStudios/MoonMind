import { JSX, useLayoutEffect, useRef } from 'react';

export const PAGE_SIZE_OPTIONS = [20, 25, 50, 100] as const;
export const DEFAULT_PAGE_SIZE = 50;

const WORKFLOW_LIVE_UPDATES_ENABLED_PREFIX = 'Live updates enabled. Polling every ';

function suppressWorkflowLiveUpdatesEnabledFooterText(root: HTMLElement | null): void {
  const footer = root?.closest('.workflow-list-results-footer');
  const liveStatus = footer?.querySelector<HTMLElement>('.workflow-list-footer-live > .small:first-child');
  if (!liveStatus) return;
  if (!liveStatus.textContent?.startsWith(WORKFLOW_LIVE_UPDATES_ENABLED_PREFIX)) {
    liveStatus.removeAttribute('aria-hidden');
    return;
  }
  liveStatus.textContent = '';
  liveStatus.setAttribute('aria-hidden', 'true');
}

export function parsePageSize(raw: string | null): number {
  const parsed = Number(raw || DEFAULT_PAGE_SIZE);
  return PAGE_SIZE_OPTIONS.includes(parsed as (typeof PAGE_SIZE_OPTIONS)[number])
    ? parsed
    : DEFAULT_PAGE_SIZE;
}

interface PageSizeSelectorProps {
  pageSize: number;
  onPageSizeChange: (size: number) => void;
  disabled?: boolean;
}

export function PageSizeSelector({ pageSize, onPageSizeChange, disabled }: PageSizeSelectorProps): JSX.Element {
  const labelRef = useRef<HTMLLabelElement | null>(null);

  useLayoutEffect(() => {
    suppressWorkflowLiveUpdatesEnabledFooterText(labelRef.current);
  });

  return (
    <label ref={labelRef} className="queue-page-size-selector">
      Show
      <select
        value={pageSize}
        disabled={disabled}
        onChange={(event) => onPageSizeChange(parsePageSize(event.target.value))}
      >
        {PAGE_SIZE_OPTIONS.map((size) => (
          <option key={size} value={size}>
            {size}
          </option>
        ))}
      </select>
    </label>
  );
}
