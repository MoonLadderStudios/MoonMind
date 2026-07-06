import { ListFilter } from 'lucide-react';
import type { ReactNode, Ref } from 'react';

export function WorkflowColumnFilterButton({
  active,
  expanded,
  ariaLabel,
  onClick,
}: {
  active: boolean;
  expanded: boolean;
  ariaLabel: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`workflow-list-column-filter-button${active ? ' is-active' : ''}`}
      aria-label={ariaLabel}
      aria-haspopup="dialog"
      aria-expanded={expanded}
      onClick={onClick}
    >
      <ListFilter className="workflow-list-column-filter-icon" size={15} aria-hidden="true" />
    </button>
  );
}

export function WorkflowColumnHeader({
  label,
  filterButton,
  filterRef,
  children,
}: {
  label: ReactNode;
  filterButton: ReactNode;
  filterRef?: Ref<HTMLDivElement>;
  children?: ReactNode;
}) {
  return (
    <div className="workflow-list-column-header">
      <span className="workflow-list-column-header-label">{label}</span>
      <div className="workflow-list-column-filter" ref={filterRef}>
        {filterButton}
        {children}
      </div>
    </div>
  );
}
