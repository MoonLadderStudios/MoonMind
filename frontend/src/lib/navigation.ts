import { isDashboardInternalUrl, resolveDashboardRoute } from './dashboardRoutes';

export function navigateTo(path: string): void {
  const url = new URL(path, window.location.origin);
  const isRelativeDashboardPath =
    path.startsWith('/') && resolveDashboardRoute(url.pathname) !== null;
  if (!isRelativeDashboardPath && !isDashboardInternalUrl(url)) {
    window.location.assign(path);
    return;
  }
  const next = `${url.pathname}${url.search}${url.hash}`;
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next !== current) {
    window.history.pushState({ moonmindDashboard: true }, '', next);
  }
  window.dispatchEvent(new PopStateEvent('popstate', { state: window.history.state }));
}
