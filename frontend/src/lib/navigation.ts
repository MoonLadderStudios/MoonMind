import { isDashboardInternalUrl } from './dashboardRoutes';

export const DASHBOARD_NAVIGATE_EVENT = 'moonmind:dashboard-navigate';

export function navigateTo(path: string): void {
  const url = new URL(path, window.location.origin);
  if (!isDashboardInternalUrl(url)) {
    window.location.assign(path);
    return;
  }
  const next = `${url.pathname}${url.search}${url.hash}`;
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next !== current) {
    window.history.pushState({ moonmindDashboard: true }, '', next);
  }
  window.dispatchEvent(new CustomEvent(DASHBOARD_NAVIGATE_EVENT, { detail: { path: next } }));
}
