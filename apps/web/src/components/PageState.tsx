import { AlertTriangle, Inbox, LoaderCircle, RotateCcw } from "lucide-react";

interface StateProps {
  title?: string;
  description?: string;
  fullPage?: boolean;
}

export function LoadingState({
  label,
  fullPage = false,
}: {
  label: string;
  fullPage?: boolean;
}) {
  return (
    <div className={`page-state ${fullPage ? "page-state--full" : ""}`} role="status">
      <LoaderCircle className="spin" aria-hidden="true" />
      <p>{label}…</p>
    </div>
  );
}

export function ErrorState({
  title = "Что-то пошло не так",
  description = "Попробуйте ещё раз.",
  onRetry,
  fullPage = false,
}: StateProps & { onRetry: () => void }) {
  return (
    <div className={`page-state ${fullPage ? "page-state--full" : ""}`} role="alert">
      <AlertTriangle aria-hidden="true" />
      <h2>{title}</h2>
      <p>{description}</p>
      <button className="button button--secondary" type="button" onClick={onRetry}>
        <RotateCcw size={16} aria-hidden="true" />
        Повторить
      </button>
    </div>
  );
}

export function EmptyState({
  title,
  description,
}: Required<Pick<StateProps, "title" | "description">>) {
  return (
    <div className="empty-state">
      <span className="empty-state__icon">
        <Inbox aria-hidden="true" />
      </span>
      <h2>{title}</h2>
      <p>{description}</p>
    </div>
  );
}
