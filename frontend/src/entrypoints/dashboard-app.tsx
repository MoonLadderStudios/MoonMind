import { Suspense, lazy, type ComponentType, type ReactNode } from 'react';
import { QueryErrorResetBoundary } from '@tanstack/react-query';

import type { BootPayload } from '../boot/parseBootPayload';
import { validatePageBoot } from '../boot/pageBootSchemas';
import { DashboardErrorState } from '../components/DashboardErrorState';
import { DashboardRouteErrorBoundary } from '../components/DashboardRouteErrorBoundary';
import { DashboardAlerts } from './dashboard-alerts';

type PageComponent = ComponentType<{ payload: BootPayload }>;
const PAGE_COMPONENTS = {
  'index-health': lazy(() => import('./index-health')),
  manifests: lazy(() => import('./manifests')),
  'oauth-terminal': lazy(() => import('./oauth-terminal')),
  schedules: lazy(() => import('./schedules')),
  settings: lazy(() => import('./settings')),
  skills: lazy(() => import('./skills')),
  'workflow-start': lazy(() => import('./workflow-start')),
  'workflow-detail': lazy(() => import('./workflow-detail')),
  'workflows-home': lazy(() => import('./workflows-home')),
  'workflow-list': lazy(() => import('./workflow-list')),
} satisfies Record<string, PageComponent>;

type SupportedPage = keyof typeof PAGE_COMPONENTS;

type SharedLayoutConfig = {
  dataWidePanel?: boolean;
};

function isSupportedPage(page: string): page is SupportedPage {
  return Object.hasOwn(PAGE_COMPONENTS, page);
}

function readSharedLayout(payload: BootPayload): SharedLayoutConfig {
  const raw = payload.initialData as { layout?: SharedLayoutConfig } | undefined;
  return raw?.layout ?? {};
}

function LoadingPage() {
  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="rounded-3xl border border-mm-border/80 bg-transparent p-6 text-sm text-slate-500 shadow-sm dark:text-slate-400">
        Loading MoonMind...
      </div>
    </div>
  );
}

function UnknownPage({ page }: { page: string }) {
  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="rounded-3xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700 shadow-sm dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-400">
        Unknown dashboard page: <code>{page}</code>
      </div>
    </div>
  );
}

function ConfigurationErrorPage({ page, message }: { page: string; message: string }) {
  return (
    <DashboardErrorState
      title="Dashboard configuration error"
      description={
        <>
          The <code>{page}</code> page received invalid boot data and cannot be displayed. Please
          reload, and contact support if the problem persists.
        </>
      }
      detail={message}
    />
  );
}

function AppShell({
  dataWidePanel,
  children,
}: {
  dataWidePanel: boolean;
  children: ReactNode;
}) {
  return (
    <>
      <div
        className={`dashboard-shell-constrained${dataWidePanel ? ' dashboard-shell-constrained--data-wide' : ''}`}
      >
        <DashboardAlerts />
      </div>
      <section
        className={`panel${dataWidePanel ? ' panel--data-wide' : ''}`}
        aria-live="polite"
      >
        {children}
      </section>
    </>
  );
}

function PageContent({ payload }: { payload: BootPayload }) {
  if (!isSupportedPage(payload.page)) {
    return <UnknownPage page={payload.page} />;
  }

  const validation = validatePageBoot(payload.page, payload);
  if (!validation.ok) {
    return <ConfigurationErrorPage page={payload.page} message={validation.message} />;
  }

  const LazyPage = PAGE_COMPONENTS[payload.page];

  return (
    <QueryErrorResetBoundary>
      {({ reset }) => (
        <DashboardRouteErrorBoundary onReset={reset}>
          <Suspense fallback={<LoadingPage />}>
            <LazyPage payload={payload} />
          </Suspense>
        </DashboardRouteErrorBoundary>
      )}
    </QueryErrorResetBoundary>
  );
}

export function DashboardApp({ payload }: { payload: BootPayload }) {
  const layout = readSharedLayout(payload);

  return (
    <AppShell dataWidePanel={layout.dataWidePanel === true}>
      <PageContent payload={payload} />
    </AppShell>
  );
}

export default DashboardApp;
