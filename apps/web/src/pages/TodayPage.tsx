import { useInfiniteQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { getToday, operationsKeys } from "../api/operations";
import {
  LoadMoreButton,
  OperationalLayout,
  SectionHeading,
} from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { WorkItemCard } from "../components/WorkItemCard";
import type { DateTimePreferences } from "../lib/dates";

const sections = [
  ["overdue", "Просрочено"],
  ["due_today", "На сегодня"],
  ["follow_ups", "Follow-up"],
  ["waiting", "Ожидания"],
  ["questions", "Открытые вопросы"],
] as const;

export function TodayPage({
  dateTimePreferences,
  defaultSnoozeMinutes,
}: {
  dateTimePreferences: DateTimePreferences;
  defaultSnoozeMinutes: number;
}) {
  const [params, setParams] = useSearchParams();
  const selected = params.get("section") ?? "all";
  const visible = sections.filter(
    ([section]) => selected === "all" || selected === section,
  );
  return (
    <OperationalLayout
      eyebrow="Фокус"
      title="Сегодня"
      description="Один спокойный список для решений, сообщений и обещаний."
      controls={
        <select
          aria-label="Раздел Сегодня"
          value={selected}
          onChange={(event) =>
            setParams(event.target.value === "all" ? {} : { section: event.target.value })
          }
        >
          <option value="all">Все группы</option>
          {sections.map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      }
    >
      {visible.map(([section, label]) => (
        <TodaySection
          key={section}
          section={section}
          label={label}
          dateTimePreferences={dateTimePreferences}
          defaultSnoozeMinutes={defaultSnoozeMinutes}
          focused={selected !== "all"}
        />
      ))}
    </OperationalLayout>
  );
}

function TodaySection({
  section,
  label,
  dateTimePreferences,
  defaultSnoozeMinutes,
  focused,
}: {
  section: (typeof sections)[number][0];
  label: string;
  dateTimePreferences: DateTimePreferences;
  defaultSnoozeMinutes: number;
  focused: boolean;
}) {
  const query = useInfiniteQuery({
    queryKey: [...operationsKeys.all, "today", section],
    queryFn: ({ pageParam }) => getToday(section, pageParam),
    initialPageParam: 0,
    getNextPageParam: (page) => (page.has_more ? page.offset + page.limit : undefined),
  });
  if (query.isPending) return <LoadingState label={`Загружаем: ${label}`} />;
  if (query.isError)
    return (
      <ErrorState
        title={`Не удалось загрузить: ${label}`}
        onRetry={() => void query.refetch()}
      />
    );
  const items = query.data.pages.flatMap((page) => page.items);
  if (!items.length && focused) {
    return (
      <EmptyState
        title="В этой группе пусто"
        description="Сейчас здесь нет записей, требующих внимания."
      />
    );
  }
  return (
    <section className="operational-section">
      <SectionHeading title={label} count={items.length} />
      {items.length ? (
        <div className="work-list">
          {items.map((item) => (
            <WorkItemCard
              key={item.id}
              item={item}
              dateTimePreferences={dateTimePreferences}
              defaultSnoozeMinutes={defaultSnoozeMinutes}
            />
          ))}
        </div>
      ) : (
        <p className="muted-copy">Нет записей.</p>
      )}
      {query.hasNextPage && (
        <LoadMoreButton
          loading={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        />
      )}
    </section>
  );
}
