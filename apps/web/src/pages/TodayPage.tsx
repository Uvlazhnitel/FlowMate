import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ArrowRight, ArrowUpRight } from "lucide-react";
import { Link, Navigate, useLocation, useSearchParams } from "react-router-dom";

import { getToday, getTodayOverview, operationsKeys } from "../api/operations";
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
  ["follow_ups", "Фоллоу-апы"],
  ["waiting", "Ждём ответа"],
  ["questions", "Нужно прояснить"],
] as const;

type TodaySectionKey = (typeof sections)[number][0];

const sectionKeys = new Set<string>(sections.map(([key]) => key));

export function TodayPage({
  dateTimePreferences,
  defaultSnoozeMinutes,
}: {
  dateTimePreferences: DateTimePreferences;
  defaultSnoozeMinutes: number;
}) {
  const [params] = useSearchParams();
  const location = useLocation();
  const requestedSection = params.get("section");

  if (requestedSection !== null && !sectionKeys.has(requestedSection)) {
    const canonicalParams = new URLSearchParams(params);
    canonicalParams.delete("section");
    const search = canonicalParams.toString();
    return (
      <Navigate
        to={{
          pathname: "/today",
          search: search ? `?${search}` : "",
          hash: location.hash,
        }}
        replace
      />
    );
  }

  return (
    <TodayContent
      selectedSection={requestedSection as TodaySectionKey | null}
      dateTimePreferences={dateTimePreferences}
      defaultSnoozeMinutes={defaultSnoozeMinutes}
    />
  );
}

