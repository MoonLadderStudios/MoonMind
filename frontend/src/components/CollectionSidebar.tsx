import { useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from 'react';

import { WorkflowColumnFilterButton, WorkflowColumnHeader } from './WorkflowColumnHeader';

/**
 * Entity-specific copy consumed by the shared sidebar filter header. Every
 * collection sidebar (Workflows, Skills, ...) provides its own nouns while the
 * open/close, focus, reset, and apply behavior stays shared so it cannot drift.
 */
export type CollectionSidebarFilterCopy = {
  /** Column header text, e.g. "Workflow". */
  columnHeader: string;
  /** Popover dialog accessible name, e.g. "Workflow sidebar filter". */
  dialogLabel: string;
  /** Popover title text, e.g. "Workflow filter". */
  dialogTitle: string;
  /** Search field label, e.g. "Workflow". */
  fieldLabel: string;
  /** Search field placeholder, e.g. "Filter workflows". */
  placeholder: string;
  /** Search input accessible name, e.g. "Workflow sidebar filter value". */
  inputLabel: string;
  /** Trigger accessible name with no filter applied. */
  triggerIdleLabel: string;
  /** Trigger accessible name announcing the current filter value. */
  triggerActiveLabel: (value: string) => string;
  /** Reset action accessible name, e.g. "Reset workflow sidebar filter". */
  resetLabel: string;
  /** Apply action accessible name, e.g. "Apply workflow sidebar filter". */
  applyLabel: string;
};

export function CollectionColumnFilter({
  copy,
  filterText,
  setFilterText,
  labelClassName,
}: {
  copy: CollectionSidebarFilterCopy;
  filterText: string;
  setFilterText: (value: string) => void;
  labelClassName?: string;
}) {
  const [open, setOpen] = useState(false);
  const filterRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const active = filterText.trim().length > 0;

  const closeAndRestoreFocus = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && filterRef.current?.contains(target)) {
        return;
      }
      setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open]);

  return (
    <WorkflowColumnHeader
      label={(
        <span className={labelClassName ?? 'workflow-list-column-header-label'}>
          {copy.columnHeader}
        </span>
      )}
      filterButton={(
        <WorkflowColumnFilterButton
          active={active}
          expanded={open}
          ariaLabel={active ? copy.triggerActiveLabel(filterText) : copy.triggerIdleLabel}
          onClick={() => setOpen((value) => !value)}
          buttonRef={triggerRef}
        />
      )}
      filterRef={filterRef}
    >
      {open ? (
        <div
          className="workflow-workspace-sidebar-filter-popover workflow-list-column-filter-popover"
          role="dialog"
          aria-label={copy.dialogLabel}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.stopPropagation();
              closeAndRestoreFocus();
            }
          }}
        >
          <div className="workflow-list-column-filter-title">{copy.dialogTitle}</div>
          <label className="workflow-list-filter-control">
            <span>{copy.fieldLabel}</span>
            <input
              type="search"
              value={filterText}
              onChange={(event) => setFilterText(event.target.value)}
              placeholder={copy.placeholder}
              aria-label={copy.inputLabel}
              autoFocus
            />
          </label>
          <div className="workflow-list-filter-actions">
            <button
              type="button"
              className="secondary"
              onClick={() => setFilterText('')}
              disabled={!active}
              aria-label={copy.resetLabel}
            >
              Reset
            </button>
            <button
              type="button"
              onClick={closeAndRestoreFocus}
              aria-label={copy.applyLabel}
            >
              Apply
            </button>
          </div>
        </div>
      ) : null}
    </WorkflowColumnHeader>
  );
}

export function CollectionSidebarFilterHeader({
  copy,
  filterText,
  setFilterText,
}: {
  copy: CollectionSidebarFilterCopy;
  filterText: string;
  setFilterText: (value: string) => void;
}) {
  return (
    <div role="rowgroup" className="workflow-workspace-sidebar-header">
      <div role="row" className="workflow-workspace-sidebar-header-row">
        <div role="columnheader" className="workflow-workspace-sidebar-header-cell">
          <CollectionColumnFilter
            copy={copy}
            filterText={filterText}
            setFilterText={setFilterText}
            labelClassName="workflow-workspace-sidebar-header-title workflow-list-column-header-label"
          />
        </div>
      </div>
    </div>
  );
}

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
