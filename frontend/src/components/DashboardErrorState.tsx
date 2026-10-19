import type { ReactNode } from 'react';

import { apiErrorKind, type ApiErrorKind } from '../lib/api/client';
import { Button } from './dashboard/Button';

/**
 * Styled dashboard error card (MM-960).
 *
 * Shared presentation for route errors, configuration errors, and boot
 * failures so dashboard load failures are recoverable, styled, and
 * diagnosable rather than raw unstyled HTML. The title is the page's `h1`
 * unless the state renders under a page that already owns one.
 */
export function DashboardErrorState({
  title,
  description,
  detail,
  onRetry,
  retryLabel = 'Try again',
  headingLevel = 1,
}: {
  title: string;
  description: ReactNode;
  detail?: string | null;
  onRetry?: (() => void) | undefined;
  retryLabel?: string;
  headingLevel?: 1 | 2 | undefined;
}) {
  const Heading = headingLevel === 1 ? 'h1' : 'h2';
  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
      <div
        role="alert"
        className="rounded-3xl border border-rose-200 bg-rose-50 p-6 shadow-sm dark:border-rose-900/50 dark:bg-rose-900/20"
      >
        <Heading className="text-base font-semibold text-rose-800 dark:text-rose-300">{title}</Heading>
        <div className="mt-2 text-sm text-rose-700 dark:text-rose-400">{description}</div>
        {detail ? (
          <pre className="mt-3 max-h-40 overflow-auto rounded-xl bg-rose-100/70 p-3 text-xs text-rose-800 dark:bg-rose-950/40 dark:text-rose-300">
            {detail}
          </pre>
        ) : null}
        {onRetry ? (
          <div className="mt-4">
            <Button onClick={onRetry}>{retryLabel}</Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

const API_ERROR_COPY: Record<
  ApiErrorKind,
  (subject: string) => { title: string; description: string; retryable: boolean }
> = {
  not_found: (subject) => ({
    title: `${capitalize(subject)} not found`,
    description: `No ${subject} exists at this address. It may have been removed, or the link is wrong.`,
    retryable: false,
  }),
  unauthorized: (subject) => ({
    title: 'Sign-in required',
    description: `The API did not accept this session for the ${subject}. Sign in again, then retry.`,
    retryable: true,
  }),
  forbidden: (subject) => ({
    title: 'Access denied',
    description: `This deployment does not allow viewing this ${subject}.`,
    retryable: false,
  }),
  client: (subject) => ({
    title: `${capitalize(subject)} could not be loaded`,
    description: `The API rejected the request for this ${subject}.`,
    retryable: false,
  }),
  unavailable: (subject) => ({
    title: `${capitalize(subject)} unavailable`,
    description: `MoonMind could not load this ${subject} right now. The service may be restarting; retry in a moment.`,
    retryable: true,
  }),
  network: (subject) => ({
    title: `${capitalize(subject)} unavailable`,
    description: `MoonMind could not be reached to load this ${subject}. Check the connection, then retry.`,
    retryable: true,
  }),
};

/**
 * Load-failure state for an entity with no data to show: not found (404/410),
 * sign-in required (401), access denied (403), rejected (other 4xx) or
 * unavailable (5xx and network failures, with a retry). Permanent failures
 * offer no retry because repeating the request cannot change the answer.
 */
export function ApiErrorState({
  error,
  subject,
  onRetry,
  headingLevel,
}: {
  error: unknown;
  /** Lower-case noun for the entity, e.g. "workflow". */
  subject: string;
  onRetry?: () => void;
  headingLevel?: 1 | 2 | undefined;
}) {
  const copy = API_ERROR_COPY[apiErrorKind(error)](subject);
  return (
    <DashboardErrorState
      title={copy.title}
      description={copy.description}
      detail={error instanceof Error ? error.message : null}
      onRetry={copy.retryable ? onRetry : undefined}
      retryLabel="Retry"
      headingLevel={headingLevel}
    />
  );
}

export default DashboardErrorState;
