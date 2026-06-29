import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';

export type DashboardToastVariant = 'success' | 'error' | 'info';

export type DashboardToastAction = {
  label: string;
  href: string;
};

export type DashboardToastOptions = {
  title: string;
  message?: ReactNode;
  action?: DashboardToastAction;
  durationMs?: number;
};

type DashboardToastItem = Omit<DashboardToastOptions, 'durationMs'> & {
  id: string;
  variant: DashboardToastVariant;
  durationMs: number | null;
};

type DashboardToastContextValue = {
  success: (toast: DashboardToastOptions) => void;
  error: (toast: DashboardToastOptions) => void;
  info: (toast: DashboardToastOptions) => void;
  dismiss: (id: string) => void;
};

const DashboardToastContext = createContext<DashboardToastContextValue | null>(null);
const SUCCESS_DURATION_MS = 5000;
const INFO_DURATION_MS = 5000;
const ERROR_DURATION_MS = 15000;

function nextToastId() {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `toast-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function DashboardToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<DashboardToastItem[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const show = useCallback((variant: DashboardToastVariant, options: DashboardToastOptions) => {
    const defaultDuration =
      variant === 'error'
        ? ERROR_DURATION_MS
        : variant === 'info'
          ? INFO_DURATION_MS
          : SUCCESS_DURATION_MS;
    const durationMs: number | null =
      options.durationMs === 0 ? null : options.durationMs ?? defaultDuration;
    const toast: DashboardToastItem = {
      ...options,
      id: nextToastId(),
      variant,
      durationMs,
    };
    setToasts((current) => [...current, toast]);
  }, []);

  const value = useMemo<DashboardToastContextValue>(
    () => ({
      success: (toast) => show('success', toast),
      error: (toast) => show('error', toast),
      info: (toast) => show('info', toast),
      dismiss,
    }),
    [dismiss, show],
  );

  return (
    <DashboardToastContext.Provider value={value}>
      {children}
      <DashboardToastViewport toasts={toasts} onDismiss={dismiss} />
    </DashboardToastContext.Provider>
  );
}

export function useDashboardToast() {
  const context = useContext(DashboardToastContext);
  if (!context) {
    throw new Error('useDashboardToast must be used within DashboardToastProvider.');
  }
  return context;
}

function DashboardToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: DashboardToastItem[];
  onDismiss: (id: string) => void;
}) {
  if (!toasts.length) return null;
  return (
    <div className="dashboard-toast-viewport" aria-label="Notifications">
      {toasts.map((toast) => (
        <DashboardToast key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

function DashboardToast({
  toast,
  onDismiss,
}: {
  toast: DashboardToastItem;
  onDismiss: (id: string) => void;
}) {
  const [paused, setPaused] = useState(false);
  const remainingMs = useRef(toast.durationMs);
  const startedAt = useRef<number | null>(null);
  const titleId = `${toast.id}-title`;

  useEffect(() => {
    if (toast.durationMs === null || paused) return undefined;
    startedAt.current = Date.now();
    const timer = window.setTimeout(() => onDismiss(toast.id), remainingMs.current ?? toast.durationMs);
    return () => {
      window.clearTimeout(timer);
      if (startedAt.current !== null && remainingMs.current !== null) {
        remainingMs.current = Math.max(0, remainingMs.current - (Date.now() - startedAt.current));
      }
    };
  }, [onDismiss, paused, toast.durationMs, toast.id]);

  const role = toast.variant === 'error' ? 'alert' : 'status';
  const ariaLive = toast.variant === 'error' ? 'assertive' : 'polite';

  return (
    <section
      className={`dashboard-toast dashboard-toast--${toast.variant}`}
      role={role}
      aria-live={ariaLive}
      aria-labelledby={titleId}
      tabIndex={-1}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocusCapture={() => setPaused(true)}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) {
          setPaused(false);
        }
      }}
    >
      <div className="dashboard-toast__content">
        <p className="dashboard-toast__title" id={titleId}>
          {toast.title}
        </p>
        {toast.message ? <p className="dashboard-toast__message">{toast.message}</p> : null}
        {toast.action ? (
          <a className="dashboard-toast__action" href={toast.action.href}>
            {toast.action.label}
          </a>
        ) : null}
      </div>
      <button
        type="button"
        className="dashboard-toast__close"
        aria-label={`Dismiss ${toast.title}`}
        onClick={() => onDismiss(toast.id)}
      >
        <span aria-hidden="true">x</span>
      </button>
    </section>
  );
}
