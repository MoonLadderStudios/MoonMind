import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

interface DraftRegistration {
  dirty: boolean;
  discard: () => void;
}

interface PendingDeparture {
  action: () => void;
  description: string;
}

interface PendingHistoryDeparture {
  restoreDelta: number;
  description: string;
}

interface SettingsDraftGuardValue {
  registerDraft: (draftId: string, registration: DraftRegistration | null) => void;
  requestDeparture: (action: () => void, description?: string) => boolean;
}

const immediateGuard: SettingsDraftGuardValue = {
  registerDraft: () => undefined,
  requestDeparture: (action) => {
    action();
    return true;
  },
};

const SettingsDraftGuardContext = createContext<SettingsDraftGuardValue>(immediateGuard);

function currentBrowserPath(): string {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function historyIndex(state: unknown = window.history.state): number | null {
  if (!state || typeof state !== 'object' || !('idx' in state)) {
    return null;
  }
  const index = (state as { idx?: unknown }).idx;
  return typeof index === 'number' && Number.isInteger(index) ? index : null;
}

function isPlainInternalNavigation(event: MouseEvent, anchor: HTMLAnchorElement): boolean {
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey ||
    anchor.target ||
    anchor.hasAttribute('download')
  ) {
    return false;
  }
  const rawHref = (anchor.getAttribute('href') ?? '').trimStart();
  return Boolean(rawHref && !rawHref.startsWith('#'));
}

