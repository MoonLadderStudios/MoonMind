import { Suspense, lazy, useEffect, useMemo, useState, type ComponentType, type ReactNode } from 'react';
import { QueryErrorResetBoundary } from '@tanstack/react-query';

import type { BootPayload } from '../boot/parseBootPayload';
import { validatePageBoot } from '../boot/pageBootSchemas';
import { DashboardErrorState } from '../components/DashboardErrorState';
import { DashboardRouteErrorBoundary } from '../components/DashboardRouteErrorBoundary';
import {
  isDashboardInternalUrl,
  payloadForDashboardRoute,
  resolveDashboardRoute,
  type DashboardClientRouteConfig,
  type DashboardRoute,
} from '../lib/dashboardRoutes';
import { DASHBOARD_NAVIGATE_EVENT, navigateTo } from '../lib/navigation';
import { DashboardAlerts } from './dashboard-alerts';

type PageComponent = ComponentType<{ payload: BootPayload }>;
type PageImport = () => Promise<{ default: PageComponent }>;

// Import factories (not memoized lazy components) so a retry can build a fresh
// `lazy(...)` and re-attempt the dynamic import. A single shared lazy component
// caches a rejected import (e.g. a transient chunk-load failure) and would
// replay that rejection on every retry, making the Retry action unrecoverable.
const PAGE_IMPORTS = {
  'index-health': () => import('./index-health'),
  manifests: () => import('./manifests'),
  'oauth-terminal': () => import('./oauth-terminal'),
  schedules: () => import('./schedules'),
  settings: () => import('./settings'),
  skills: () => import('./skills'),
  'workflow-start': () => import('./workflow-start'),
  'workflow-detail': () => import('./workflow-detail'),
  'workflows-home': () => import('./workflows-home'),
  'workflow-list': () => import('./workflow-list'),
} satisfies Record<string, PageImport>;

type SupportedPage = keyof typeof PAGE_IMPORTS;

type SharedLayoutConfig = {
  dataWidePanel?: boolean;
};

function isSupportedPage(page: string): page is SupportedPage {
  return Object.hasOwn(PAGE_IMPORTS, page);
}

function readSharedLayout(payload: BootPayload): SharedLayoutConfig {
  const raw = payload.initialData as { layout?: SharedLayoutConfig } | undefined;
  return raw?.layout ?? {};
}

function currentDashboardRoute(payload: BootPayload): DashboardRoute {
  return {
    page: payload.page as SupportedPage,
    dataWidePanel: readSharedLayout(payload).dataWidePanel === true,
    currentPath: typeof window === 'undefined' ? '/' : window.location.pathname,
  };
}

function setActiveNavigation(route: DashboardRoute): void {
  const links = document.querySelectorAll<HTMLAnchorElement>('[data-nav]');
  links.forEach((link) => {
    const href = link.getAttribute('href') ?? '';
    const url = new URL(href, window.location.origin);
    const isWorkflowsDetail =
      url.pathname === '/workflows' &&
      route.currentPath.startsWith('/workflows/') &&
      route.currentPath !== '/workflows/new';
    const active = url.pathname === route.currentPath || isWorkflowsDetail;
    link.classList.toggle('active', active);
    if (active) {
      link.setAttribute('aria-current', 'page');
    } else {
      link.removeAttribute('aria-current');
    }
  });
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

function LazyPageView({ page, payload }: { page: SupportedPage; payload: BootPayload }) {
  // Bumped on retry to rebuild the lazy component and remount the boundary, so a
  // failed dynamic import is re-attempted rather than replaying its cached error.
  const [reloadKey, setReloadKey] = useState(0);
  const LazyPage = useMemo(() => lazy(PAGE_IMPORTS[page]), [page, reloadKey]);

  return (
    <QueryErrorResetBoundary>
      {({ reset }) => (
        <DashboardRouteErrorBoundary
          key={reloadKey}
          onReset={() => {
            reset();
            setReloadKey((value) => value + 1);
          }}
        >
          <Suspense fallback={<LoadingPage />}>
            <LazyPage payload={payload} />
          </Suspense>
        </DashboardRouteErrorBoundary>
      )}
    </QueryErrorResetBoundary>
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

  return <LazyPageView page={payload.page} payload={payload} />;
}

export function DashboardApp({ payload }: { payload: BootPayload }) {
  const [route, setRoute] = useState<DashboardRoute>(() => currentDashboardRoute(payload));
  const [hasClientNavigated, setHasClientNavigated] = useState(false);
  const [clientRouteConfig, setClientRouteConfig] = useState<DashboardClientRouteConfig | null>(null);
  const routedPayload = useMemo(
    () => (hasClientNavigated ? payloadForDashboardRoute(payload, route, clientRouteConfig) : payload),
    [clientRouteConfig, hasClientNavigated, payload, route],
  );
  const layout = readSharedLayout(routedPayload);
  const routeKey =
    typeof window === 'undefined'
      ? `${route.page}:${route.currentPath}`
      : `${route.page}:${route.currentPath}${window.location.search}${window.location.hash}`;

  useEffect(() => {
    const refreshRoute = () => {
      const nextRoute = resolveDashboardRoute(window.location.pathname);
      if (nextRoute) {
        setHasClientNavigated(true);
        setClientRouteConfig(null);
        setRoute(nextRoute);
      }
    };

    const handleClick = (event: MouseEvent) => {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      ) {
        return;
      }
      const target = event.target;
      if (!(target instanceof Element)) {
        return;
      }
      const link = target.closest<HTMLAnchorElement>('a[href]');
      if (!link || link.target || link.hasAttribute('download')) {
        return;
      }
      const url = new URL(link.href, window.location.origin);
      if (!isDashboardInternalUrl(url)) {
        return;
      }
      event.preventDefault();
      navigateTo(`${url.pathname}${url.search}${url.hash}`);
    };

    window.addEventListener('popstate', refreshRoute);
    window.addEventListener(DASHBOARD_NAVIGATE_EVENT, refreshRoute);
    document.addEventListener('click', handleClick);
    return () => {
      window.removeEventListener('popstate', refreshRoute);
      window.removeEventListener(DASHBOARD_NAVIGATE_EVENT, refreshRoute);
      document.removeEventListener('click', handleClick);
    };
  }, []);

  useEffect(() => {
    if (!hasClientNavigated) {
      return;
    }
    const controller = new AbortController();
    const currentPath = route.currentPath;

    fetch(`/api/dashboard/config?currentPath=${encodeURIComponent(currentPath)}`, {
      credentials: 'same-origin',
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Dashboard config request failed: ${response.status}`);
        }
        return response.json() as Promise<DashboardClientRouteConfig>;
      })
      .then((config) => {
        setClientRouteConfig(config);
      })
      .catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === 'AbortError')) {
          setClientRouteConfig(null);
        }
      });

    return () => {
      controller.abort();
    };
  }, [hasClientNavigated, route.currentPath]);

  useEffect(() => {
    setActiveNavigation(route);
  }, [route]);

  return (
    <AppShell dataWidePanel={layout.dataWidePanel === true}>
      <PageContent key={routeKey} payload={routedPayload} />
    </AppShell>
  );
}

export default DashboardApp;
