import type { ReactNode } from "react";

export function LoadingView({ label = "Carregando…" }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <p>{label}</p>
    </div>
  );
}

export function ErrorView({
  title = "Não foi possível carregar",
  message,
  onRetry,
  children,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
  children?: ReactNode;
}) {
  return (
    <div className="state state-error" role="alert">
      <h2>{title}</h2>
      <p>{message}</p>
      {onRetry ? (
        <button type="button" className="button" onClick={onRetry}>
          Tentar novamente
        </button>
      ) : null}
      {children}
    </div>
  );
}

export function EmptyView({ title, message }: { title: string; message: string }) {
  return (
    <div className="state">
      <h2>{title}</h2>
      <p>{message}</p>
    </div>
  );
}
