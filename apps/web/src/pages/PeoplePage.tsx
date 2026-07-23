import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";

import { getPeople, operationsKeys, type PeopleScope } from "../api/operations";
import { OperationalLayout } from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { formatRelative, type DateTimePreferences } from "../lib/dates";

const scopes: { value: PeopleScope; label: string }[] = [
  { value: "work", label: "В работе" },
  { value: "recent", label: "Недавние" },
  { value: "all", label: "Все" },
];

function peopleSearchParams(q: string, scope: PeopleScope, page = 0) {
  return {
    ...(q ? { q } : {}),
    scope,
    ...(page ? { page: String(page) } : {}),
  };
}

export function PeoplePage({
  dateTimePreferences,
}: {
  dateTimePreferences: DateTimePreferences;
}) {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const requestedScope = params.get("scope");
  const scope: PeopleScope = scopes.some(({ value }) => value === requestedScope)
    ? (requestedScope as PeopleScope)
    : "work";
  const page = Number(params.get("page") ?? 0);
  const query = useQuery({
    queryKey: [...operationsKeys.all, "people", scope, q, page],
    queryFn: () => getPeople(q, page * 20, scope),
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
          onChange={(event) => setParams(peopleSearchParams(event.target.value, scope))}
        />
      }
    >
      <div className="section-tabs" role="navigation" aria-label="Фильтр людей">
        {scopes.map(({ value, label }) => (
          <button
            className={scope === value ? "section-tab section-tab--active" : "section-tab"}
            aria-pressed={scope === value}
            key={value}
            onClick={() => setParams(peopleSearchParams(q, value))}
          >
            {label}
          </button>
        ))}
      </div>
      {!query.data.items.length ? (
        <EmptyState
          title={q ? "Люди не найдены" : "В этом списке пока никого нет"}
          description={
            q
              ? "Измените запрос или выберите другой раздел."
              : scope === "work"
                ? "Люди появятся здесь, когда с ними будет связана открытая работа."
                : scope === "recent"
                  ? "За последние 90 дней активности с людьми не было."
                  : "Создайте связь через Telegram или настройки."
          }
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
                <span>{person.open_item_count} открытых</span>
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
          onClick={() => setParams(peopleSearchParams(q, scope, page - 1))}
        >
          Назад
        </button>
        <button
          className="button button--secondary"
          disabled={!query.data.has_more}
          onClick={() => setParams(peopleSearchParams(q, scope, page + 1))}
        >
          Дальше
        </button>
      </div>
    </OperationalLayout>
  );
}