export function SettingsDraftGuardProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();
  const registrationsRef = useRef(new Map<string, DraftRegistration>());
  const currentPathRef = useRef(currentBrowserPath());
  const currentHistoryIndexRef = useRef(historyIndex());
  const restoringPopStateRef = useRef(false);
  const pendingHistoryDepartureRef = useRef<PendingHistoryDeparture | null>(null);
  const stayButtonRef = useRef<HTMLButtonElement | null>(null);
  const discardButtonRef = useRef<HTMLButtonElement | null>(null);
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);
  const [pendingDeparture, setPendingDeparture] = useState<PendingDeparture | null>(null);

  const hasDirtyDraft = useCallback(
    () => Array.from(registrationsRef.current.values()).some((draft) => draft.dirty),
    [],
  );

  const registerDraft = useCallback(
    (draftId: string, registration: DraftRegistration | null) => {
      if (registration) {
        registrationsRef.current.set(draftId, registration);
      } else {
        registrationsRef.current.delete(draftId);
      }
    },
    [],
  );

  const requestDeparture = useCallback(
    (action: () => void, description = 'Leave this page?') => {
      if (!hasDirtyDraft()) {
        action();
        return true;
      }
      setPendingDeparture({ action, description });
      return false;
    },
    [hasDirtyDraft],
  );

  useEffect(() => {
    currentPathRef.current = `${location.pathname}${location.search}${location.hash}`;
    currentHistoryIndexRef.current = historyIndex();
  }, [location.hash, location.pathname, location.search]);

  useEffect(() => {
    const handleDocumentClick = (event: MouseEvent) => {
      const target = event.target;
      const anchor = target instanceof Element ? target.closest<HTMLAnchorElement>('a[href]') : null;
      if (!anchor || !isPlainInternalNavigation(event, anchor)) {
        return;
      }
      const destination = new URL(anchor.href, window.location.href);
      if (destination.origin !== window.location.origin) {
        return;
      }
      const targetPath = `${destination.pathname}${destination.search}${destination.hash}`;
      if (targetPath === currentPathRef.current || !hasDirtyDraft()) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      requestDeparture(
        () => navigate(targetPath),
        'Leave this Settings page? Your unsaved draft will be discarded.',
      );
    };

    const handlePopState = (event: PopStateEvent) => {
      if (restoringPopStateRef.current) {
        restoringPopStateRef.current = false;
        const pendingHistoryDeparture = pendingHistoryDepartureRef.current;
        pendingHistoryDepartureRef.current = null;
        if (pendingHistoryDeparture) {
          requestDeparture(
            () => window.history.go(-pendingHistoryDeparture.restoreDelta),
            pendingHistoryDeparture.description,
          );
        }
        return;
      }
      const targetPath = currentBrowserPath();
      const activePath = currentPathRef.current;
      if (targetPath === activePath || !hasDirtyDraft()) {
        return;
      }

      const activeIndex = currentHistoryIndexRef.current;
      const targetIndex = historyIndex(event.state);
      const restoreDelta =
        activeIndex !== null && targetIndex !== null && activeIndex !== targetIndex
          ? activeIndex - targetIndex
          : 1;

      // popstate fires after the browser has moved. Replay the inverse traversal
      // to restore the dirty route without appending history entries, then replay
      // the original traversal only after the user confirms discard.
      event.stopImmediatePropagation();
      restoringPopStateRef.current = true;
      pendingHistoryDepartureRef.current = {
        restoreDelta,
        description: 'Leave this Settings page? Your unsaved draft will be discarded.',
      };
      window.history.go(restoreDelta);
    };

    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (!hasDirtyDraft()) {
        return;
      }
      event.preventDefault();
      event.returnValue = '';
    };

    document.addEventListener('click', handleDocumentClick, true);
    window.addEventListener('popstate', handlePopState);
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => {
      document.removeEventListener('click', handleDocumentClick, true);
      window.removeEventListener('popstate', handlePopState);
      window.removeEventListener('beforeunload', handleBeforeUnload);
    };
  }, [hasDirtyDraft, navigate, requestDeparture]);

  const value = useMemo<SettingsDraftGuardValue>(
    () => ({ registerDraft, requestDeparture }),
    [registerDraft, requestDeparture],
  );

  const stay = () => setPendingDeparture(null);
  const discardAndLeave = () => {
    const departure = pendingDeparture;
    if (!departure) {
      return;
    }
    for (const registration of registrationsRef.current.values()) {
      if (registration.dirty) {
        registration.discard();
      }
    }
    registrationsRef.current.clear();
    setPendingDeparture(null);
    departure.action();
  };

  const departurePending = pendingDeparture !== null;
  useEffect(() => {
    if (!departurePending) {
      return undefined;
    }
    previouslyFocusedRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    stayButtonRef.current?.focus();
    return () => {
      const previous = previouslyFocusedRef.current;
      previouslyFocusedRef.current = null;
      if (previous?.isConnected) {
        previous.focus();
      }
    };
  }, [departurePending]);

  const handleDialogKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      stay();
      return;
    }
    if (event.key !== 'Tab') {
      return;
    }
    if (event.shiftKey && document.activeElement === stayButtonRef.current) {
      event.preventDefault();
      discardButtonRef.current?.focus();
    } else if (!event.shiftKey && document.activeElement === discardButtonRef.current) {
      event.preventDefault();
      stayButtonRef.current?.focus();
    }
  };

  return (
    <SettingsDraftGuardContext.Provider value={value}>
      {children}
      {pendingDeparture ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/55 p-4"
          role="presentation"
        >
          <section
            aria-describedby="settings-unsaved-description"
            aria-labelledby="settings-unsaved-title"
            aria-modal="true"
            className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-700 dark:bg-slate-900"
            onKeyDown={handleDialogKeyDown}
            role="dialog"
          >
            <h2 id="settings-unsaved-title" className="text-lg font-semibold text-slate-950 dark:text-white">
              Unsaved changes
            </h2>
            <p id="settings-unsaved-description" className="mt-2 text-sm text-slate-600 dark:text-slate-400">
              {pendingDeparture.description}
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 dark:border-slate-700 dark:text-slate-200"
                onClick={stay}
                ref={stayButtonRef}
              >
                Stay
              </button>
              <button
                type="button"
                className="rounded-xl bg-rose-600 px-4 py-2 text-sm font-semibold text-white hover:bg-rose-500"
                onClick={discardAndLeave}
                ref={discardButtonRef}
              >
                Discard and leave
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </SettingsDraftGuardContext.Provider>
  );
}

export function useSettingsDraftGuard(): Pick<SettingsDraftGuardValue, 'requestDeparture'> {
  const { requestDeparture } = useContext(SettingsDraftGuardContext);
  return { requestDeparture };
}

export function useSettingsDraftRegistration(
  draftId: string,
  dirty: boolean,
  discard: () => void,
): void {
  const { registerDraft } = useContext(SettingsDraftGuardContext);
  const discardRef = useRef(discard);
  discardRef.current = discard;

  useEffect(() => {
    registerDraft(draftId, {
      dirty,
      discard: () => discardRef.current(),
    });
    return () => registerDraft(draftId, null);
  }, [dirty, draftId, registerDraft]);
}
