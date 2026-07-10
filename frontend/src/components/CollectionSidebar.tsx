import { useMemo, useState, type ReactNode } from 'react';

export type CollectionSidebarRow = {
  id: string;
  href: string;
  primaryText: string;
  metadata?: ReactNode;
};

export function CollectionSidebar({
  landmarkLabel,
  tableLabel,
  header,
  filterLabel,
  filterPlaceholder,
  rows,
  activeId,
  pinnedRow = null,
  isLoading = false,
  error = null,
  onRetry,
  loadingCopy,
  emptyCopy,
  filteredEmptyCopy,
  errorCopy,
  currentRowCopy,
  rowFocusAttribute,
}: {
  landmarkLabel: string;
  tableLabel: string;
  header: string;
  filterLabel: string;
  filterPlaceholder: string;
  rows: CollectionSidebarRow[];
  activeId: string | null;
  pinnedRow?: CollectionSidebarRow | null;
  isLoading?: boolean;
  error?: unknown;
  onRetry?: () => void;
  loadingCopy: string;
  emptyCopy: string;
  filteredEmptyCopy: string;
  errorCopy: string;
  currentRowCopy: string;
  rowFocusAttribute?: string;
}) {
  const [filter, setFilter] = useState('');
  const normalizedFilter = filter.trim().toLocaleLowerCase();
  const filteredRows = useMemo(() => rows.filter((row) => (
    !normalizedFilter
    || row.primaryText.toLocaleLowerCase().includes(normalizedFilter)
    || row.id.toLocaleLowerCase().includes(normalizedFilter)
  )), [normalizedFilter, rows]);
  const activeInRows = rows.some((row) => row.id === activeId);
  const visiblePinnedRow = pinnedRow && !activeInRows ? pinnedRow : null;

  const renderRow = (row: CollectionSidebarRow, pinned = false) => {
    const focusProps = rowFocusAttribute ? { [rowFocusAttribute]: row.id } : {};
    return (
      <div key={`${pinned ? 'pinned-' : ''}${row.id}`} role="row" className={`workflow-workspace-sidebar-row-frame${pinned ? ' workflow-workspace-sidebar-row-frame-pinned' : ''}`}>
        <div role="cell" className="workflow-workspace-sidebar-cell">
          <a
            href={row.href}
            className={`workflow-workspace-sidebar-row${pinned ? ' workflow-workspace-sidebar-row-pinned' : ''}`}
            aria-current={row.id === activeId ? 'page' : undefined}
            data-active={row.id === activeId ? 'true' : 'false'}
            data-pinned={pinned ? 'true' : 'false'}
            {...focusProps}
          >
            <span className="workflow-workspace-sidebar-row-main">
              {pinned ? <span className="workflow-workspace-sidebar-kicker">{currentRowCopy}</span> : null}
              <span className="workflow-workspace-sidebar-title">{row.primaryText}</span>
            </span>
            {row.metadata ? <span className="collection-sidebar-row-meta">{row.metadata}</span> : null}
          </a>
        </div>
      </div>
    );
  };

  return (
    <aside className="collection-sidebar workflow-workspace-sidebar" aria-label={landmarkLabel}>
      <div role="table" aria-label={tableLabel} className="workflow-workspace-sidebar-table">
        <div role="rowgroup" className="workflow-workspace-sidebar-header">
          <div role="row" className="workflow-workspace-sidebar-header-row">
            <div role="columnheader" className="workflow-workspace-sidebar-header-cell">
              <span className="workflow-workspace-sidebar-header-title">{header}</span>
              <label className="collection-sidebar-filter">
                <span className="sr-only">{filterLabel}</span>
                <input type="search" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder={filterPlaceholder} aria-label={filterLabel} />
              </label>
            </div>
          </div>
        </div>
        {isLoading ? <SidebarState>{loadingCopy}</SidebarState> : null}
        {error ? (
          <SidebarState role="status">
            <p>{errorCopy}</p>
            {onRetry ? <button type="button" className="secondary" onClick={onRetry}>Retry</button> : null}
          </SidebarState>
        ) : null}
        {!isLoading && !error && visiblePinnedRow ? (
          <div role="rowgroup" className="workflow-workspace-sidebar-list workflow-workspace-sidebar-pinned-list" aria-label={currentRowCopy}>
            {renderRow(visiblePinnedRow, true)}
          </div>
        ) : null}
        {!isLoading && !error && filteredRows.length === 0 ? <SidebarState>{normalizedFilter ? filteredEmptyCopy : emptyCopy}</SidebarState> : null}
        {!isLoading && !error && filteredRows.length > 0 ? (
          <div role="rowgroup" className="workflow-workspace-sidebar-list" aria-label={`${header} navigation list`}>
            {filteredRows.map((row) => renderRow(row))}
          </div>
        ) : null}
      </div>
    </aside>
  );
}

function SidebarState({ children, role }: { children: ReactNode; role?: 'status' }) {
  return (
    <div role="rowgroup" className="workflow-workspace-sidebar-state-group">
      <div role="row" className="workflow-workspace-sidebar-row-frame">
        <div role="cell" className="workflow-workspace-sidebar-cell">
          <div className="workflow-workspace-sidebar-state" role={role}>{children}</div>
        </div>
      </div>
    </div>
  );
}
