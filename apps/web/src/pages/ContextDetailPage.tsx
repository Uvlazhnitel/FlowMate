import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";

import {
  getPerson,
  getPersonContent,
  getTopic,
  getTopicContent,
  operationsKeys,
  type ActivityEntry,
  type NamedEntry,
  type NoteEntry,
  type WorkItemCardData,
} from "../api/operations";
import { LoadMoreButton, OperationalLayout } from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { WorkItemCard } from "../components/WorkItemCard";
import { formatRelative } from "../lib/dates";

const topicSections = [
  ["active", "Активные записи"],
  ["people", "Люди"],
  ["notes", "Заметки"],
  ["decisions", "Решения"],
  ["history", "История"],
] as const;
const personSections = [
  ["follow_ups", "Follow-up"],
  ["waiting", "Ожидания"],
  ["questions", "Вопросы"],
  ["topics", "Темы"],
  ["notes", "Заметки"],
  ["history", "История"],
] as const;

export function ContextDetailPage({
  kind,
  timezone,
}: {
  kind: "topic" | "person";
  timezone: string;
}) {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const sections = kind === "topic" ? topicSections : personSections;
  const section = params.get("section") ?? sections[0][0];
  const detail = useQuery({
    queryKey: [...operationsKeys.all, kind, id],
    queryFn: async () => {
      if (kind === "topic") {
        const value = await getTopic(id);
        return { name: value.name, description: value.description };
      }
      const value = await getPerson(id);
      return { name: value.display_name, description: value.role };
    },
  });
  const content = useInfiniteQuery({
    queryKey: [...operationsKeys.all, kind, id, section],
    queryFn: ({ pageParam }) =>
      kind === "topic"
        ? getTopicContent<unknown>(id, section, pageParam)
        : getPersonContent<unknown>(id, section, pageParam),
    initialPageParam: 0,
    getNextPageParam: (page) => (page.has_more ? page.offset + page.limit : undefined),
  });
  if (detail.isPending || content.isPending)
    return <LoadingState label="Собираем контекст" />;
  if (detail.isError || content.isError)
    return (
      <ErrorState
        title="Не удалось загрузить детали"
        onRetry={() => void Promise.all([detail.refetch(), content.refetch()])}
      />
    );
  const { name, description } = detail.data;
  const items = content.data.pages.flatMap((page) => page.items);
  const workSections = new Set([
    "active",
    "decisions",
    "follow_ups",
    "waiting",
    "questions",
  ]);
  return (
    <OperationalLayout
      eyebrow={kind === "topic" ? "Тема" : "Человек"}
      title={name}
      description={description ?? "Связанный рабочий контекст."}
    >
      <div className="section-tabs" role="navigation" aria-label="Разделы деталей">
        {sections.map(([key, label]) => (
          <button
            className={section === key ? "section-tab section-tab--active" : "section-tab"}
            key={key}
            onClick={() => setParams({ section: key })}
          >
            {label}
          </button>
        ))}
      </div>
      {!items.length ? (
        <EmptyState
          title="Здесь пока пусто"
          description="Связанные данные появятся после следующих действий."
        />
      ) : workSections.has(section) ? (
        <div className="work-list">
          {(items as WorkItemCardData[]).map((item) => (
            <WorkItemCard key={item.id} item={item} timezone={timezone} />
          ))}
        </div>
      ) : section === "notes" ? (
        <div className="notes-list">
          {(items as NoteEntry[]).map((note) => (
            <article className="note-card" key={note.id}>
              <p>{note.content}</p>
              <span>{formatRelative(note.created_at, timezone)}</span>
            </article>
          ))}
        </div>
      ) : section === "history" ? (
        <div className="activity-list">
          {(items as ActivityEntry[]).map((event) => (
            <article className="activity-row" key={event.id}>
              <div>
                <strong>{event.title}</strong>
                <span>{event.event_type.replaceAll("_", " ")}</span>
              </div>
              <time>{formatRelative(event.created_at, timezone)}</time>
            </article>
          ))}
        </div>
      ) : (
        <div className="named-list">
          {(items as NamedEntry[]).map((entry) => (
            <Link
              to={`/${section === "people" ? "people" : "topics"}/${entry.id}`}
              key={entry.id}
            >
              <strong>{entry.name}</strong>
              <span>{entry.subtitle}</span>
            </Link>
          ))}
        </div>
      )}
      {content.hasNextPage && (
        <LoadMoreButton
          loading={content.isFetchingNextPage}
          onClick={() => void content.fetchNextPage()}
        />
      )}
    </OperationalLayout>
  );
}
