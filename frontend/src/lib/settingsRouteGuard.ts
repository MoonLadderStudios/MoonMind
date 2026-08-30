export const SETTINGS_ROUTE_CHANGE_REQUEST_EVENT =
  'moonmind:settings-route-change-request';

export type SettingsRouteChangeRequestDetail = {
  href: string;
};

export function requestSettingsRouteChange(href: string): boolean {
  if (typeof window === 'undefined') {
    return true;
  }
  return window.dispatchEvent(
    new CustomEvent<SettingsRouteChangeRequestDetail>(
      SETTINGS_ROUTE_CHANGE_REQUEST_EVENT,
      {
        cancelable: true,
        detail: { href },
      },
    ),
  );
}
