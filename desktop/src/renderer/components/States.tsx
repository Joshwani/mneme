import type { ReactNode } from "react";

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return (
    <div className="state-card">
      <span className="spinner" />
      <p>{label}…</p>
    </div>
  );
}

export function ErrorState({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  return (
    <div className="state-card error-state">
      <span className="state-icon">!</span>
      <div>
        <strong>Something went wrong</strong>
        <p>{error instanceof Error ? error.message : String(error)}</p>
      </div>
      {retry && (
        <button className="button secondary" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <div className="state-card empty-state">
      <span className="state-icon">○</span>
      <strong>{title}</strong>
      <p>{detail}</p>
      {action}
    </div>
  );
}
