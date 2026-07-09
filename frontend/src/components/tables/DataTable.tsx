import React, { useMemo, useState } from 'react';

export type ColumnAlign = 'left' | 'center' | 'right';

export interface Column<T> {
  key: keyof T | string;
  header: React.ReactNode;
  render?: (item: T) => React.ReactNode;
  /** Cell + header text alignment. */
  align?: ColumnAlign;
  /** Fixed/min column width (CSS length). */
  width?: string;
  /** Enable click-to-sort on this column's header. */
  sortable?: boolean;
  /**
   * Value used for built-in sorting. Defaults to `item[key]` when omitted.
   * Provide this when the rendered cell differs from the sortable value.
   */
  sortValue?: (item: T) => string | number | boolean | null | undefined;
  /**
   * Accessible label for the sort control when the visible header is an icon
   * or otherwise not descriptive. Defaults to `header`.
   */
  sortLabel?: string;
}

export type DataTableDensity = 'comfortable' | 'compact';

export type SortDirection = 'asc' | 'desc';

export interface SortState {
  key: string;
  direction: SortDirection;
}

export interface DataTableProps<T> {
  data?: T[];
  columns: Column<T>[];
  emptyMessage?: string;
  getRowKey: (item: T) => React.Key;
  ariaLabel?: string;
  /** Loading state replaces the body with an accessible loading row. */
  isLoading?: boolean;
  loadingMessage?: React.ReactNode;
  /** Error state replaces the body with an accessible error row. */
  isError?: boolean;
  errorMessage?: React.ReactNode;
  /** Sticky header (default true). */
  sticky?: boolean;
  /** Row density (default comfortable). */
  density?: DataTableDensity;
  /** Render a trailing actions cell for each row. */
  rowActions?: (item: T) => React.ReactNode;
  rowActionsHeader?: string;
  /** Render a responsive stacked-card fallback alongside the table. */
  responsive?: boolean;
  /** Initial sort applied when a sortable column is present. */
  defaultSort?: SortState;
}

