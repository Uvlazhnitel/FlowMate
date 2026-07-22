import { useInfiniteQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { getAgenda, operationsKeys } from "../api/operations";
import {
  LoadMoreButton,
  OperationalLayout,
  SectionHeading,
} from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { WorkItemCard } from "../components/WorkItemCard";
import type { DateTimePreferences } from "../lib/dates";

export function AgendaPage({
  dateTimePreferences,
  defaultSnoozeMinutes,
}: {
  dateTimePreferences: DateTimePreferences;
  defaultSnoozeMinutes: number;
}) {
  const [params, setParams] = useSearchParams();
  const groupKind = params.get("group_kind") ?? "";
  const query = useInfiniteQuery({
    queryKey: [...operationsKeys.all, "agenda", groupKind],
    queryFn: ({ pageParam }) => getAgenda(groupKind, pageParam),
    initialPageParam: 0,
    getNextPageParam: (page) => (page.has_more ? page.offset + page.limit : undefined),
  });
  if (query.isPending) return <LoadingState label="Собираем повестку" />;
  if (query.isError)
    return (
      <ErrorState
        title="Не удалось загрузить повестку"
        onRetry={() => void query.refetch()}
      />
    );
  const items = query.data.pages.flatMap((page) => page.items);
  const groups = new Map<string, typeof items>();
  for (const entry of items) {
    const key = `${entry.group_kind}:${entry.group_id ?? "none"}:${entry.group_label}`;
    groups.set(key, [...(groups.get(key) ?? []), entry]);
  }
  return (
    <OperationalLayout
      eyebrow="Разговоры"
      title="Повестка"
      description="Вопросы и темы, которые важно не забыть в следующем разговоре."
      controls={
        <select
          aria-label="Группа повестки"
          value={groupKind}
          onChange={(event) =>
            setParams(event.target.value ? { group_kind: event.target.value } : {})
          }
        >
          <option value="">Все группы</option>
          <option value="person">По людям</option>
          <option value="topic">По темам</option>
          <option value="unassigned">Без привязки</option>
        </select>
      }
    >
      {!items.length ? (
        <EmptyState
          title="Повестка пуста"
          description="Открытые вопросы и agenda items появятся здесь автоматически."
        />
      ) : (
        <div className="agenda-groups">
          {Array.from(groups.entries()).map(([key, entries]) => {
            const first = entries[0];
            if (!first) return null;
            return (
              <section className="agenda-group" key={key}>
                <SectionHeading title={first.group_label} count={entries.length} />
                <div className="work-list">
                  {entries.map(({ item }) => (
                    <WorkItemCard
                      key={item.id}
                      item={item}
                      dateTimePreferences={dateTimePreferences}
                      agenda
                      defaultSnoozeMinutes={defaultSnoozeMinutes}
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
      {query.hasNextPage && (
        <LoadMoreButton
          loading={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        />
      )}
    </OperationalLayout>
  );
}
