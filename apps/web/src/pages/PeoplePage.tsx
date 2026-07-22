import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";

import { getPeople, operationsKeys } from "../api/operations";
import { OperationalLayout } from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { formatRelative, type DateTimePreferences } from "../lib/dates";

export function PeoplePage({
  dateTimePreferences,
}: {
  dateTimePreferences: DateTimePreferences;
}) {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const page = Number(params.get("page") ?? 0);
  const query = useQuery({
    queryKey: [...operationsKeys.all, "people", q, page],
    queryFn: () => getPeople(q, page * 20),
  });
  if (query.isPending) return <LoadingState label="Загружаем людей" />;
  if (query.isError)
    return (
      <ErrorState title="Не удалось загрузить людей" onRetry={() => void query.refetch()} />
    );
  return (
    <OperationalLayout
      eyebrow="Связи"
      title="Люди"
      description="Кому написать, от кого ждём и какие вопросы ещё открыты."
      controls={
        <input
          className="search-field"
          aria-label="Поиск людей"
          placeholder="Имя или роль"
          value={q}
          onChange={(event) =>
            setParams(event.target.value ? { q: event.target.value } : {})
          }
        />
      }
    >
      {!query.data.items.length ? (
        <EmptyState
          title="Люди не найдены"
          description="Измените запрос или создайте связь через Telegram."
        />
      ) : (
        <div className="directory-grid directory-grid--people">
          {query.data.items.map((person) => (
            <Link
              className="directory-card person-card"
              to={`/people/${person.id}`}
              key={person.id}
            >
              <div className="person-card__head">
                <span className="person-monogram">
                  {person.display_name.slice(0, 2).toUpperCase()}
                </span>
                <div>
                  <h2>{person.display_name}</h2>
                  <p>{person.role ?? "Роль не указана"}</p>
                </div>
              </div>
              <div className="metric-row">
                <span>{person.follow_up_count} follow-up</span>
                <span>{person.waiting_count} ожиданий</span>
                <span>{person.question_count} вопросов</span>
              </div>
              <footer>
                <span>
                  Активность: {formatRelative(person.last_activity, dateTimePreferences)}
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