function compareValues(
  a: string | number | boolean | null | undefined,
  b: string | number | boolean | null | undefined,
): number {
  if (a === b) return 0;
  if (a === null || a === undefined) return -1;
  if (b === null || b === undefined) return 1;
  if (typeof a === 'number' && typeof b === 'number') {
    return a - b;
  }
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

function defaultSortValue<T>(item: T, key: string): string | number | undefined {
  const value = (item as Record<string, unknown>)[key];
  if (typeof value === 'number' || typeof value === 'string') {
    return value;
  }
  return value === null || value === undefined ? undefined : String(value);
}

function nextDirection(current: SortDirection): SortDirection {
  return current === 'asc' ? 'desc' : 'asc';
}

export function DataTable<T>({
  data,
  columns,
  emptyMessage = 'No data found.',
  getRowKey,
  ariaLabel = 'Data table',
  isLoading = false,
  loadingMessage = 'Loading...',
  isError = false,
  errorMessage = 'Failed to load data.',
  sticky = true,
  density = 'comfortable',
  rowActions,
  rowActionsHeader = 'Actions',
  responsive = false,
  defaultSort,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<SortState | null>(defaultSort ?? null);

  const columnByKey = useMemo(() => {
    const map = new Map<string, Column<T>>();
    for (const column of columns) {
      map.set(column.key as string, column);
    }
    return map;
  }, [columns]);

  const sortedData = useMemo(() => {
    if (!data || !sort) return data ?? [];
    const column = columnByKey.get(sort.key);
    if (!column || !column.sortable) return data;
    const getValue = column.sortValue
      ? column.sortValue
      : (item: T) => defaultSortValue(item, sort.key);
    const factor = sort.direction === 'asc' ? 1 : -1;
    return [...data].sort((a, b) => compareValues(getValue(a), getValue(b)) * factor);
  }, [data, sort, columnByKey]);

  const handleSort = (key: string) => {
    setSort((current) => {
      if (current && current.key === key) {
        return { key, direction: nextDirection(current.direction) };
      }
      return { key, direction: 'asc' };
    });
  };

  const totalColumnCount = columns.length + (rowActions ? 1 : 0);
  const hasRows = sortedData.length > 0;

  const renderHeaderCell = (column: Column<T>) => {
    const key = column.key as string;
    const isSorted = sort?.key === key;
    const ariaSort: React.AriaAttributes['aria-sort'] = column.sortable
      ? isSorted
        ? sort?.direction === 'asc'
          ? 'ascending'
          : 'descending'
        : 'none'
      : undefined;
    const style: React.CSSProperties = {};
    if (column.width) style.width = column.width;
    return (
      <th
        key={key}
        scope="col"
        aria-sort={ariaSort}
        data-align={column.align ?? 'left'}
        data-column-key={key}
        style={style}
      >
        {column.sortable ? (
          <button
            type="button"
            className="data-table__sort"
            onClick={() => handleSort(key)}
            aria-label={`Sort by ${column.sortLabel ?? String(column.header)}${
              isSorted
                ? sort?.direction === 'asc'
                  ? ' (ascending)'
                  : ' (descending)'
                : ''
            }`}
          >
            <span>{column.header}</span>
            <span aria-hidden="true" className="data-table__sort-indicator">
              {isSorted ? (sort?.direction === 'asc' ? '▲' : '▼') : '↕'}
            </span>
          </button>
        ) : (
          column.header
        )}
      </th>
    );
  };

  const renderBody = () => {
    if (isLoading) {
      return (
        <tr>
          <td colSpan={totalColumnCount} className="data-table-empty" aria-busy="true">
            {loadingMessage}
          </td>
        </tr>
      );
    }
    if (isError) {
      return (
        <tr>
          <td colSpan={totalColumnCount} className="data-table-empty data-table-error" role="alert">
            {errorMessage}
          </td>
        </tr>
      );
    }
    if (!hasRows) {
      return (
        <tr>
          <td colSpan={totalColumnCount} className="data-table-empty">
            {emptyMessage}
          </td>
        </tr>
      );
    }
    return sortedData.map((item) => (
      <tr key={getRowKey(item)}>
        {columns.map((col) => (
          <td
            key={col.key as string}
            data-align={col.align ?? 'left'}
            data-column-key={col.key as string}
          >
            {col.render ? col.render(item) : String(item[col.key as keyof T] ?? '')}
          </td>
        ))}
        {rowActions ? (
          <td className="data-table__row-actions" data-align="right" data-column-key="actions">
            {rowActions(item)}
          </td>
        ) : null}
      </tr>
    ));
  };

  const renderCards = () => {
    if (isLoading || isError || !hasRows) return null;
    // Intentionally not aria-hidden: when the responsive breakpoint hides the
    // table (`display: none`), the table is removed from the accessibility tree
    // and these cards become the only representation of the rows for assistive
    // tech. CSS `display: none` mutually excludes the table and cards across the
    // breakpoint, so exactly one is exposed to AT at any width.
    return (
      <ul className="data-table-cards">
        {sortedData.map((item) => (
          <li key={getRowKey(item)} className="data-table-card">
            {columns.map((col) => (
              <div key={col.key as string} className="data-table-card__row">
                <span className="data-table-card__label">{col.header}</span>
                <span className="data-table-card__value">
                  {col.render ? col.render(item) : String(item[col.key as keyof T] ?? '')}
                </span>
              </div>
            ))}
            {rowActions ? (
              <div className="data-table-card__actions">{rowActions(item)}</div>
            ) : null}
          </li>
        ))}
      </ul>
    );
  };

  return (
    <div
      className="data-table-slab"
      data-layout="table"
      data-density={density}
      data-sticky={sticky ? 'on' : 'off'}
      data-responsive={responsive ? 'on' : 'off'}
    >
      <table className="data-table" aria-label={ariaLabel}>
        <thead>
          <tr>
            {columns.map(renderHeaderCell)}
            {rowActions ? (
              <th scope="col" data-align="right" data-column-key="actions">
                {rowActionsHeader}
              </th>
            ) : null}
          </tr>
        </thead>
        <tbody>{renderBody()}</tbody>
      </table>
      {responsive ? renderCards() : null}
    </div>
  );
}
