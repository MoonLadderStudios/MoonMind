import { useId, useState, type ReactNode } from 'react';

/**
 * Consistent dashboard notice surface (MM-959).
 *
 * Replaces ad hoc `.notice` markup so info/success/warning/error/pending
 * messages share a single accessible structure with optional title, actions,
 * and collapsible technical details.
 */
export type DashboardNoticeVariant =
  | 'info'
  | 'success'
  | 'warning'
  | 'error'
  | 'pending';

export interface DashboardNoticeProps {
  variant: DashboardNoticeVariant;
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  /** Optional collapsible technical details disclosure. */
  details?: ReactNode;
  detailsLabel?: string;
  className?: string;
}

export function DashboardNotice({
  variant,
  title,
  children,
  actions,
  details,
  detailsLabel = 'Technical details',
  className,
}: DashboardNoticeProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const titleId = useId();

  const classes = ['dashboard-notice', `dashboard-notice--${variant}`];
  if (className) {
    classes.push(className);
  }

  const role = variant === 'error' ? 'alert' : 'status';
  const ariaLive = variant === 'error' ? 'assertive' : 'polite';

  return (
    <div
      className={classes.join(' ')}
      data-variant={variant}
      role={role}
      aria-live={ariaLive}
      aria-labelledby={title ? titleId : undefined}
    >
      {title ? (
        <p className="dashboard-notice__title" id={titleId}>
          {title}
        </p>
      ) : null}
      {children ? <div className="dashboard-notice__body">{children}</div> : null}
      {details ? (
        <details
          className="dashboard-notice__details"
          open={detailsOpen}
          onToggle={(event) =>
            setDetailsOpen((event.target as HTMLDetailsElement).open)
          }
        >
          <summary className="dashboard-notice__details-summary">
            {detailsLabel}
          </summary>
          <div className="dashboard-notice__details-body">{details}</div>
        </details>
      ) : null}
      {actions ? (
        <div className="dashboard-notice__actions">{actions}</div>
      ) : null}
    </div>
  );
}

export default DashboardNotice;
