import { JSX } from 'react';

export const PAGE_SIZE_OPTIONS = [20, 25, 50, 100] as const;
export const DEFAULT_PAGE_SIZE = 50;

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
  return (
    <label className="queue-inline-filter">
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
