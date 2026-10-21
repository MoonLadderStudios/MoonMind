import { Button } from '../Button';
import { useEffect, type HTMLAttributes, type ReactNode } from 'react';

const DOCUMENT_TITLE_SUFFIX = 'MoonMind';

export function documentTitleFor(title: string | null | undefined): string {
  const trimmed = title?.trim();
  return trimmed ? `${trimmed} | ${DOCUMENT_TITLE_SUFFIX}` : DOCUMENT_TITLE_SUFFIX;
}

/** Sets the browser tab title for the current page while it is mounted. */
export function useDocumentTitle(title: string | null | undefined): void {
  useEffect(() => {
    document.title = documentTitleFor(title);
  }, [title]);
}

export type PageHeaderProps = {
  /** Page title, rendered as the page's single `h1` and used for the tab title. */
  title: ReactNode;
  /** Plain-text tab title when `title` is not a string. */
  documentTitle?: string | undefined;
  description?: ReactNode;
  eyebrow?: ReactNode;
  /** Primary action(s), rendered beside the refresh button. */
  actions?: ReactNode;
  onRefresh?: (() => void) | undefined;
  refreshing?: boolean | undefined;
  refreshLabel?: string | undefined;
  /** Extra content under the title row, such as an identity line. */
  children?: ReactNode;
  className?: string | undefined;
  headingProps?: HTMLAttributes<HTMLHeadingElement> & Record<`data-${string}`, unknown>;
};

export function PageHeader({
  title,
  documentTitle,
  description,
  eyebrow,
  actions,
  onRefresh,
  refreshing = false,
  refreshLabel = 'Refresh',
  children,
  className,
  headingProps,
}: PageHeaderProps) {
  useDocumentTitle(documentTitle ?? (typeof title === 'string' ? title : null));
  const hasActions = Boolean(actions) || Boolean(onRefresh);
  return (
    <header className={className ? `page-header ${className}` : 'page-header'}>
      <div className="page-header__row">
        <div className="page-header__text">
          {eyebrow ? <p className="page-header__eyebrow">{eyebrow}</p> : null}
          <h1 {...headingProps} className={headingProps?.className ? `page-title ${headingProps.className}` : 'page-title'}>
            {title}
          </h1>
          {description ? <p className="page-header__description">{description}</p> : null}
        </div>
        {hasActions ? (
          <div className="page-header__actions">
            {actions}
            {onRefresh ? (
              <Button variant="secondary"
                type="button"
                onClick={onRefresh}
                disabled={refreshing}
                aria-busy={refreshing || undefined}
              >
                {refreshing ? 'Refreshing…' : refreshLabel}
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
      {children}
    </header>
  );
}