function TodayContent({
  selectedSection,
  dateTimePreferences,
  defaultSnoozeMinutes,
}: {
  selectedSection: TodaySectionKey | null;
  dateTimePreferences: DateTimePreferences;
  defaultSnoozeMinutes: number;
}) {
  const [, setParams] = useSearchParams();
  const overview = useQuery({
    queryKey: operationsKeys.todayOverview,
    queryFn: getTodayOverview,
  });

  return (
    <OperationalLayout
      eyebrow="Исполнение"
      title="Сегодня"
      description="Разберите входящее, выберите главное и спокойно двигайтесь по дню."
      controls={
        <nav className="today-section-nav" aria-label="Раздел Сегодня">
          <select
            aria-label="Раздел Сегодня"
            value={selectedSection ?? "overview"}
            onChange={(event) =>
              setParams(
                event.target.value === "overview" ? {} : { section: event.target.value },
              )
            }
          >
            <option value="overview">Главное сейчас</option>
            {sections.map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </nav>
      }
    >
      {overview.isPending ? (
        <LoadingState label="Собираем главное на сегодня" />
      ) : overview.isError ? (
        <ErrorState
          title="Не удалось загрузить сегодняшний обзор"
          onRetry={() => void overview.refetch()}
        />
      ) : (
        <>
          <TodaySummary summary={overview.data.summary} selectedSection={selectedSection} />
          {selectedSection === null ? (
            <TodayOverview
              focus={overview.data.focus}
              later={overview.data.later_today}
              inboxCount={overview.data.summary.inbox}
              dateTimePreferences={dateTimePreferences}
              defaultSnoozeMinutes={defaultSnoozeMinutes}
            />
          ) : (
            <TodaySection
              section={selectedSection}
              label={
                sections.find(([key]) => key === selectedSection)?.[1] ?? "Выбранный раздел"
              }
              dateTimePreferences={dateTimePreferences}
              defaultSnoozeMinutes={defaultSnoozeMinutes}
            />
          )}
        </>
      )}
    </OperationalLayout>
  );
}

function TodaySummary({
  summary,
  selectedSection,
}: {
  summary: {
    overdue: number;
    due_today: number;
    follow_ups: number;
    waiting_overdue: number;
    questions: number;
    inbox: number;
  };
  selectedSection: TodaySectionKey | null;
}) {
  const primary = [
    ["inbox", "Входящие", summary.inbox, "/inbox", null],
    ["overdue", "Просрочено", summary.overdue, "/today?section=overdue", "overdue"],
    ["due_today", "На сегодня", summary.due_today, "/today?section=due_today", "due_today"],
  ] as const;
  const attention = [
    ["Фоллоу-апы", summary.follow_ups, "follow_ups"],
    ["Ждём ответа", summary.waiting_overdue, "waiting"],
    ["Вопросы", summary.questions, "questions"],
  ] as const;

  return (
    <>
      <div className="today-summary">
        {primary.map(([key, label, count, to, section]) => {
          const isActive = section !== null && selectedSection === section;
          return (
            <Link
              className={[
                "today-summary-card",
                key === "overdue" && count > 0 ? "today-summary-card--urgent" : "",
                isActive ? "today-summary-card--active" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              to={to}
              key={key}
              aria-current={isActive ? "page" : undefined}
            >
              <strong>{count}</strong>
              <span>{label}</span>
              <ArrowUpRight size={15} aria-hidden />
            </Link>
          );
        })}
      </div>
      {attention.some(([, count]) => count > 0) && (
        <nav className="today-attention-links" aria-label="Другие приоритеты дня">
          {attention.map(([label, count, section]) =>
            count > 0 ? (
              <Link
                key={section}
                to={`/today?section=${section}`}
                aria-current={selectedSection === section ? "page" : undefined}
              >
                {label} {count}
              </Link>
            ) : null,
          )}
        </nav>
      )}
      <Link className="today-tomorrow-link" to="/tomorrow">
        Посмотреть задачи на завтра
        <ArrowRight size={16} aria-hidden />
      </Link>
    </>
  );
}

function TodayOverview({
  focus,
  later,
  inboxCount,
  dateTimePreferences,
  defaultSnoozeMinutes,
}: {
  focus: Awaited<ReturnType<typeof getTodayOverview>>["focus"];
  later: Awaited<ReturnType<typeof getTodayOverview>>["later_today"];
  inboxCount: number;
  dateTimePreferences: DateTimePreferences;
  defaultSnoozeMinutes: number;
}) {
  const focusIds = new Set(focus.map((item) => item.id));
  const laterItems = later.items.filter((item) => !focusIds.has(item.id));

  if (!focus.length && !laterItems.length) {
    return (
      <div className="today-overview today-overview--empty">
        <EmptyState
          title="На сегодня всё разобрано"
          description={
            inboxCount > 0 ? (
              <>
                Во входящих осталось {inboxCount}.{" "}
                <Link to="/inbox">Разобрать входящие</Link>
              </>
            ) : (
              "Срочных записей на сегодня нет. Остальные открытые задачи остаются в своих разделах."
            )
          }
        />
      </div>
    );
  }

  return (
    <div className="today-overview">
      {focus.length > 0 && (
        <section className="operational-section">
          <SectionHeading title="Главное сейчас" count={focus.length} />
          <div className="work-list">
            {focus.map((item) => (
              <WorkItemCard
                key={item.id}
                item={item}
                dateTimePreferences={dateTimePreferences}
                defaultSnoozeMinutes={defaultSnoozeMinutes}
              />
            ))}
          </div>
        </section>
      )}
      {laterItems.length > 0 && (
        <section className="operational-section">
          <SectionHeading title="Позже сегодня" count={laterItems.length} />
          <div className="work-list">
            {laterItems.map((item) => (
              <WorkItemCard
                key={item.id}
                item={item}
                dateTimePreferences={dateTimePreferences}
                defaultSnoozeMinutes={defaultSnoozeMinutes}
              />
            ))}
          </div>
          {later.has_more && (
            <Link className="today-more-link" to="/today?section=due_today">
              Показать все задачи на сегодня
            </Link>
          )}
        </section>
      )}
    </div>
  );
}

function TodaySection({
  section,
  label,
  dateTimePreferences,
  defaultSnoozeMinutes,
}: {
  section: TodaySectionKey;
  label: string;
  dateTimePreferences: DateTimePreferences;
  defaultSnoozeMinutes: number;
}) {
  const query = useInfiniteQuery({
    queryKey: [...operationsKeys.all, "today", "section", section],
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
  if (!items.length) {
    return (
      <EmptyState
        title="Здесь всё закрыто"
        description="В этой группе сейчас нет записей, которые требуют действия."
      />
    );
  }
  return (
    <section className="operational-section today-selected-section">
      <SectionHeading title={label} count={items.length} />
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
      {query.hasNextPage && (
        <LoadMoreButton
          loading={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        />
      )}
    </section>
  );
}
