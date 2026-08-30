import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Archive, Bot, ChevronDown, Moon, Rows3, Settings, ShieldCheck, Sparkles, Wrench } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';

import {
  DASHBOARD_DESTINATION_GROUPS,
  destinationGroupForDestination,
  destinationForPath,
  destinationState,
  exposedSystemDestinations,
  type DashboardDestination,
  type DashboardDestinationState,
  type DashboardIconKey,
  type DashboardUiInfo,
} from '../lib/dashboardRoutes';

const ICONS: Partial<Record<DashboardIconKey, typeof Settings>> = {
  archive: Archive,
  bot: Bot,
  manifest: Rows3,
  moon: Moon,
  settings: Settings,
  'shield-check': ShieldCheck,
  sparkles: Sparkles,
  wrench: Wrench,
};

const SECTION_LABELS: Record<string, string> = {
  recurring: 'Workflow resources',
  manifests: 'Data & evidence',
  'omnigent-agents': 'Omnigent',
  remediation: 'Operations',
};

function DestinationLink({ destination, state, onSelect, menuItem = true }: {
  destination: DashboardDestination;
  state: DashboardDestinationState;
  onSelect: () => void;
  menuItem?: boolean;
}) {
  const Icon = ICONS[destination.iconKey] ?? Settings;
  if (state === 'unavailable') {
    return (
      <span
        role={menuItem ? 'menuitem' : undefined}
        aria-disabled="true"
        className="dashboard-system-destination-unavailable"
      >
        <Icon size={16} className="route-nav-icon" aria-hidden="true" />
        {destination.label}
        <span className="sr-only"> unavailable</span>
      </span>
    );
  }
  return (
    <NavLink
      to={destination.canonicalPath}
      role={menuItem ? 'menuitem' : undefined}
      className={({ isActive }) => (isActive ? 'active' : undefined)}
      onClick={onSelect}
    >
      <Icon size={16} className="route-nav-icon" aria-hidden="true" />
      {destination.label}
    </NavLink>
  );
}

function DestinationSection({
  destinations,
  label,
  uiInfo,
  onSelect,
  menuItems,
}: {
  destinations: DashboardDestination[];
  label?: string | undefined;
  uiInfo: DashboardUiInfo | null;
  onSelect: () => void;
  menuItems: boolean;
}) {
  return (
    <div className="dashboard-system-menu-section">
      {label ? <div className="dashboard-system-menu-label">{label}</div> : null}
      {destinations.map((destination) => (
        <DestinationLink
          key={destination.key}
          destination={destination}
          state={destinationState(destination, uiInfo)}
          onSelect={onSelect}
          menuItem={menuItems}
        />
      ))}
    </div>
  );
}

function DestinationSections({ destinations, uiInfo, onSelect, menuItems = true }: {
  destinations: DashboardDestination[];
  uiInfo: DashboardUiInfo | null;
  onSelect: () => void;
  menuItems?: boolean;
}) {
  const destinationByKey = new Map(destinations.map((destination) => [destination.key, destination]));
  const renderedGroups = new Set<string>();
  return destinations.flatMap((destination) => {
    const group = destinationGroupForDestination(destination);
    if (group) {
      if (renderedGroups.has(group.key)) return [];
      renderedGroups.add(group.key);
      const children = group.destinationKeys.flatMap((key) => {
        const child = destinationByKey.get(key);
        return child ? [child] : [];
      });
      if (children.length === 0) return [];
      return [(
        <DestinationSection
          key={group.key}
          destinations={children}
          label={group.label}
          uiInfo={uiInfo}
          onSelect={onSelect}
          menuItems={menuItems}
        />
      )];
    }
    const sectionLabel = SECTION_LABELS[destination.key];
    return [(
      <DestinationSection
        key={destination.key}
        destinations={[destination]}
        label={sectionLabel}
        uiInfo={uiInfo}
        onSelect={onSelect}
        menuItems={menuItems}
      />
    )];
  });
}

export function DashboardSystemMenu({ uiInfo, mobileDrawerOpen }: {
  uiInfo: DashboardUiInfo | null;
  mobileDrawerOpen: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const location = useLocation();
  const destinations = exposedSystemDestinations(uiInfo);
  const activeDestination = destinationForPath(location.pathname);
  const activeDestinationGroup = destinationGroupForDestination(activeDestination) ?? (
    location.pathname.replace(/\/$/, '') === '/settings'
      ? DASHBOARD_DESTINATION_GROUPS.find(({ key }) => key === 'configuration') ?? null
      : null
  );
  const active = Boolean(
    activeDestinationGroup || (activeDestination && activeDestination.navigationGroup !== 'primary'),
  );
  const triggerLabel = activeDestinationGroup?.triggerLabel ?? (
    active && activeDestination ? activeDestination.label : 'System'
  );
  const triggerIconKey = activeDestinationGroup?.triggerIconKey ?? activeDestination?.iconKey;
  const TriggerIcon = active && triggerIconKey ? (ICONS[triggerIconKey] ?? Settings) : Settings;

  useEffect(() => setOpen(false), [location.pathname, location.search]);

  useEffect(() => {
    if (!open) return undefined;
    const closeOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', closeOutside);
    return () => document.removeEventListener('pointerdown', closeOutside);
  }, [open]);

  if (destinations.length === 0) return null;

  const items = () => Array.from(
    rootRef.current?.querySelectorAll<HTMLElement>(
      '.dashboard-system-popover [role="menuitem"]:not([aria-disabled="true"])',
    ) ?? [],
  );
  const focusAt = (index: number) => items()[index]?.focus();
  const handleTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (!['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(event.key)) return;
    event.preventDefault();
    setOpen(true);
    window.requestAnimationFrame(() => focusAt(event.key === 'ArrowUp' ? items().length - 1 : 0));
  };
  const handleMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const menuItems = items();
    const current = menuItems.indexOf(document.activeElement as HTMLElement);
    let next: number | null = null;
    if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
      return;
    }
    if (event.key === 'ArrowDown') next = (current + 1) % menuItems.length;
    if (event.key === 'ArrowUp') next = (current - 1 + menuItems.length) % menuItems.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = menuItems.length - 1;
    if (next !== null) {
      event.preventDefault();
      menuItems[next]?.focus();
    }
  };

  return (
    <>
      <div
        ref={rootRef}
        className="dashboard-system-menu"
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
        }}
      >
        <button
          ref={triggerRef}
          type="button"
          className={`dashboard-system-trigger${active ? ' active' : ''}`}
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
          onKeyDown={handleTriggerKeyDown}
        >
          <TriggerIcon size={16} className="route-nav-icon" aria-hidden="true" />
          {triggerLabel}
          <ChevronDown size={14} aria-hidden="true" />
        </button>
        {open ? (
          <div className="dashboard-system-popover" role="menu" aria-label="System" onKeyDown={handleMenuKeyDown}>
            <DestinationSections destinations={destinations} uiInfo={uiInfo} onSelect={() => setOpen(false)} />
          </div>
        ) : null}
      </div>
      {mobileDrawerOpen ? (
        <div className="dashboard-system-inline" aria-label="System destinations">
          <div className="dashboard-system-inline-heading">System</div>
          <DestinationSections
            destinations={destinations}
            uiInfo={uiInfo}
            onSelect={() => undefined}
            menuItems={false}
          />
        </div>
      ) : null}
    </>
  );
}
