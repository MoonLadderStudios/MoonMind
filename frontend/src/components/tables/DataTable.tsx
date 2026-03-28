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
}

export function DataTable<T>({ data, columns, emptyMessage = 'No data found.' }: DataTableProps<T>) {
  return (
    <div className="overflow-x-auto w-full rounded shadow-sm border border-gray-200">
      <table className="min-w-full text-left text-sm whitespace-nowrap">
        <thead className="bg-gray-50 border-b border-gray-200">
          <tr>
            {columns.map((col) => (
              <th key={col.key as string} className="px-4 py-3 font-medium text-gray-900">
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200">
          {data && data.length > 0 ? (
            data.map((item, index) => (
              <tr key={index} className="hover:bg-gray-50 transition-colors">
                {columns.map((col) => (
                  <td key={col.key as string} className="px-4 py-3">
                    {col.render ? col.render(item) : String(item[col.key as keyof T] ?? '')}
                  </td>
                ))}
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={columns.length} className="px-4 py-8 text-center text-gray-500">
                {emptyMessage}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
