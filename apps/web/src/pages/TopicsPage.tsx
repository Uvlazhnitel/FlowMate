import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CalendarClock } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";

import { getTopics, operationsKeys } from "../api/operations";
import { OperationalLayout } from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { formatDateTime, type DateTimePreferences } from "../lib/dates";

export function TopicsPage({
  dateTimePreferences,
}: {
  dateTimePreferences: DateTimePreferences;
}) {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const page = Number(params.get("page") ?? 0);
  const query = useQuery({
    queryKey: [...operationsKeys.all, "topics", q, page],
    queryFn: () => getTopics(q, page * 20),
  });
  if (query.isPending) return <LoadingState label="Загружаем темы" />;
  if (query.isError)
    return (
      <ErrorState title="Не удалось загрузить темы" onRetry={() => void query.refetch()} />
    );
  return (
    <OperationalLayout
      eyebrow="Контекст"
      title="Темы"
      description="Направления работы, собранные вокруг фактов, людей и следующих шагов."
      controls={
        <input
          className="search-field"
          aria-label="Поиск тем"
          placeholder="Найти тему"
          value={q}
          onChange={(event) =>
            setParams(event.target.value ? { q: event.target.value } : {})
          }
        />
      }
    >
      {!query.data.items.length ? (
        <EmptyState
          title="Темы не найдены"
          description="Измените запрос или добавьте тему через Telegram."
        />
      ) : (
        <div className="directory-grid">
          {query.data.items.map((topic) => (
            <Link className="directory-card" to={`/topics/${topic.id}`} key={topic.id}>
              <div>
                <span className="directory-kicker">{topic.open_count} открыто</span>
                <h2>{topic.name}</h2>
                <p>{topic.description ?? "Контекст собирается из связанных записей."}</p>
              </div>
              <div className="metric-row">
                <span>{topic.overdue_count} просрочено</span>
                <span>{topic.follow_up_count} follow-up</span>
                <span>{topic.waiting_count} ожиданий</span>
              </div>
              <footer>
                <span>
                  <CalendarClock size={15} aria-hidden />{" "}
                  {formatDateTime(topic.next_deadline, dateTimePreferences)}
                </span>
                <ArrowRight size={18} aria-hidden />
              </footer>
            </Link>
          ))}
        </div>
      )}
      <div className="pager">
        <button
          className="button button--secondary"
          disabled={page === 0}
          onClick={() => setParams({ ...(q ? { q } : {}), page: String(page - 1) })}
        >
          Назад
        </button>
        <button
          className="button button--secondary"
          disabled={!query.data.has_more}
          onClick={() => setParams({ ...(q ? { q } : {}), page: String(page + 1) })}
        >
          Дальше
        </button>
      </div>
    </OperationalLayout>
  );
}
