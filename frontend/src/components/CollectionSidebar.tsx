import { useMemo, useState, type ReactElement, type ReactNode } from 'react';

export type CollectionSidebarRow = {
  id: string;
  href: string;
  primaryText: string;
  metadata?: ReactNode;
};

export type CollectionSidebarLinkRenderer = (
  row: CollectionSidebarRow,
  props: {
    className: string;
    children: ReactNode;
    'aria-current': 'page' | undefined;
    'data-active': 'true' | 'false';
    'data-pinned': 'true' | 'false';
  },
) => ReactElement;

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
  renderLink,
  headerContent,
  filterValue,
  onFilterChange,
  externalFiltering = false,
  className,
}: {
  landmarkLabel: string;
  tableLabel: string;
  header: string;
  filterLabel: string;
  filterPlaceholder: string;
  rows: CollectionSidebarRow[];
  activeId: string | null;
  pinnedRow?: CollectionSidebarRow | null | undefined;
  isLoading?: boolean;
  error?: unknown;
  onRetry?: (() => void) | undefined;
  loadingCopy: string;
  emptyCopy: string;
  filteredEmptyCopy: string;
  errorCopy: string;
  currentRowCopy: string;
  rowFocusAttribute?: string;
  renderLink?: CollectionSidebarLinkRenderer;
  headerContent?: ReactNode;
  filterValue?: string;
  onFilterChange?: (value: string) => void;
  externalFiltering?: boolean;
  className?: string;
}) {
  const [internalFilter, setInternalFilter] = useState('');
  const isControlled = filterValue !== undefined;
  const filter = isControlled ? filterValue : internalFilter;
  const setFilter = (value: string) => {
    if (!isControlled) {
      setInternalFilter(value);
    }
    onFilterChange?.(value);
  };
  const normalizedFilter = filter.trim().toLocaleLowerCase();
  const filteredRows = useMemo(() => externalFiltering ? rows : rows.filter((row) => (
    !normalizedFilter
    || row.primaryText.toLocaleLowerCase().includes(normalizedFilter)
    || row.id.toLocaleLowerCase().includes(normalizedFilter)
  )), [externalFiltering, normalizedFilter, rows]);
  const activeInFilteredRows = filteredRows.some((row) => row.id === activeId);
  const visiblePinnedRow = pinnedRow && !activeInFilteredRows ? pinnedRow : null;

  const renderRow = (row: CollectionSidebarRow, pinned = false) => {
    const focusProps = rowFocusAttribute ? { [rowFocusAttribute]: row.id } : {};
    const linkProps = {
      className: `workflow-workspace-sidebar-row${pinned ? ' workflow-workspace-sidebar-row-pinned' : ''}`,
      children: (
        <>
          <span className="workflow-workspace-sidebar-row-main">
            {pinned ? <span className="workflow-workspace-sidebar-kicker">{currentRowCopy}</span> : null}
            <span className="workflow-workspace-sidebar-title">{row.primaryText}</span>
          </span>
          {row.metadata ? <span className="collection-sidebar-row-meta">{row.metadata}</span> : null}
        </>
      ),
      'aria-current': row.id === activeId ? 'page' as const : undefined,
      'data-active': row.id === activeId ? 'true' as const : 'false' as const,
      'data-pinned': pinned ? 'true' as const : 'false' as const,
      ...focusProps,
    };
    return (
      <div key={`${pinned ? 'pinned-' : ''}${row.id}`} role="row" className={`workflow-workspace-sidebar-row-frame${pinned ? ' workflow-workspace-sidebar-row-frame-pinned' : ''}`}>
        <div role="cell" className="workflow-workspace-sidebar-cell">
          {renderLink ? renderLink(row, linkProps) : <a href={row.href} {...linkProps} />}
        </div>
      </div>
    );
  };

  return (
    <aside className={`collection-sidebar workflow-workspace-sidebar${className ? ` ${className}` : ''}`} aria-label={landmarkLabel}>
      <div role="table" aria-label={tableLabel} className="workflow-workspace-sidebar-table">
        {headerContent ?? <div role="rowgroup" className="workflow-workspace-sidebar-header">
          <div role="row" className="workflow-workspace-sidebar-header-row">
            <div role="columnheader" className="workflow-workspace-sidebar-header-cell">
              <span className="workflow-workspace-sidebar-header-title">{header}</span>
              <label className="collection-sidebar-filter">
                <span className="sr-only">{filterLabel}</span>
                <input type="search" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder={filterPlaceholder} aria-label={filterLabel} />
              </label>
            </div>
          </div>
        </div>}
        {isLoading ? <SidebarState>{loadingCopy}</SidebarState> : null}
        {error ? (
          <SidebarState role="status">
            <p>{errorCopy}</p>
            {onRetry ? <button type="button" className="secondary" onClick={onRetry}>Retry</button> : null}
          </SidebarState>
        ) : null}
        {!isLoading && visiblePinnedRow ? (
          <div role="rowgroup" className="workflow-workspace-sidebar-list workflow-workspace-sidebar-pinned-list" aria-label={currentRowCopy}>
            {renderRow(visiblePinnedRow, true)}
          </div>
        ) : null}
        {!isLoading && !error && filteredRows.length === 0 ? <SidebarState>{normalizedFilter ? filteredEmptyCopy : emptyCopy}</SidebarState> : null}
        {!isLoading && filteredRows.length > 0 ? (
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
