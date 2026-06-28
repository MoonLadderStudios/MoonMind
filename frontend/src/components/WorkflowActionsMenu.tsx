import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';

import type { WorkflowActionMenuItem } from '../lib/workflowActions';

export type WorkflowActionsMenuProps = {
  items: WorkflowActionMenuItem[];
  /** Content rendered inside the trigger button. Defaults to a "Workflow actions" label. */
  triggerContent?: ReactNode;
  /** Accessible name for the trigger button. Required when triggerContent is an icon. */
  triggerAriaLabel?: string;
  /** Class applied to the trigger button. */
  triggerClassName?: string;
  /** Accessible name for the open menu popover. */
  menuAriaLabel?: string;
  /** Message rendered when there are no items to show (e.g. loading or empty). */
  emptyMessage?: ReactNode;
  /** Notifies the parent when the menu opens or closes (used for lazy loading). */
  onOpenChange?: (open: boolean) => void;
};

/**
 * Accessible dropdown that renders a list of workflow action options. Shared by
 * the Workflow Detail "Workflow actions" surface and the Workflows table row
 * "Actions" menu so both present identical behavior and option semantics.
 */
export function WorkflowActionsMenu({
  items,
  triggerContent = 'Workflow actions',
  triggerAriaLabel,
  triggerClassName = 'secondary td-workflow-actions-trigger',
  menuAriaLabel = 'Workflow actions',
  emptyMessage = 'No workflow actions are currently available.',
  onOpenChange,
}: WorkflowActionsMenuProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | HTMLAnchorElement | null>>([]);
  const availableIndexes = useMemo(
    () => items
      .map((item, index) => (item.disabledReason ? -1 : index))
      .filter((index) => index >= 0),
    [items],
  );

  const closeMenu = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };
  const focusItem = (index: number) => {
    const nextIndex = items[index] ? index : availableIndexes[0] ?? 0;
    setActiveIndex(nextIndex);
  };
  const selectItem = (item: WorkflowActionMenuItem) => {
    if (item.disabledReason) return;
    item.onSelect?.();
    setOpen(false);
    triggerRef.current?.focus();
  };

  useEffect(() => {
    itemRefs.current = itemRefs.current.slice(0, items.length);
  }, [items]);

  // Keep the latest onOpenChange in a ref so the open/close notification effect
  // does not re-run (and re-fire) whenever the parent passes a new inline
  // callback identity on every render.
  const onOpenChangeRef = useRef(onOpenChange);
  useEffect(() => {
    onOpenChangeRef.current = onOpenChange;
  }, [onOpenChange]);

  useEffect(() => {
    onOpenChangeRef.current?.(open);
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const preferredIndex = availableIndexes.includes(activeIndex)
      ? activeIndex
      : availableIndexes[0] ?? 0;
    setActiveIndex(preferredIndex);
  }, [activeIndex, availableIndexes, open]);

  useEffect(() => {
    if (!open) return;
    const activeItem = itemRefs.current[activeIndex];
    if (activeItem && document.activeElement !== activeItem) {
      activeItem.focus();
    }
  }, [activeIndex, open]);

  const onTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      setOpen(true);
      focusItem(availableIndexes[0] ?? 0);
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setOpen(true);
      focusItem(availableIndexes[0] ?? 0);
    }
  };
  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      closeMenu();
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (availableIndexes.length === 0) return;
      const currentAvailableIndex = Math.max(0, availableIndexes.indexOf(activeIndex));
      const direction = event.key === 'ArrowDown' ? 1 : -1;
      const next =
        availableIndexes[
          (currentAvailableIndex + direction + availableIndexes.length) % availableIndexes.length
        ] ?? availableIndexes[0] ?? 0;
      focusItem(next);
      return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      itemRefs.current[activeIndex]?.click();
    }
  };

  return (
    <div
      className="td-workflow-actions-menu"
      ref={rootRef}
      onBlur={(event) => {
        if (open && !event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setOpen(false);
        }
      }}
    >
      <button
        type="button"
        ref={triggerRef}
        className={triggerClassName}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={triggerAriaLabel}
        onClick={() => {
          const nextOpen = !open;
          setOpen(nextOpen);
          if (nextOpen) {
            const firstAvailable = availableIndexes[0] ?? 0;
            setActiveIndex(firstAvailable);
          }
        }}
        onKeyDown={onTriggerKeyDown}
      >
        {triggerContent}
      </button>
      {open ? (
        <div
          className="td-workflow-actions-popover"
          role="menu"
          aria-label={menuAriaLabel}
          onKeyDown={onMenuKeyDown}
        >
          {items.length === 0 ? (
            <p className="td-workflow-actions-empty">{emptyMessage}</p>
          ) : (
            items.map((item, index) => {
              const disabledReasonId = item.disabledReason
                ? `workflow-action-${item.id}-disabled-reason`
                : undefined;
              const commonProps = {
                role: 'menuitem',
                tabIndex: index === activeIndex ? 0 : -1,
                'aria-label': item.label,
                'aria-disabled': item.disabledReason ? true : undefined,
                'aria-describedby': disabledReasonId,
                className: [
                  'td-workflow-actions-item',
                  item.danger ? 'td-workflow-actions-item-danger' : '',
                  item.disabledReason ? 'td-workflow-actions-item-disabled' : '',
                ].filter(Boolean).join(' '),
                ref: (node: HTMLButtonElement | HTMLAnchorElement | null) => {
                  itemRefs.current[index] = node;
                },
                onFocus: () => setActiveIndex(index),
              };
              const content = (
                <>
                  <span>{item.label}</span>
                  {item.disabledReason ? (
                    <span
                      id={disabledReasonId}
                      className="td-workflow-actions-disabled-reason"
                    >
                      {item.disabledReason}
                    </span>
                  ) : null}
                </>
              );
              if (item.href && !item.disabledReason) {
                return (
                  <a
                    key={item.id}
                    {...commonProps}
                    href={item.href}
                    onClick={() => {
                      item.onSelect?.();
                      setOpen(false);
                    }}
                  >
                    {content}
                  </a>
                );
              }
              return (
                <button
                  key={item.id}
                  {...commonProps}
                  type="button"
                  onClick={() => selectItem(item)}
                >
                  {content}
                </button>
              );
            })
          )}
        </div>
      ) : null}
    </div>
  );
}

export type { WorkflowActionMenuItem };
