import React from 'react';

export interface Column<T> {
  key: keyof T | string;
  header: string;
  render?: (item: T) => React.ReactNode;
}

export interface DataTableProps<T> {
  data?: T[];
  columns: Column<T>[];
  emptyMessage?: string;
  getRowKey: (item: T) => React.Key;
  ariaLabel?: string;
}

export function DataTable<T>({
  data,
  columns,
  emptyMessage = 'No data found.',
  getRowKey,
  ariaLabel = 'Data table',
}: DataTableProps<T>) {
  return (
    <div className="data-table-slab" data-layout="table">
      <table className="data-table" aria-label={ariaLabel}>
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col.key as string}>
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data && data.length > 0 ? (
            data.map((item) => (
              <tr key={getRowKey(item)}>
                {columns.map((col) => (
                  <td key={col.key as string}>
                    {col.render ? col.render(item) : String(item[col.key as keyof T] ?? '')}
                  </td>
                ))}
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={columns.length} className="data-table-empty">
                {emptyMessage}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
