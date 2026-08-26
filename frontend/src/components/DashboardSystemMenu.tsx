import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Archive, Bot, ChevronDown, Moon, Rows3, Settings, ShieldCheck, Sparkles, Wrench } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';

import {
  destinationForPath,
  visibleSystemDestinations,
  type DashboardDestination,
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
  settings: 'Configuration',
};

function DestinationLink({ destination, onSelect, menuItem = true }: {
  destination: DashboardDestination;
  onSelect: () => void;
  menuItem?: boolean;
}) {
  const Icon = ICONS[destination.iconKey] ?? Settings;
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

function DestinationSections({ destinations, onSelect, menuItems = true }: {
  destinations: DashboardDestination[];
  onSelect: () => void;
  menuItems?: boolean;
}) {
  return destinations.map((destination) => {
    const sectionLabel = SECTION_LABELS[destination.key];
    return (
      <div className="dashboard-system-menu-section" key={destination.key}>
        {sectionLabel ? <div className="dashboard-system-menu-label">{sectionLabel}</div> : null}
        <DestinationLink destination={destination} onSelect={onSelect} menuItem={menuItems} />
      </div>
    );
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
  const destinations = visibleSystemDestinations(uiInfo);
  const activeDestination = destinationForPath(location.pathname);
  const active = Boolean(activeDestination && activeDestination.navigationGroup !== 'primary');
  // When a System destination is active, the trigger takes on that selection's
  // one-word label and icon (e.g. "Skills") instead of the generic "System".
  const triggerLabel = active && activeDestination ? activeDestination.label : 'System';
  const TriggerIcon = active && activeDestination ? (ICONS[activeDestination.iconKey] ?? Settings) : Settings;

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
    rootRef.current?.querySelectorAll<HTMLAnchorElement>('.dashboard-system-popover [role="menuitem"]') ?? [],
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
    const current = menuItems.indexOf(document.activeElement as HTMLAnchorElement);
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
            <DestinationSections destinations={destinations} onSelect={() => setOpen(false)} />
          </div>
        ) : null}
      </div>
      {mobileDrawerOpen ? (
        <div className="dashboard-system-inline" aria-label="System destinations">
          <div className="dashboard-system-inline-heading">System</div>
          <DestinationSections destinations={destinations} onSelect={() => undefined} menuItems={false} />
        </div>
      ) : null}
    </>
  );
}
