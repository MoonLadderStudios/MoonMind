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
import { CheckCircle2, X, XCircle } from 'lucide-react';

export type DashboardToastVariant = 'success' | 'error' | 'info';

export type DashboardToastAction = {
  label: string;
  href: string;
};

export type DashboardToastOptions = {
  title: string;
  message?: string;
  action?: DashboardToastAction;
  durationMs?: number | null;
};

type DashboardToastItem = DashboardToastOptions & {
  id: string;
  variant: DashboardToastVariant;
};

type DashboardToastContextValue = {
  success: (options: DashboardToastOptions) => string;
  error: (options: DashboardToastOptions) => string;
  info: (options: DashboardToastOptions) => string;
  dismiss: (id: string) => void;
};

const DashboardToastContext = createContext<DashboardToastContextValue | null>(null);
const SUCCESS_TOAST_DURATION_MS = 5000;
let toastIdSequence = 0;

function createToastId(): string {
  toastIdSequence += 1;
  return `dashboard-toast-${toastIdSequence}`;
}

export function DashboardToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<DashboardToastItem[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const addToast = useCallback((variant: DashboardToastVariant, options: DashboardToastOptions) => {
    const id = createToastId();
    const durationMs =
      options.durationMs === undefined
        ? variant === 'success' || variant === 'info'
          ? SUCCESS_TOAST_DURATION_MS
          : null
        : options.durationMs;
    setToasts((current) => [
      ...current,
      {
        ...options,
        durationMs,
        id,
        variant,
      },
    ]);
    return id;
  }, []);

  const value = useMemo<DashboardToastContextValue>(
    () => ({
      success: (options) => addToast('success', options),
      error: (options) => addToast('error', options),
      info: (options) => addToast('info', options),
      dismiss,
    }),
    [addToast, dismiss],
  );

  return (
    <DashboardToastContext.Provider value={value}>
      {children}
      <DashboardToastViewport toasts={toasts} onDismiss={dismiss} />
    </DashboardToastContext.Provider>
  );
}

export function useDashboardToast(): DashboardToastContextValue {
  const context = useContext(DashboardToastContext);
  if (!context) {
    throw new Error('useDashboardToast must be used within DashboardToastProvider');
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
  if (toasts.length === 0) return null;
  return (
    <div className="dashboard-toast-viewport" aria-label="Dashboard notifications">
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
  const [isPaused, setIsPaused] = useState(false);
  const remainingMs = useRef(toast.durationMs ?? null);
  const startedAt = useRef<number | null>(null);
  const timer = useRef<number | null>(null);
  const isError = toast.variant === 'error';

  const clearTimer = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  useEffect(() => {
    if (remainingMs.current === null || isPaused) return clearTimer;
    startedAt.current = Date.now();
    timer.current = window.setTimeout(() => {
      onDismiss(toast.id);
    }, remainingMs.current);
    return clearTimer;
  }, [clearTimer, isPaused, onDismiss, toast.id]);

  const pause = () => {
    if (remainingMs.current !== null && startedAt.current !== null) {
      remainingMs.current = Math.max(0, remainingMs.current - (Date.now() - startedAt.current));
    }
    setIsPaused(true);
  };
  const resume = () => setIsPaused(false);

  return (
    <section
      className={`dashboard-toast dashboard-toast--${toast.variant}`}
      role={isError ? 'alert' : 'status'}
      aria-live={isError ? 'assertive' : 'polite'}
      onMouseEnter={pause}
      onMouseLeave={resume}
      onFocus={pause}
      onBlur={resume}
    >
      <div className="dashboard-toast__icon" aria-hidden="true">
        {isError ? <XCircle size={18} /> : <CheckCircle2 size={18} />}
      </div>
      <div className="dashboard-toast__content">
        <p className="dashboard-toast__title">{toast.title}</p>
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
        <X size={16} aria-hidden="true" />
      </button>
    </section>
  );
}

