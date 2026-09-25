import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

// The toast provider and the toast itself are always used as a pair, so they
// live in one file: ToastProvider owns the state, Toast renders it.
export const ToastContext = createContext(null);

const AUTO_DISMISS_MS = 4000;

export function ToastProvider({ children }) {
  const [toast, setToast] = useState(null);
  const timerRef = useRef(null);

  const dismiss = useCallback(() => {
    clearTimeout(timerRef.current);
    setToast(null);
  }, []);

  const showToast = useCallback((message, tone = 'info') => {
    clearTimeout(timerRef.current);
    // A new id restarts the enter animation even when the text is identical.
    setToast({ id: Date.now(), message, tone });
  }, []);

  // Effects are for syncing with things outside React -- here, a timer. The
  // cleanup return value is what prevents a stale timer dismissing a newer toast.
  useEffect(() => {
    if (!toast) return undefined;
    timerRef.current = setTimeout(() => setToast(null), AUTO_DISMISS_MS);
    return () => clearTimeout(timerRef.current);
  }, [toast]);

  // useMemo keeps the context value stable, so consumers don't re-render on
  // every parent render for no reason.
  const value = useMemo(() => ({ toast, showToast, dismiss }), [toast, showToast, dismiss]);

  return <ToastContext.Provider value={value}>{children}</ToastContext.Provider>;
}

// Mounted once at the app root, outside the router outlet, so navigation never
// unmounts it mid-flight.
export function Toast() {
  const { toast, dismiss } = useContext(ToastContext);
  if (!toast) return null;

  const isError = toast.tone === 'error';

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed bottom-6 left-1/2 z-50 w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2"
    >
      <div
        key={toast.id}
        className={`animate-toast-in flex items-start gap-3 rounded-[var(--radius-control)] border px-4 py-3 shadow-lg shadow-black/5 ${
          isError
            ? 'border-critical/30 bg-critical text-white'
            : 'border-border bg-ink text-white'
        }`}
      >
        <span
          aria-hidden="true"
          className={`mt-[7px] size-2 shrink-0 rounded-full ${isError ? 'bg-white' : 'bg-healthy'}`}
        />
        <p className="flex-1 text-sm leading-relaxed">{toast.message}</p>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss notification"
          className="-mr-1 shrink-0 rounded px-1 text-lg leading-none text-white/60 transition hover:text-white"
        >
          &times;
        </button>
      </div>
    </div>
  );
}
