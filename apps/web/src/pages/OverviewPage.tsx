import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, CalendarDays, CalendarRange, Inbox } from "lucide-react";
import { useState, type DragEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";

import {
  getOverview,
  operationsKeys,
  runWorkItemAction,
  type OverviewColumn as OverviewColumnData,
  type OverviewInboxItem,
  type OverviewResponse,
  type OverviewWorkItem,
  type WorkItemCardData,
} from "../api/operations";
import { ApiError } from "../api/client";
import { remainingKeys } from "../api/remaining";
import { OperationalLayout } from "../components/OperationalLayout";
import { ErrorState, LoadingState } from "../components/PageState";
import { WorkspaceBadge } from "../components/WorkspaceBadge";
import { WorkspaceScopeFilter } from "../components/WorkspaceScopeFilter";
import type { DateTimePreferences } from "../lib/dates";
import {
  useWorkspaceScope,
  workspacePath,
  type WorkspaceScope,
} from "../lib/workspaceScope";
import { reasonLabels } from "./inbox/presentation";
import { OverviewWorkItemRow } from "./overview/OverviewWorkItemRow";

const inboxKindLabels: Record<OverviewInboxItem["kind"], string> = {
  draft: "Черновик AI",
  work_item: "Задача",
  note: "Заметка",
};

type OverviewBucket = "today" | "tomorrow" | "inbox";

interface DraggedWorkItem {
  item: WorkItemCardData;
  source: OverviewBucket;
}

const draggableTypes = new Set(["task", "follow_up", "waiting", "question"]);
const bucketLabels: Record<OverviewBucket, string> = {
  today: "Сегодня",
  tomorrow: "Завтра",
  inbox: "Входящие",
};

function optimisticMove(
  current: OverviewResponse | undefined,
  item: WorkItemCardData,
  target: OverviewBucket,
): OverviewResponse | undefined {
  if (!current) return current;
  const hadToday = current.today.items.some((entry) => entry.item.id === item.id);
  const hadTomorrow = current.tomorrow.items.some((entry) => entry.item.id === item.id);
  const hadInbox = current.inbox.items.some(
    (entry) => entry.kind === "work_item" && entry.item.id === item.id,
  );
  const datedEntry: OverviewWorkItem = { item, needs_inbox: false };
  const inboxEntry: OverviewInboxItem = {
    id: item.id,
    kind: "work_item",
    item: { ...item, status: "inbox", effective_at: null },
    title: item.title,
    excerpt: item.description ?? "",
    status: "inbox",
    reasons: ["inbox_status"],
    occurred_at: item.updated_at,
    item_count: 1,
    workspace: item.workspace,
  };
  const todayTotal = current.today.total - Number(hadToday) + Number(target === "today");
  const tomorrowTotal =
    current.tomorrow.total - Number(hadTomorrow) + Number(target === "tomorrow");
  const inboxTotal = current.inbox.total - Number(hadInbox) + Number(target === "inbox");
  const todayItems = current.today.items.filter((entry) => entry.item.id !== item.id);
  const tomorrowItems = current.tomorrow.items.filter((entry) => entry.item.id !== item.id);
  const inboxItems = current.inbox.items.filter(
    (entry) => entry.kind !== "work_item" || entry.item.id !== item.id,
  );
  if (target === "today") todayItems.unshift(datedEntry);
  if (target === "tomorrow") tomorrowItems.unshift(datedEntry);
  if (target === "inbox") inboxItems.unshift(inboxEntry);
  return {
    ...current,
    today: {
      items: todayItems.slice(0, 8),
      total: todayTotal,
      has_more: todayTotal > Math.min(todayItems.length, 8),
    },
    tomorrow: {
      items: tomorrowItems.slice(0, 8),
      total: tomorrowTotal,
      has_more: tomorrowTotal > Math.min(tomorrowItems.length, 8),
    },
    inbox: {
      items: inboxItems.slice(0, 8),
      total: inboxTotal,
      has_more: inboxTotal > Math.min(inboxItems.length, 8),
    },
  };
}

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
  dropEnabled,
  dropActive,
  onDragOver,
  onDragLeave,
  onDrop,
}: {
  title: string;
  icon: ReactNode;
  data: OverviewColumnData<unknown>;
  to: string;
  className: string;
  empty: string;
  children: ReactNode;
  dropEnabled: boolean;
  dropActive: boolean;
  onDragOver: (event: DragEvent<HTMLElement>) => void;
  onDragLeave: (event: DragEvent<HTMLElement>) => void;
  onDrop: (event: DragEvent<HTMLElement>) => void;
}) {
  return (
    <section
      className={`overview-column ${className} ${dropEnabled ? "overview-column--drop-enabled" : ""} ${dropActive ? "overview-column--drop-active" : ""}`}
      aria-labelledby={`${className}-title`}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
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

function OverviewInboxRow({
  item,
  moveDisabled,
  dragging,
  onDragStart,
  onDragEnd,
  scope,
}: {
  item: OverviewInboxItem;
  moveDisabled: boolean;
  dragging: boolean;
  onDragStart: (event: DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  scope: WorkspaceScope;
}) {
  const reason = item.reasons[0];
  const canDrag = item.kind === "work_item" && draggableTypes.has(item.item.type);
  return (
    <Link
      className={`overview-inbox-row ${dragging ? "overview-row--dragging" : ""}`}
      to={workspacePath(`/inbox?kind=${item.kind}&focus=${item.id}`, scope)}
      draggable={canDrag && !moveDisabled}
      aria-busy={canDrag && moveDisabled}
      onDragStart={canDrag ? onDragStart : undefined}
      onDragEnd={canDrag ? onDragEnd : undefined}
    >
      <div className="overview-row__badges">
        <span className="overview-badge">{inboxKindLabels[item.kind]}</span>
        <WorkspaceBadge workspace={item.workspace} />
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
  const queryClient = useQueryClient();
  const { scope, setScope } = useWorkspaceScope();
  const overviewKey = operationsKeys.overview(scope);
  const [dragged, setDragged] = useState<DraggedWorkItem | null>(null);
  const [dropTarget, setDropTarget] = useState<OverviewBucket | null>(null);
  const [moveFeedback, setMoveFeedback] = useState<{
    kind: "success" | "error";
    text: string;
  } | null>(null);
  const query = useQuery({ queryKey: overviewKey, queryFn: () => getOverview(scope) });
  const moveMutation = useMutation({
    mutationFn: ({ item, target }: DraggedWorkItem & { target: OverviewBucket }) =>
      runWorkItemAction(item.id, {
        action: "move_bucket",
        target,
        client_action_id: crypto.randomUUID(),
        expected_revision: item.revision,
      }),
    onMutate: async ({ item, target }) => {
      await queryClient.cancelQueries({ queryKey: overviewKey });
      const previous = queryClient.getQueryData<OverviewResponse>(overviewKey);
      queryClient.setQueryData<OverviewResponse | undefined>(overviewKey, (current) =>
        optimisticMove(current, item, target),
      );
      return { previous };
    },
    onSuccess: (_response, variables) => {
      setMoveFeedback({
        kind: "success",
        text: `Задача перемещена в «${bucketLabels[variables.target]}».`,
      });
    },
    onError: (error, _variables, context) => {
      if (context?.previous) {
        queryClient.setQueryData(overviewKey, context.previous);
      }
      const text =
        error instanceof ApiError && error.status === 409
          ? "Задача уже изменилась. Обзор обновлён — повторите перенос."
          : error instanceof ApiError
            ? error.message
            : "Не удалось переместить задачу. Попробуйте ещё раз.";
      setMoveFeedback({ kind: "error", text });
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: operationsKeys.all }),
        queryClient.invalidateQueries({ queryKey: remainingKeys.all }),
      ]);
    },
  });

  function startDrag(
    event: DragEvent<HTMLElement>,
    item: WorkItemCardData,
    source: OverviewBucket,
  ) {
    if (moveMutation.isPending) {
      event.preventDefault();
      return;
    }
    moveMutation.reset();
    setMoveFeedback(null);
    setDragged({ item, source });
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", item.id);
  }

  function dragOver(event: DragEvent<HTMLElement>, target: OverviewBucket) {
    if (!dragged || dragged.source === target || moveMutation.isPending) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    setDropTarget(target);
  }

  function dragLeave(event: DragEvent<HTMLElement>, target: OverviewBucket) {
    if (
      dropTarget === target &&
      !event.currentTarget.contains(event.relatedTarget as Node | null)
    ) {
      setDropTarget(null);
    }
  }

  function drop(event: DragEvent<HTMLElement>, target: OverviewBucket) {
    event.preventDefault();
    setDropTarget(null);
    if (!dragged || dragged.source === target || moveMutation.isPending) return;
    moveMutation.mutate({ ...dragged, target });
    setDragged(null);
  }

  function endDrag() {
    setDragged(null);
    setDropTarget(null);
  }

  const columnDragProps = (bucket: OverviewBucket) => ({
    dropEnabled: Boolean(dragged && dragged.source !== bucket),
    dropActive: dropTarget === bucket,
    onDragOver: (event: DragEvent<HTMLElement>) => dragOver(event, bucket),
    onDragLeave: (event: DragEvent<HTMLElement>) => dragLeave(event, bucket),
    onDrop: (event: DragEvent<HTMLElement>) => drop(event, bucket),
  });

  return (
    <OperationalLayout
      eyebrow="Главное"
      title="Обзор"
      description="Сегодня, завтра и входящие — в одном спокойном рабочем пространстве."
      controls={
        <WorkspaceScopeFilter
          scope={scope}
          counts={query.data?.workspace_counts}
          onChange={setScope}
        />
      }
    >
      {query.isPending ? (
        <LoadingState label="Собираем обзор" />
      ) : query.isError ? (
        <ErrorState
          title="Не удалось загрузить обзор"
          onRetry={() => void query.refetch()}
        />
      ) : (
        <>
          {moveFeedback && (
            <p
              className={`overview-move-feedback overview-move-feedback--${moveFeedback.kind}`}
              role={moveFeedback.kind === "error" ? "alert" : "status"}
              aria-live="polite"
            >
              {moveFeedback.text}
            </p>
          )}
          <div className="overview-grid">
            <OverviewColumn
              title="Сегодня"
              icon={<CalendarDays size={18} aria-hidden />}
              data={query.data.today}
              to={workspacePath("/today", scope)}
              className="overview-column--today"
              empty="На сегодня всё разобрано."
              {...columnDragProps("today")}
            >
              {query.data.today.items.map((entry: OverviewWorkItem) => (
                <OverviewWorkItemRow
                  key={entry.item.id}
                  entry={entry}
                  dateTimePreferences={dateTimePreferences}
                  moveDisabled={moveMutation.isPending}
                  dragging={dragged?.item.id === entry.item.id}
                  onDragStart={(event) => startDrag(event, entry.item, "today")}
                  onDragEnd={endDrag}
                />
              ))}
            </OverviewColumn>
            <OverviewColumn
              title="Завтра"
              icon={<CalendarRange size={18} aria-hidden />}
              data={query.data.tomorrow}
              to={workspacePath("/tomorrow", scope)}
              className="overview-column--tomorrow"
              empty="На завтра ничего не запланировано."
              {...columnDragProps("tomorrow")}
            >
              {query.data.tomorrow.items.map((entry: OverviewWorkItem) => (
                <OverviewWorkItemRow
                  key={entry.item.id}
                  entry={entry}
                  dateTimePreferences={dateTimePreferences}
                  moveDisabled={moveMutation.isPending}
                  dragging={dragged?.item.id === entry.item.id}
                  onDragStart={(event) => startDrag(event, entry.item, "tomorrow")}
                  onDragEnd={endDrag}
                />
              ))}
            </OverviewColumn>
            <OverviewColumn
              title="Входящие"
              icon={<Inbox size={18} aria-hidden />}
              data={query.data.inbox}
              to={workspacePath("/inbox", scope)}
              className="overview-column--inbox"
              empty="Входящие разобраны."
              {...columnDragProps("inbox")}
            >
              {query.data.inbox.items.map((item: OverviewInboxItem) => (
                <OverviewInboxRow
                  key={`${item.kind}-${item.id}`}
                  item={item}
                  moveDisabled={moveMutation.isPending}
                  dragging={item.kind === "work_item" && dragged?.item.id === item.item.id}
                  onDragStart={(event) => {
                    if (item.kind === "work_item") startDrag(event, item.item, "inbox");
                  }}
                  onDragEnd={endDrag}
                  scope={scope}
                />
              ))}
            </OverviewColumn>
          </div>
        </>
      )}
    </OperationalLayout>
  );
}
