import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, CalendarCheck, History } from "lucide-react";
import { Link } from "react-router-dom";

import { getDashboard, operationsKeys } from "../api/operations";
import { OperationalLayout, SectionHeading } from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { WorkItemCard } from "../components/WorkItemCard";
import { formatRelative, type DateTimePreferences } from "../lib/dates";

const summaryDefinitions = [
  ["inbox", "Входящие", "/inbox"],
  ["overdue", "Просрочено", "/today?section=overdue"],
  ["due_today", "На сегодня", "/today?section=due_today"],
  ["follow_ups", "Фоллоу-апы", "/today?section=follow_ups"],
  ["waiting_overdue", "Ждём ответа", "/today?section=waiting"],
  ["questions", "Вопросы", "/today?section=questions"],
  ["planner_queue", "Очередь Planner", "/planner-queue"],
] as const;

export function DashboardPage({
  dateTimePreferences,
}: {
  dateTimePreferences: DateTimePreferences;
}) {
  const query = useQuery({ queryKey: operationsKeys.dashboard, queryFn: getDashboard });
  if (query.isPending) return <LoadingState label="Собираем обзор" />;
  if (query.isError)
    return (
      <ErrorState title="Не удалось загрузить обзор" onRetry={() => void query.refetch()} />
    );
  const data = query.data;
  return (
    <OperationalLayout
      eyebrow="Фокус дня"
      title="Панель"
      description="Сначала разобрать входящее, потом сделать главное на сегодня."
    >
      <div className="summary-grid">
        {summaryDefinitions.map(([key, label, to]) => (
          <Link
            className={`summary-card ${key === "overdue" ? "summary-card--urgent" : ""}`}
            to={to}
            key={key}
          >
            <span>{label}</span>
            <strong>{data.summary[key]}</strong>
            <ArrowUpRight size={17} aria-hidden />
          </Link>
        ))}
      </div>
      <div className="dashboard-layout">
        <div className="dashboard-main">
          <SectionHeading title="Главное сейчас" count={data.recommended.length} />
          {data.recommended.length ? (
            <div className="work-list">
              {data.recommended.map((item) => (
                <WorkItemCard
                  key={item.id}
                  item={item}
                  dateTimePreferences={dateTimePreferences}
                />
              ))}
            </div>
          ) : (
            <EmptyState
              title="Главное на сегодня разобрано"
              description="Срочных записей сейчас нет, можно вернуться к входящему или планированию."
            />
          )}
        </div>
        <aside className="dashboard-rail">
          <section className="rail-panel">
            <SectionHeading title="Ближайшие сроки" />
            {data.deadlines.length ? (
              data.deadlines.map((item) => (
                <div className="compact-row" key={item.id}>
                  <CalendarCheck size={16} aria-hidden />
                  <div>
                    <strong>{item.title}</strong>
                    <span>{item.topic_name ?? "Без темы"}</span>
                  </div>
                </div>
              ))
            ) : (
              <p className="muted-copy">Жёстких сроков рядом нет.</p>
            )}
          </section>
          <section className="rail-panel">
            <SectionHeading title="Вторичный контур" />
            <div className="compact-row">
              <ArrowUpRight size={16} aria-hidden />
              <div>
                <strong>Очередь Planner</strong>
                <span>{data.summary.planner_queue} записей ждут ручной передачи</span>
              </div>
            </div>
            {data.activity.length ? (
              data.activity.map((event) => (
                <div className="compact-row" key={event.id}>
                  <History size={16} aria-hidden />
                  <div>
                    <strong>{event.title}</strong>
                    <span>{formatRelative(event.created_at, dateTimePreferences)}</span>
                  </div>
                </div>
              ))
            ) : (
              <p className="muted-copy">Свежих изменений пока нет.</p>
            )}
          </section>
        </aside>
      </div>
    </OperationalLayout>
  );
}
