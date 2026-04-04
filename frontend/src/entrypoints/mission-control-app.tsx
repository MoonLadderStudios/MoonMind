import { Suspense, lazy, useMemo, type ComponentType, type ReactNode } from 'react';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardAlerts } from './dashboard-alerts';

type PageComponent = ComponentType<{ payload: BootPayload }>;
type PageModule = { default: PageComponent };

const PAGE_LOADERS = {
  'manifest-submit': () => import('./manifest-submit'),
  manifests: () => import('./manifests'),
  proposals: () => import('./proposals'),
  schedules: () => import('./schedules'),
  settings: () => import('./settings'),
  skills: () => import('./skills'),
  'task-create': () => import('./task-create'),
  'task-detail': () => import('./task-detail'),
  'tasks-home': () => import('./tasks-home'),
  'tasks-list': () => import('./tasks-list'),
} satisfies Record<string, () => Promise<PageModule>>;

type SupportedPage = keyof typeof PAGE_LOADERS;

type SharedLayoutConfig = {
  dataWidePanel?: boolean;
};

function isSupportedPage(page: string): page is SupportedPage {
  return page in PAGE_LOADERS;
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
  const pageLoader = isSupportedPage(payload.page) ? PAGE_LOADERS[payload.page] : null;
  const LazyPage = useMemo(
    () => (pageLoader ? lazy(pageLoader) : null),
    [pageLoader],
  );

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
