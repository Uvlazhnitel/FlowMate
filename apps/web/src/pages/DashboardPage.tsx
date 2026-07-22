import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, CalendarCheck, History } from "lucide-react";
import { Link } from "react-router-dom";

import { getDashboard, operationsKeys } from "../api/operations";
import { OperationalLayout, SectionHeading } from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { WorkItemCard } from "../components/WorkItemCard";
import { formatRelative, type DateTimePreferences } from "../lib/dates";

const summaryDefinitions = [
  ["overdue", "Просрочено", "/today?section=overdue"],
  ["due_today", "На сегодня", "/today?section=due_today"],
  ["follow_ups", "Follow-up", "/today?section=follow_ups"],
  ["waiting_overdue", "Ждём дольше срока", "/today?section=waiting"],
  ["questions", "Открытые вопросы", "/today?section=questions"],
  ["inbox", "Inbox", "/inbox"],
  ["planner_queue", "Planner Queue", "/planner-queue"],
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
      eyebrow="Командный центр"
      title="Обзор"
      description="Рабочий день без слепых зон: сначала то, что действительно требует внимания."
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
          <SectionHeading title="Следующие действия" count={data.recommended.length} />
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
              title="Всё спокойно"
              description="Срочных рекомендаций сейчас нет."
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
              <p className="muted-copy">Нет ближайших сроков.</p>
            )}
          </section>
          <section className="rail-panel">
            <SectionHeading title="Последняя активность" />
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
              <p className="muted-copy">История пока пуста.</p>
            )}
          </section>
        </aside>
      </div>
    </OperationalLayout>
  );
}
