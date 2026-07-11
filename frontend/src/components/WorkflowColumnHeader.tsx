import { ListFilter } from 'lucide-react';
import type { ReactNode, Ref } from 'react';

export function WorkflowColumnFilterButton({
  active,
  expanded,
  ariaLabel,
  onClick,
  buttonRef,
}: {
  active: boolean;
  expanded: boolean;
  ariaLabel: string;
  onClick: () => void;
  buttonRef?: Ref<HTMLButtonElement>;
}) {
  return (
    <button
      ref={buttonRef}
      type="button"
      className={`workflow-list-column-filter-button${active ? ' is-active' : ''}`}
      aria-label={ariaLabel}
      aria-haspopup="dialog"
      aria-expanded={expanded}
      onClick={onClick}
    >
      <ListFilter className="workflow-list-column-filter-icon" aria-hidden="true" />
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
      {label}
      <div className="workflow-list-column-filter" ref={filterRef}>
        {filterButton}
        {children}
      </div>
    </div>
  );
}
