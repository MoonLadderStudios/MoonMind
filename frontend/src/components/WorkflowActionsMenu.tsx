import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';

import { useActionConfirmation } from './DashboardActionDialog';
import type { WorkflowActionMenuItem, WorkflowActionMenuSection } from '../lib/workflowActions';

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
  /** Workflow named in confirmation dialogs. */
  subject?: string;
  /** Compact workflow identifier shown beside the subject in confirmation dialogs. */
  subjectId?: string | null;
  /** Notifies the parent when the menu opens or closes (used for lazy loading). */
  onOpenChange?: (open: boolean) => void;
};

const UNAVAILABLE_TOGGLE_KEY = 'unavailable-toggle';

/**
 * Accessible dropdown that renders a list of workflow action options. Shared by
 * the Workflow Detail "Workflow actions" surface and the Workflows table row
 * "Actions" menu so both present identical behavior and option semantics.
 *
 * Safe actions come first and take initial focus; destructive ones follow a
 * divider, Troubleshooting actions sit in their own group, and ineligible
 * actions collapse under "Unavailable (n)". Items that carry a confirmation
 * open a dialog and run only when it is confirmed.
 */
// The menu item holding native focus. Focus can reach an item (keyboard,
// assistive technology, scripts) before React commits activeKey for it.
function focusedItemKey(items: Map<string, HTMLElement>, target: Node | null): string | null {
  if (!target) return null;
  return [...items].find(([, item]) => item.contains(target))?.[0] ?? null;
}

