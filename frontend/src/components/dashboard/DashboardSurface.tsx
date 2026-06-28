import type { ElementType, HTMLAttributes, ReactNode } from 'react';

/**
 * Explicit surface hierarchy primitive (MM-959).
 *
 * Pages declare their surface intent directly via a variant instead of
 * relying on global `.panel:has(...)` exceptions to neutralize or re-style
 * the default panel chrome. Each variant maps to a `dashboard-surface--<variant>`
 * class defined in dashboard.css.
 */
export type DashboardSurfaceVariant =
  | 'page'
  | 'controlDeck'
  | 'dataSlab'
  | 'formSlab'
  | 'evidenceSlab'
  | 'floatingRail'
  | 'debugDrawer';

export interface DashboardSurfaceProps
  extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  variant: DashboardSurfaceVariant;
  /** Render element/component for the surface (defaults to `section`). */
  as?: ElementType;
  children?: ReactNode;
}

export function DashboardSurface({
  variant,
  as,
  className,
  children,
  ...rest
}: DashboardSurfaceProps) {
  const Component = (as ?? 'section') as ElementType;
  const classes = ['dashboard-surface', `dashboard-surface--${variant}`];
  if (className) {
    classes.push(className);
  }
  return (
    <Component className={classes.join(' ')} data-surface={variant} {...rest}>
      {children}
    </Component>
  );
}

export default DashboardSurface;
