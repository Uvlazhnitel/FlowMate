import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CalendarDays, CalendarRange, Inbox } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import {
  getOverview,
  operationsKeys,
  type OverviewColumn as OverviewColumnData,
  type OverviewInboxItem,
  type OverviewWorkItem,
} from "../api/operations";
import { OperationalLayout } from "../components/OperationalLayout";
import { ErrorState, LoadingState } from "../components/PageState";
import type { DateTimePreferences } from "../lib/dates";
import { reasonLabels } from "./inbox/presentation";
import { OverviewWorkItemRow } from "./overview/OverviewWorkItemRow";

const inboxKindLabels: Record<OverviewInboxItem["kind"], string> = {
  draft: "Черновик AI",
  work_item: "Задача",
  note: "Заметка",
};

function footerLabel(total: number) {
  return total > 8 ? `Показать все ${total}` : "Открыть раздел";
}

function OverviewColumn({
  title,
  icon,
  data,
  to,
  className,
  empty,
  children,
}: {
  title: string;
  icon: ReactNode;
  data: OverviewColumnData<unknown>;
  to: string;
  className: string;
  empty: string;
  children: ReactNode;
}) {
  return (
    <section
      className={`overview-column ${className}`}
      aria-labelledby={`${className}-title`}
    >
      <header className="overview-column__header">
        <span className="overview-column__icon">{icon}</span>
        <h2 id={`${className}-title`}>{title}</h2>
        <span className="overview-column__count" aria-label={`${data.total} записей`}>
          {data.total}
        </span>
      </header>
      <div className="overview-column__list">
        {data.items.length ? children : <p className="overview-column__empty">{empty}</p>}
      </div>
      <Link className="overview-column__footer" to={to}>
        {footerLabel(data.total)} <ArrowRight size={15} aria-hidden />
      </Link>
    </section>
  );
}

function OverviewInboxRow({ item }: { item: OverviewInboxItem }) {
  const reason = item.reasons[0];
  return (
    <Link className="overview-inbox-row" to={`/inbox?kind=${item.kind}&focus=${item.id}`}>
      <div className="overview-row__badges">
        <span className="overview-badge">{inboxKindLabels[item.kind]}</span>
        {item.item_count > 1 && (
          <span className="overview-badge">{item.item_count} записи</span>
        )}
      </div>
      <h3 title={item.title}>{item.title}</h3>
      {item.excerpt && <p>{item.excerpt}</p>}
      {reason && (
        <span className="overview-inbox-row__reason">{reasonLabels[reason] ?? reason}</span>
      )}
    </Link>
  );
}

export function OverviewPage({
  dateTimePreferences,
}: {
  dateTimePreferences: DateTimePreferences;
}) {
  const query = useQuery({ queryKey: operationsKeys.overview, queryFn: getOverview });

  return (
    <OperationalLayout
      eyebrow="Главное"
      title="Обзор"
      description="Сегодня, завтра и входящие — в одном спокойном рабочем пространстве."
    >
      {query.isPending ? (
        <LoadingState label="Собираем обзор" />
      ) : query.isError ? (
        <ErrorState
          title="Не удалось загрузить обзор"
          onRetry={() => void query.refetch()}
        />
      ) : (
        <div className="overview-grid">
          <OverviewColumn
            title="Сегодня"
            icon={<CalendarDays size={18} aria-hidden />}
            data={query.data.today}
            to="/today"
            className="overview-column--today"
            empty="На сегодня всё разобрано."
          >
            {query.data.today.items.map((entry: OverviewWorkItem) => (
              <OverviewWorkItemRow
                key={entry.item.id}
                entry={entry}
                dateTimePreferences={dateTimePreferences}
              />
            ))}
          </OverviewColumn>
          <OverviewColumn
            title="Завтра"
            icon={<CalendarRange size={18} aria-hidden />}
            data={query.data.tomorrow}
            to="/tomorrow"
            className="overview-column--tomorrow"
            empty="На завтра ничего не запланировано."
          >
            {query.data.tomorrow.items.map((entry: OverviewWorkItem) => (
              <OverviewWorkItemRow
                key={entry.item.id}
                entry={entry}
                dateTimePreferences={dateTimePreferences}
              />
            ))}
          </OverviewColumn>
          <OverviewColumn
            title="Входящие"
            icon={<Inbox size={18} aria-hidden />}
            data={query.data.inbox}
            to="/inbox"
            className="overview-column--inbox"
            empty="Входящие разобраны."
          >
            {query.data.inbox.items.map((item: OverviewInboxItem) => (
              <OverviewInboxRow key={`${item.kind}-${item.id}`} item={item} />
            ))}
          </OverviewColumn>
        </div>
      )}
    </OperationalLayout>
  );
}
