import type { ElementType, ReactNode } from 'react';

export type CollectionWorkspaceMode = 'single' | 'sidebar' | 'detail' | 'preview' | 'create' | 'table';

export function CollectionWorkspace({
  collection,
  mode,
  sidebar,
  children,
  utilities,
  className,
  primaryClassName,
  primaryAs: Primary = 'main',
  primaryLabel,
  sidebarState = 'ready',
  primaryState = 'ready',
  ...rest
}: {
  collection: string;
  mode: CollectionWorkspaceMode;
  sidebar?: ReactNode;
  children: ReactNode;
  utilities?: ReactNode;
  className?: string;
  primaryClassName?: string;
  primaryAs?: ElementType;
  primaryLabel?: string;
  sidebarState?: 'loading' | 'ready' | 'empty' | 'error';
  primaryState?: 'loading' | 'ready' | 'empty' | 'error';
} & Omit<React.HTMLAttributes<HTMLDivElement>, 'children'>) {
  const hasSidebar = sidebar !== undefined && sidebar !== null;

  return (
    <div
      className={`collection-workspace${hasSidebar ? ' collection-workspace--with-sidebar' : ' collection-workspace--single'}${className ? ` ${className}` : ''}`}
      data-collection={collection}
      data-collection-mode={mode}
      data-sidebar-present={hasSidebar ? 'true' : 'false'}
      data-sidebar-state={sidebarState}
      data-primary-state={primaryState}
      {...rest}
    >
      {sidebar}
      <Primary className={`collection-workspace__primary${primaryClassName ? ` ${primaryClassName}` : ''}`} aria-label={primaryLabel}>
        {utilities ? <div className="collection-workspace__utilities">{utilities}</div> : null}
        {children}
      </Primary>
    </div>
  );
}