export function WorkflowActionsMenu({
  items,
  triggerContent = 'Workflow actions',
  triggerAriaLabel,
  triggerClassName = 'secondary td-workflow-actions-trigger',
  menuAriaLabel = 'Workflow actions',
  emptyMessage = 'No workflow actions are currently available.',
  subject = 'Workflow',
  subjectId,
  onOpenChange,
}: WorkflowActionsMenuProps) {
  const [open, setOpen] = useState(false);
  const [unavailableExpanded, setUnavailableExpanded] = useState(false);
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const { requestConfirmation, confirmationDialog } = useActionConfirmation();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const itemRefs = useRef(new Map<string, HTMLButtonElement | HTMLAnchorElement>());
  const idPrefix = useId();

  const sections = useMemo(() => {
    const grouped: Record<WorkflowActionMenuSection, WorkflowActionMenuItem[]> = {
      primary: [],
      destructive: [],
      troubleshooting: [],
      unavailable: [],
    };
    for (const item of items) grouped[item.section ?? 'primary'].push(item);
    return grouped;
  }, [items]);
  const unavailableCount = sections.unavailable.length;

  // Arrow keys move through enabled actions, the Unavailable toggle and, once
  // expanded, the unavailable actions so their reasons can be read.
  const navKeys = useMemo(() => {
    const keys = [...sections.primary, ...sections.destructive, ...sections.troubleshooting]
      .filter((item) => !item.disabledReason)
      .map((item) => item.id);
    if (unavailableCount > 0) {
      keys.push(UNAVAILABLE_TOGGLE_KEY);
      if (unavailableExpanded) keys.push(...sections.unavailable.map((item) => item.id));
    }
    return keys;
  }, [sections, unavailableCount, unavailableExpanded]);
  // Opening the menu never focuses a destructive action. With no enabled safe
  // action, focus the Unavailable toggle, or else the menu itself.
  const initialKey =
    sections.primary.find((item) => !item.disabledReason)?.id ??
    (unavailableCount > 0 ? UNAVAILABLE_TOGGLE_KEY : null);

  const openMenu = () => {
    setUnavailableExpanded(false);
    setActiveKey(null);
    setOpen(true);
  };
  const closeMenu = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };
  const selectItem = (item: WorkflowActionMenuItem) => {
    if (item.disabledReason) return;
    closeMenu();
    if (item.confirmation) {
      requestConfirmation({
        ...item.confirmation,
        subject,
        compactId: subjectId,
        danger: item.danger,
        onConfirm: () => item.onSelect?.(),
      });
      return;
    }
    item.onSelect?.();
  };

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

  // Items can arrive or change while the menu is open (lazy capabilities,
  // refreshes); fall back to the safe initial target when focus has nowhere
  // valid to sit.
  useEffect(() => {
    if (!open) return;
    if (activeKey === null || !navKeys.includes(activeKey)) {
      const focused = focusedItemKey(itemRefs.current, document.activeElement);
      const next = focused !== null && navKeys.includes(focused) ? focused : initialKey;
      if (activeKey !== next) setActiveKey(next);
    }
  }, [activeKey, initialKey, navKeys, open]);

  useEffect(() => {
    if (!open) return;
    // An effect from an earlier render must not pull focus off the item the
    // operator already moved to; that item's focus handler updates activeKey.
    const focused = focusedItemKey(itemRefs.current, document.activeElement);
    if (focused !== null && navKeys.includes(focused)) return;
    const target = (activeKey && itemRefs.current.get(activeKey)) || menuRef.current;
    if (target && document.activeElement !== target) target.focus();
  }, [activeKey, open, navKeys]);

  const onTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === 'Enter' || event.key === ' ' || event.key === 'ArrowDown') {
      event.preventDefault();
      openMenu();
    }
  };
  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    // Native focus can move before React commits activeKey. Keyboard actions
    // belong to the event's item, not the previous render's roving tab stop.
    const focusedKey = focusedItemKey(itemRefs.current, event.target as Node) ?? activeKey;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeMenu();
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (navKeys.length === 0) return;
      const current = focusedKey ? navKeys.indexOf(focusedKey) : -1;
      const direction = event.key === 'ArrowDown' ? 1 : -1;
      const next =
        current < 0
          ? direction > 0 ? 0 : navKeys.length - 1
          : (current + direction + navKeys.length) % navKeys.length;
      const nextKey = navKeys[next] ?? null;
      setActiveKey(nextKey);
      // The next key may equal the previous render's key, so a state effect
      // alone cannot restore native focus after an intervening focus event.
      if (nextKey) itemRefs.current.get(nextKey)?.focus();
      return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (focusedKey) itemRefs.current.get(focusedKey)?.click();
    }
  };

  const setItemRef = (key: string) => (node: HTMLButtonElement | HTMLAnchorElement | null) => {
    if (node) itemRefs.current.set(key, node);
    else itemRefs.current.delete(key);
  };

  const renderItem = (item: WorkflowActionMenuItem) => {
    const noteId = item.disabledReason || item.description
      ? `${idPrefix}-${item.id}-note`
      : undefined;
    const commonProps = {
      role: 'menuitem',
      tabIndex: item.id === activeKey ? 0 : -1,
      'aria-label': item.label,
      'aria-disabled': item.disabledReason ? true : undefined,
      'aria-describedby': noteId,
      className: [
        'td-workflow-actions-item',
        item.danger ? 'td-workflow-actions-item-danger' : '',
        item.disabledReason ? 'td-workflow-actions-item-disabled' : '',
      ].filter(Boolean).join(' '),
      ref: setItemRef(item.id),
      onFocus: () => setActiveKey(item.id),
    };
    const note = item.disabledReason || item.description;
    const content = (
      <>
        <span>{item.label}</span>
        {note ? (
          <span
            id={noteId}
            className={
              item.disabledReason
                ? 'td-workflow-actions-disabled-reason'
                : 'td-workflow-actions-description'
            }
          >
            {note}
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
  };

  const separator = (key: string) => (
    <div key={key} role="separator" className="td-workflow-actions-separator" />
  );
  const body: ReactNode[] = [];
  const addBlock = (key: string, block: ReactNode) => {
    if (body.length > 0) body.push(separator(`${key}-separator`));
    body.push(block);
  };
  if (sections.primary.length > 0) {
    body.push(...sections.primary.map(renderItem));
  }
  if (sections.destructive.length > 0) {
    addBlock('destructive', sections.destructive.map(renderItem));
  }
  if (sections.troubleshooting.length > 0) {
    const labelId = `${idPrefix}-troubleshooting-label`;
    addBlock(
      'troubleshooting',
      <div
        key="troubleshooting"
        role="group"
        aria-labelledby={labelId}
        className="td-workflow-actions-group"
      >
        <p id={labelId} className="td-workflow-actions-group-label">Troubleshooting</p>
        {sections.troubleshooting.map(renderItem)}
      </div>,
    );
  }
  if (unavailableCount > 0) {
    const groupId = `${idPrefix}-unavailable`;
    addBlock(
      'unavailable',
      <div key="unavailable" className="td-workflow-actions-unavailable">
        <button
          type="button"
          role="menuitem"
          ref={setItemRef(UNAVAILABLE_TOGGLE_KEY)}
          tabIndex={activeKey === UNAVAILABLE_TOGGLE_KEY ? 0 : -1}
          className="td-workflow-actions-item td-workflow-actions-unavailable-toggle"
          aria-expanded={unavailableExpanded}
          aria-controls={unavailableExpanded ? groupId : undefined}
          onFocus={() => setActiveKey(UNAVAILABLE_TOGGLE_KEY)}
          onClick={() => {
            setUnavailableExpanded((expanded) => !expanded);
            setActiveKey(UNAVAILABLE_TOGGLE_KEY);
          }}
        >
          <span>{`Unavailable (${unavailableCount})`}</span>
        </button>
        {unavailableExpanded ? (
          <div
            id={groupId}
            role="group"
            aria-label="Unavailable actions"
            className="td-workflow-actions-group"
          >
            {sections.unavailable.map(renderItem)}
          </div>
        ) : null}
      </div>,
    );
  }

  return (
    <div
      className="td-workflow-actions-menu"
      ref={rootRef}
      onBlur={(event) => {
        // Window/tab blur has no related target in Firefox, but focus stays
        // on the menu item within this document. Only dismiss when it leaves
        // the menu, not when another browser page becomes active.
        const nextFocus = event.relatedTarget ?? event.currentTarget.ownerDocument.activeElement;
        if (open && !event.currentTarget.contains(nextFocus as Node | null)) {
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
          if (open) setOpen(false);
          else openMenu();
        }}
        onKeyDown={onTriggerKeyDown}
      >
        {triggerContent}
      </button>
      {open ? (
        <div
          ref={menuRef}
          className="td-workflow-actions-popover"
          role="menu"
          aria-label={menuAriaLabel}
          tabIndex={-1}
          onKeyDown={onMenuKeyDown}
        >
          {items.length === 0 ? (
            <p className="td-workflow-actions-empty">{emptyMessage}</p>
          ) : (
            body
          )}
        </div>
      ) : null}
      {confirmationDialog}
    </div>
  );
}

export type { WorkflowActionMenuItem };
