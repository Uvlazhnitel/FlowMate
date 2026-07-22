import type { ReactNode } from "react";

export function OperationalLayout({
  eyebrow,
  title,
  description,
  controls,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  controls?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="page operational-page" aria-labelledby="page-title">
      <header className="operational-heading">
        <div>
          <span className="eyebrow">{eyebrow}</span>
          <h1 id="page-title">{title}</h1>
          <p>{description}</p>
        </div>
        {controls && <div className="page-controls">{controls}</div>}
      </header>
      {children}
    </section>
  );
}

export function SectionHeading({ title, count }: { title: string; count?: number }) {
  return (
    <div className="section-heading">
      <h2>{title}</h2>
      {count !== undefined && <span>{count}</span>}
    </div>
  );
}

export function LoadMoreButton({
  loading,
  onClick,
}: {
  loading: boolean;
  onClick: () => void;
}) {
  return (
    <div className="load-more">
      <button
        className="button button--secondary"
        type="button"
        disabled={loading}
        onClick={onClick}
      >
        {loading ? "Загружаем…" : "Показать ещё"}
      </button>
    </div>
  );
}
