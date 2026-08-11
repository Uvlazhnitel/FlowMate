import { useInfiniteQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";

import { getTomorrow, operationsKeys } from "../api/operations";
import {
  LoadMoreButton,
  OperationalLayout,
  SectionHeading,
} from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { WorkItemCard } from "../components/WorkItemCard";
import type { DateTimePreferences } from "../lib/dates";

export function TomorrowPage({
  dateTimePreferences,
  defaultSnoozeMinutes,
}: {
  dateTimePreferences: DateTimePreferences;
  defaultSnoozeMinutes: number;
}) {
  const query = useInfiniteQuery({
    queryKey: operationsKeys.tomorrow,
    queryFn: ({ pageParam }) => getTomorrow(pageParam),
    initialPageParam: 0,
    getNextPageParam: (page) => (page.has_more ? page.offset + page.limit : undefined),
  });

  const content = query.isPending ? (
    <LoadingState label="Собираем задачи на завтра" />
  ) : query.isError ? (
    <ErrorState
      title="Не удалось загрузить задачи на завтра"
      onRetry={() => void query.refetch()}
    />
  ) : (
    <TomorrowList
      items={query.data.pages.flatMap((page) => page.items)}
      hasNextPage={query.hasNextPage}
      isFetchingNextPage={query.isFetchingNextPage}
      fetchNextPage={() => void query.fetchNextPage()}
      dateTimePreferences={dateTimePreferences}
      defaultSnoozeMinutes={defaultSnoozeMinutes}
    />
  );

  return (
    <OperationalLayout
      eyebrow="Планирование"
      title="Завтра"
      description="Все открытые записи, которые запланированы на следующий день."
      controls={
        <Link className="button button--secondary tomorrow-back-link" to="/today">
          <ArrowLeft size={16} aria-hidden />
          Вернуться к сегодня
        </Link>
      }
    >
      {content}
    </OperationalLayout>
  );
}

function TomorrowList({
  items,
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
  dateTimePreferences,
  defaultSnoozeMinutes,
}: {
  items: Awaited<ReturnType<typeof getTomorrow>>["items"];
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => void;
  dateTimePreferences: DateTimePreferences;
  defaultSnoozeMinutes: number;
}) {
  if (!items.length) {
    return (
      <div className="tomorrow-empty">
        <EmptyState
          title="На завтра ничего не запланировано"
          description="Можно спокойно завершить сегодняшний день или разобрать входящие."
        />
      </div>
    );
  }

  return (
    <section className="operational-section tomorrow-list">
      <SectionHeading title="Все записи" count={items.length} />
      <div className="work-list work-list--compact-grid">
        {items.map((item) => (
          <WorkItemCard
            key={item.id}
            item={item}
            dateTimePreferences={dateTimePreferences}
            compact
            defaultSnoozeMinutes={defaultSnoozeMinutes}
          />
        ))}
      </div>
      {hasNextPage && (
        <LoadMoreButton loading={isFetchingNextPage} onClick={fetchNextPage} />
      )}
    </section>
  );
}
