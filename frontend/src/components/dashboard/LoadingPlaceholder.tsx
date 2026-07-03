import type { CSSProperties, HTMLAttributes } from 'react';

export type LoadingPlaceholderSurface =
  | 'workflow-list'
  | 'workflow-detail'
  | 'settings'
  | 'schedules'
  | 'manifests'
  | 'skills'
  | 'workflow-start';

export type LoadingPlaceholderVariant =
  | 'list'
  | 'detail'
  | 'settings'
  | 'catalog'
  | 'table'
  | 'compact-controls'
  | 'form-controls'
  | 'metric-strip'
  | 'operations';

export type LoadingPlaceholderDensity = 'compact' | 'normal' | 'detail-heavy';

interface LoadingPlaceholderProps extends HTMLAttributes<HTMLElement> {
  surface: LoadingPlaceholderSurface;
  region: string;
  variant: LoadingPlaceholderVariant;
  density?: LoadingPlaceholderDensity;
  preserveContext?: boolean;
}

const surfaceLabels: Record<LoadingPlaceholderSurface, string> = {
  'workflow-list': 'Workflow list',
  'workflow-detail': 'Workflow detail',
  settings: 'Settings',
  schedules: 'Schedules',
  manifests: 'Manifests',
  skills: 'Skills',
  'workflow-start': 'Workflow start',
};

const variantRows: Record<LoadingPlaceholderVariant, number[]> = {
  list: [3, 3, 3],
  detail: [2, 4, 3, 3],
  settings: [2, 3, 3],
  catalog: [2, 2, 2, 2],
  table: [4, 4, 4],
  'compact-controls': [3, 3],
  'form-controls': [2, 2, 3],
  'metric-strip': [1, 1, 1, 1],
  operations: [3, 2, 2],
};

function blockWidth(rowIndex: number, cellIndex: number): string {
  const widths = ['62%', '84%', '48%', '72%'];
  return widths[(rowIndex + cellIndex) % widths.length] ?? '64%';
}

export function LoadingPlaceholder({
  surface,
  region,
  variant,
  density = 'normal',
  preserveContext = false,
  className,
  ...rest
}: LoadingPlaceholderProps) {
  const classes = ['loading-placeholder', `loading-placeholder--${variant}`];
  if (className) {
    classes.push(className);
  }
  const label = `${surfaceLabels[surface]} ${region} loading placeholder`;
  const rows = variantRows[variant];

  return (
    <section
      role="status"
      aria-busy="true"
      className={classes.join(' ')}
      data-testid={`loading-placeholder-${variant}`}
      data-surface={surface}
      data-region={region}
      data-variant={variant}
      data-density={density}
      data-preserve-context={preserveContext ? 'true' : 'false'}
      {...rest}
    >
      <span className="sr-only">{label}</span>
      <div className="loading-placeholder__grid" aria-hidden="true">
        {rows.map((cellCount, rowIndex) => (
          <div
            className="loading-placeholder__row"
            data-testid="loading-placeholder-row"
            key={`${variant}-${rowIndex}`}
          >
            {Array.from({ length: cellCount }).map((_, cellIndex) => (
              <span
                className="loading-placeholder__block loading-placeholder__cell"
                data-testid="loading-placeholder-block"
                key={`${rowIndex}-${cellIndex}`}
                style={{ '--loading-placeholder-block-width': blockWidth(rowIndex, cellIndex) } as CSSProperties}
              />
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}

export default LoadingPlaceholder;
