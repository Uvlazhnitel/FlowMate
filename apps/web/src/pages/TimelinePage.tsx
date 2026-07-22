import { useQuery } from "@tanstack/react-query";
import { CalendarDays, History, UserRound } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import {
  getSettingsPeople,
  getSettingsTopics,
  getTimeline,
  remainingKeys,
} from "../api/remaining";
import { OperationalLayout } from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { formatDateTime, type DateTimePreferences } from "../lib/dates";

const eventLabels: Record<string, string> = {
  created: "Создано",
  converted_from_draft: "Создано из draft",
  completed: "Завершено",
  reopened: "Возвращено",
  cancelled: "Отменено",
  rescheduled: "Перенесено",
  snoozed: "Напоминание отложено",
  note_added: "Добавлена заметка",
  topic_changed: "Изменена тема",
  person_changed: "Изменены люди",
  waiting_received: "Ожидание получено",
  planner_status_changed: "Изменён Planner status",
  archived: "Архивировано",
};

const workItemTypes = [
  "task",
  "follow_up",
  "waiting",
  "question",
  "decision",
  "agenda_item",
];

export function TimelinePage({
  dateTimePreferences,
}: {
  dateTimePreferences: DateTimePreferences;
}) {
  const [params, setParams] = useSearchParams();
  const page = Number(params.get("page") ?? 0);
  const query = useQuery({
    queryKey: [...remainingKeys.all, "timeline", params.toString(), page],
    queryFn: () => getTimeline(params, page * 30),
  });
  const options = useQuery({
    queryKey: [...remainingKeys.all, "timeline-options"],
    queryFn: async () => {
      const [topics, people] = await Promise.all([
        getSettingsTopics(),
        getSettingsPeople(),
      ]);
      return { topics: topics.items, people: people.items };
    },
  });
  function update(name: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    next.delete("page");
    setParams(next);
  }
  if (query.isPending) return <LoadingState label="Собираем хронологию" />;
  if (query.isError)
    return (
      <ErrorState
        title="Не удалось загрузить Timeline"
        onRetry={() => void query.refetch()}
      />
    );
  return (
    <OperationalLayout
      eyebrow="История"
      title="Timeline"
      description="Безопасная хронология рабочих изменений без AI payloads и технических логов."
      controls={
        <button className="button button--secondary" onClick={() => setParams({})}>
          Сбросить фильтры
        </button>
      }
    >
      <div className="timeline-filters" aria-label="Фильтры Timeline">
        <label>
          С
          <input
            type="date"
            value={params.get("from") ?? ""}
            onChange={(event) => update("from", event.target.value)}
          />
        </label>
        <label>
          По
          <input
            type="date"
            value={params.get("to") ?? ""}
            onChange={(event) => update("to", event.target.value)}
          />
        </label>
        <label>
          Событие
          <select
            value={params.get("event_type") ?? ""}
            onChange={(event) => update("event_type", event.target.value)}
          >
            <option value="">Все</option>
            {Object.entries(eventLabels).map(([value, label]) => (
              <option value={value} key={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Тип записи
          <select
            value={params.get("work_item_type") ?? ""}
            onChange={(event) => update("work_item_type", event.target.value)}
          >
            <option value="">Все</option>
            {workItemTypes.map((value) => (
              <option value={value} key={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          Тема
          <select
            value={params.get("topic_id") ?? ""}
            onChange={(event) => update("topic_id", event.target.value)}
          >
            <option value="">Все</option>
            {options.data?.topics.map((topic) => (
              <option value={topic.id} key={topic.id}>
                {topic.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Человек
          <select
            value={params.get("person_id") ?? ""}
            onChange={(event) => update("person_id", event.target.value)}
          >
            <option value="">Все</option>
            {options.data?.people.map((person) => (
              <option value={person.id} key={person.id}>
                {person.display_name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {!query.data.items.length ? (
        <EmptyState title="Событий не найдено" description="Измените период или фильтры." />
      ) : (
        <div className="timeline-list">
          {query.data.items.map((event) => (
            <article className="timeline-entry" key={event.id}>
              <span className="timeline-entry__icon">
                <History size={17} />
              </span>
              <div>
                <span className="directory-kicker">
                  {eventLabels[event.event_type] ?? event.event_type}
                </span>
                <h2>{event.title}</h2>
                <div className="timeline-entry__meta">
                  <span>
                    <CalendarDays size={14} />{" "}
                    {formatDateTime(event.occurred_at, dateTimePreferences)}
                  </span>
                  {event.topic && <span>#{event.topic.name}</span>}
                  {event.people.length > 0 && (
                    <span>
                      <UserRound size={14} />{" "}
                      {event.people.map((person) => person.display_name).join(", ")}
                    </span>
                  )}
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
      <div className="pager">
        <button
          className="button button--secondary"
          disabled={page === 0}
          onClick={() => {
            const next = new URLSearchParams(params);
            next.set("page", String(page - 1));
            setParams(next);
          }}
        >
          Назад
        </button>
        <button
          className="button button--secondary"
          disabled={!query.data.has_more}
          onClick={() => {
            const next = new URLSearchParams(params);
            next.set("page", String(page + 1));
            setParams(next);
          }}
        >
          Дальше
        </button>
      </div>
    </OperationalLayout>
  );
}
