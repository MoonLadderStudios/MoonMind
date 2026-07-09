import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { DataTable, type Column } from './DataTable';

interface Row {
  id: string;
  name: string;
  count: number;
}

const rows: Row[] = [
  { id: 'a', name: 'Beta', count: 2 },
  { id: 'b', name: 'Alpha', count: 30 },
  { id: 'c', name: 'Gamma', count: 1 },
];

const columns: Column<Row>[] = [
  { key: 'name', header: 'Name', sortable: true },
  { key: 'count', header: 'Count', align: 'right', sortable: true },
];

describe('DataTable (MM-959)', () => {
  it('renders the empty message when there are no rows', () => {
    render(<DataTable data={[]} columns={columns} getRowKey={(r) => r.id} emptyMessage="Nothing here" />);
    expect(screen.getByText('Nothing here')).toBeTruthy();
  });

  it('renders an accessible loading state', () => {
    render(
      <DataTable
        data={[]}
        columns={columns}
        getRowKey={(r) => r.id}
        isLoading
        loadingMessage="Loading rows..."
      />,
    );
    const cell = screen.getByText('Loading rows...');
    expect(cell).toBeTruthy();
    expect(cell.getAttribute('aria-busy')).toBe('true');
  });

  it('renders an error state with an alert role', () => {
    render(
      <DataTable
        data={[]}
        columns={columns}
        getRowKey={(r) => r.id}
        isError
        errorMessage="Boom"
      />,
    );
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain('Boom');
  });

  it('sorts rows when a sortable header is activated', () => {
    render(<DataTable data={rows} columns={columns} getRowKey={(r) => r.id} />);

    // Ascending by name on first click.
    fireEvent.click(screen.getByRole('button', { name: /Sort by Name/ }));
    let bodyRows = screen.getAllByRole('row').slice(1); // drop header row
    expect(within(bodyRows[0]!).getByText('Alpha')).toBeTruthy();
    expect(within(bodyRows[2]!).getByText('Gamma')).toBeTruthy();

    // aria-sort reflects ascending direction.
    const nameHeader = screen.getByRole('columnheader', { name: /Name/ });
    expect(nameHeader.getAttribute('aria-sort')).toBe('ascending');

    // Descending by name on second click.
    fireEvent.click(screen.getByRole('button', { name: /Sort by Name/ }));
    bodyRows = screen.getAllByRole('row').slice(1);
    expect(within(bodyRows[0]!).getByText('Gamma')).toBeTruthy();
  });

  it('marks header, data, and action cells with stable column keys', () => {
    render(
      <DataTable
        data={rows.slice(0, 1)}
        columns={columns}
        getRowKey={(r) => r.id}
        rowActions={(r) => <button type="button">Edit {r.name}</button>}
      />,
    );

    expect(screen.getByRole('columnheader', { name: 'Name' }).getAttribute('data-column-key')).toBe('name');
    expect(screen.getByRole('cell', { name: 'Beta' }).getAttribute('data-column-key')).toBe('name');
    expect(screen.getByRole('columnheader', { name: 'Actions' }).getAttribute('data-column-key')).toBe('actions');
    expect(screen.getByRole('cell', { name: 'Edit Beta' }).getAttribute('data-column-key')).toBe('actions');
  });

  it('renders a row actions column and responsive card fallback', () => {
    render(
      <DataTable
        data={rows}
        columns={columns}
        getRowKey={(r) => r.id}
        responsive
        rowActions={(r) => <button type="button">Edit {r.name}</button>}
      />,
    );
    expect(screen.getByRole('columnheader', { name: 'Actions' })).toBeTruthy();
    // The action renders in both the table and the card fallback.
    expect(screen.getAllByRole('button', { name: 'Edit Beta' }).length).toBeGreaterThanOrEqual(1);
    // Card fallback present in DOM (CSS toggles visibility by viewport width).
    const cards = document.querySelector('.data-table-cards');
    expect(cards).toBeTruthy();
    expect(document.querySelector('[data-responsive="on"]')).toBeTruthy();
    // The cards replace the table at narrow widths, so they must remain exposed
    // to assistive tech rather than being aria-hidden.
    expect(cards?.getAttribute('aria-hidden')).toBeNull();
  });
});
