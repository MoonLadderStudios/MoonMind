import { Suspense, lazy, type ComponentType, type ReactNode } from 'react';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardAlerts } from './dashboard-alerts';

type PageComponent = ComponentType<{ payload: BootPayload }>;
const PAGE_COMPONENTS = {
  manifests: lazy(() => import('./manifests')),
  'oauth-terminal': lazy(() => import('./oauth-terminal')),
  proposals: lazy(() => import('./proposals')),
  schedules: lazy(() => import('./schedules')),
  settings: lazy(() => import('./settings')),
  skills: lazy(() => import('./skills')),
  'task-create': lazy(() => import('./task-create')),
  'task-detail': lazy(() => import('./task-detail')),
  'tasks-home': lazy(() => import('./tasks-home')),
  'tasks-list': lazy(() => import('./tasks-list')),
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
        Loading Mission Control...
      </div>
    </div>
  );
}

function UnknownPage({ page }: { page: string }) {
  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="rounded-3xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700 shadow-sm dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-400">
        Unknown Mission Control page: <code>{page}</code>
      </div>
    </div>
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
      <div className="dashboard-shell-constrained">
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

export function MissionControlApp({ payload }: { payload: BootPayload }) {
  const layout = readSharedLayout(payload);
  const LazyPage = isSupportedPage(payload.page) ? PAGE_COMPONENTS[payload.page] : null;

  return (
    <AppShell dataWidePanel={layout.dataWidePanel === true}>
      {LazyPage ? (
        <Suspense fallback={<LoadingPage />}>
          <LazyPage payload={payload} />
        </Suspense>
      ) : (
        <UnknownPage page={payload.page} />
      )}
    </AppShell>
  );
}

export default MissionControlApp;
