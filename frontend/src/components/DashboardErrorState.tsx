import type { ReactNode } from 'react';

/**
 * Styled dashboard error card (MM-960).
 *
 * Shared presentation for route errors, configuration errors, and boot
 * failures so dashboard load failures are recoverable, styled, and
 * diagnosable rather than raw unstyled HTML.
 */
export function DashboardErrorState({
  title,
  description,
  detail,
  onRetry,
  retryLabel = 'Try again',
}: {
  title: string;
  description: ReactNode;
  detail?: string | null;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
      <div
        role="alert"
        className="rounded-3xl border border-rose-200 bg-rose-50 p-6 shadow-sm dark:border-rose-900/50 dark:bg-rose-900/20"
      >
        <h2 className="text-base font-semibold text-rose-800 dark:text-rose-300">{title}</h2>
        <div className="mt-2 text-sm text-rose-700 dark:text-rose-400">{description}</div>
        {detail ? (
          <pre className="mt-3 max-h-40 overflow-auto rounded-xl bg-rose-100/70 p-3 text-xs text-rose-800 dark:bg-rose-950/40 dark:text-rose-300">
            {detail}
          </pre>
        ) : null}
        {onRetry ? (
          <div className="mt-4">
            <button type="button" onClick={onRetry}>
              {retryLabel}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default DashboardErrorState;
