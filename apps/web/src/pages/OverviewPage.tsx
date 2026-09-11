import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BriefcaseBusiness,
  Check,
  ChevronDown,
  House,
  Inbox,
  Plus,
  Search,
  Waves,
} from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import {
  captureText,
  getOverview,
  operationsKeys,
  runWorkItemAction,
  type OverviewColumn as OverviewColumnData,
  type OverviewInboxItem,
  type OverviewResponse,
  type OverviewWorkItem,
  type TextCapturePayload,
  type WorkItemCardData,
} from "../api/operations";
import { remainingKeys } from "../api/remaining";
import { WorkspaceBadge } from "../components/WorkspaceBadge";
import { WorkspaceScopeFilter } from "../components/WorkspaceScopeFilter";
import type { DateTimePreferences } from "../lib/dates";
import {
  useWorkspaceScope,
  workspacePath,
  type WorkspaceScope,
} from "../lib/workspaceScope";
import { OverviewWorkItemRow, type OverviewBucket } from "./overview/OverviewWorkItemRow";

type CaptureBucket = TextCapturePayload["target_bucket"];

const bucketLabels: Record<OverviewBucket, string> = {
  today: "Сегодня",
  tomorrow: "Завтра",
  inbox: "Входящие",
};

function updateOverviewMove(
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
  const inboxItem = { ...item, status: "inbox", effective_at: null };
  const inboxEntry: OverviewInboxItem = {
    id: item.id,
    kind: "work_item",
    item: inboxItem,
    title: item.title,
    excerpt: "",
    status: "inbox",
    reasons: ["inbox_status"],
    occurred_at: item.updated_at,
    item_count: 1,
    workspace: item.workspace,
  };
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
      ...current.today,
      items: todayItems.slice(0, 8),
      total: current.today.total - Number(hadToday) + Number(target === "today"),
    },
    tomorrow: {
      ...current.tomorrow,
      items: tomorrowItems.slice(0, 8),
      total: current.tomorrow.total - Number(hadTomorrow) + Number(target === "tomorrow"),
    },
    inbox: {
      ...current.inbox,
      items: inboxItems.slice(0, 8),
      total: current.inbox.total - Number(hadInbox) + Number(target === "inbox"),
    },
  };
}

function moveToCompleted(
  current: OverviewResponse | undefined,
  original: WorkItemCardData,
  completed: WorkItemCardData,
  bucket: OverviewBucket,
): OverviewResponse | undefined {
  if (!current) return current;
  const column = current[bucket];
  const items = column.items.filter((entry) =>
    "kind" in entry
      ? entry.kind !== "work_item" || entry.item.id !== original.id
      : entry.item.id !== original.id,
  );
  return {
    ...current,
    [bucket]: {
      ...column,
      items,
      total: Math.max(0, column.total - 1),
      completed_items: [completed, ...column.completed_items].slice(0, 8),
      completed_total: column.completed_total + 1,
    },
  };
}

function moveToActive(
  current: OverviewResponse | undefined,
  item: WorkItemCardData,
  bucket: OverviewBucket,
): OverviewResponse | undefined {
  if (!current) return current;
  const column = current[bucket];
  const activeEntry =
    bucket === "inbox"
      ? ({
          id: item.id,
          kind: "work_item",
          item,
          title: item.title,
          excerpt: item.description ?? "",
          status: item.status,
          reasons: ["inbox_status"],
          occurred_at: item.updated_at,
          item_count: 1,
          workspace: item.workspace,
        } satisfies OverviewInboxItem)
      : ({ item, needs_inbox: false } satisfies OverviewWorkItem);
  return {
    ...current,
    [bucket]: {
      ...column,
      items: [activeEntry, ...column.items].slice(0, 8),
      total: column.total + 1,
      completed_items: column.completed_items.filter((entry) => entry.id !== item.id),
      completed_total: Math.max(0, column.completed_total - 1),
    },
  };
}

function boardDate(daysAhead: number, timezone: string) {
  const value = new Date();
  value.setDate(value.getDate() + daysAhead);
  const date = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    timeZone: timezone,
  }).format(value);
  const weekday = new Intl.DateTimeFormat("ru-RU", {
    weekday: "long",
    timeZone: timezone,
  }).format(value);
  return `${date}, ${weekday}`;
}

function isWindowsPlatform() {
  if (typeof navigator === "undefined") return false;
  return navigator.platform.toLowerCase().startsWith("win");
}

function OverviewColumn<T>({
  bucket,
  title,
  subtitle,
  icon,
  data,
  empty,
  to,
  completedOpen,
  onToggleCompleted,
  onAdd,
  children,
  completedChildren,
}: {
  bucket: OverviewBucket;
  title: string;
  subtitle?: string;
  icon: ReactNode;
  data: OverviewColumnData<T>;
  empty: string;
  to: string;
  completedOpen: boolean;
  onToggleCompleted: () => void;
  onAdd: () => void;
  children: ReactNode;
  completedChildren: ReactNode;
}) {
  const total = data.total + data.completed_total;
  return (
    <section
      className={`overview-column overview-column--${bucket}`}
      aria-labelledby={`${bucket}-title`}
    >
      <header className="overview-column__header">
        <div>
          <h2 id={`${bucket}-title`}>{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        <span className="overview-column__count" aria-label={`${total} записей`}>
          {total}
        </span>
        <button type="button" aria-label={`Добавить задачу в «${title}»`} onClick={onAdd}>
          <Plus size={21} aria-hidden />
        </button>
      </header>
      <div className="overview-column__list">
        {data.items.length ? children : <p className="overview-column__empty">{empty}</p>}
      </div>
      {data.completed_total > 0 && (
        <div className="overview-completed">
          <button type="button" aria-expanded={completedOpen} onClick={onToggleCompleted}>
            <ChevronDown size={17} aria-hidden />
            <span>Выполнено</span>
            <strong>{data.completed_total}</strong>
          </button>
          {completedOpen && (
            <div className="overview-completed__list">{completedChildren}</div>
          )}
        </div>
      )}
      {data.has_more && (
        <Link className="overview-column__more" to={to}>
          Показать все {data.total}
        </Link>
      )}
      <button className="overview-column__add" type="button" onClick={onAdd}>
        <Plus size={18} aria-hidden /> Добавить задачу
      </button>
      <span className="overview-column__decorative-icon" aria-hidden>
        {icon}
      </span>
    </section>
  );
}

function InboxEntry({ item, scope }: { item: OverviewInboxItem; scope: WorkspaceScope }) {
  return (
    <article className={`overview-inbox-row overview-card--${item.workspace}`}>
      <span className="overview-inbox-row__marker" aria-hidden />
      <div>
        <Link to={workspacePath(`/inbox?kind=${item.kind}&focus=${item.id}`, scope)}>
          <h3 title={item.title}>{item.title}</h3>
        </Link>
        <WorkspaceBadge workspace={item.workspace} />
      </div>
    </article>
  );
}

export function OverviewPage({
  dateTimePreferences,
  defaultWorkspace,
}: {
  dateTimePreferences: DateTimePreferences;
  defaultWorkspace: "work" | "personal";
}) {
  const queryClient = useQueryClient();
  const { scope, setScope } = useWorkspaceScope();
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [captureTextValue, setCaptureTextValue] = useState("");
  const [captureWorkspace, setCaptureWorkspace] = useState(defaultWorkspace);
  const [captureBucket, setCaptureBucket] = useState<CaptureBucket>("auto");
  const [captureId, setCaptureId] = useState(() => crypto.randomUUID());
  const [feedback, setFeedback] = useState<{
    kind: "success" | "error";
    text: string;
  } | null>(null);
  const [completedOpen, setCompletedOpen] = useState<Record<OverviewBucket, boolean>>({
    today: false,
    tomorrow: false,
    inbox: false,
  });
  const [keyboardMove, setKeyboardMove] = useState<{
    item: WorkItemCardData;
    source: OverviewBucket;
    target: OverviewBucket;
    beforeId: string | null;
    afterId: string | null;
    snapshot: OverviewResponse;
  } | null>(null);
  const composerInput = useRef<HTMLInputElement | null>(null);
  const searchInput = useRef<HTMLInputElement | null>(null);
  const windows = isWindowsPlatform();

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    if (!windows) return;
    function focusSearch(event: globalThis.KeyboardEvent) {
      if (event.ctrlKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchInput.current?.focus();
      }
    }
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, [windows]);

  const overviewKey = operationsKeys.overview(scope, debouncedSearch);
  const query = useQuery({
    queryKey: overviewKey,
    queryFn: () => getOverview(scope, debouncedSearch),
    placeholderData: (previous) => previous,
  });

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: operationsKeys.all }),
      queryClient.invalidateQueries({ queryKey: remainingKeys.all }),
    ]);
  }

  const moveMutation = useMutation({
    mutationFn: ({
      item,
      target,
      beforeId,
      afterId,
      keyboard,
    }: {
      item: WorkItemCardData;
      source: OverviewBucket;
      target: OverviewBucket;
      beforeId?: string | null;
      afterId?: string | null;
      keyboard?: boolean;
    }) =>
      runWorkItemAction(item.id, {
        action: keyboard ? "move_keyboard" : "move_bucket",
        target,
        before_id: beforeId,
        after_id: afterId,
        client_action_id: crypto.randomUUID(),
        expected_revision: item.revision,
      }),
    onMutate: async ({ item, target }) => {
      await queryClient.cancelQueries({ queryKey: overviewKey });
      const previous = queryClient.getQueryData<OverviewResponse>(overviewKey);
      queryClient.setQueryData<OverviewResponse | undefined>(overviewKey, (current) =>
        updateOverviewMove(current, item, target),
      );
      setFeedback(null);
      return { previous };
    },
    onSuccess: (_response, variables) => {
      setFeedback({
        kind: "success",
        text: variables.keyboard
          ? "Перемещение сохранено"
          : `Перемещено в «${bucketLabels[variables.target]}»`,
      });
    },
    onError: (error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(overviewKey, context.previous);
      setFeedback({
        kind: "error",
        text:
          error instanceof ApiError && error.status === 409
            ? "Задача уже изменилась. Обзор обновлён — повторите перенос."
            : error instanceof ApiError
              ? error.message
              : "Не удалось переместить задачу.",
      });
    },
    onSettled: refresh,
  });

  const captureMutation = useMutation({
    mutationFn: () =>
      captureText({
        text: captureTextValue.trim(),
        workspace: captureWorkspace,
        target_bucket: captureBucket,
        client_capture_id: captureId,
      }),
    onSuccess: async (result) => {
      setCaptureTextValue("");
      setCaptureBucket("auto");
      setCaptureId(crypto.randomUUID());
      setFeedback({
        kind: "success",
        text:
          result.disposition === "created"
            ? "Задача добавлена"
            : "Нужно уточнение — запись добавлена во «Входящие»",
      });
      await refresh();
    },
    onError: (error) => {
      setFeedback({
        kind: "error",
        text: error instanceof ApiError ? error.message : "Не удалось добавить задачу.",
      });
    },
  });

  function focusComposer(bucket: CaptureBucket) {
    setCaptureBucket(bucket);
    setFeedback(null);
    composerInput.current?.focus();
  }

  function beginKeyboardMove(item: WorkItemCardData, source: OverviewBucket) {
    const snapshot = query.data;
    if (!snapshot || moveMutation.isPending) return;
    setKeyboardMove({
      item,
      source,
      target: source,
      beforeId: null,
      afterId: null,
      snapshot,
    });
    setFeedback({
      kind: "success",
      text: "Режим перемещения · ← → колонка · ↑ ↓ порядок · Enter сохранить · Esc отменить",
    });
  }

  function keyboardMoveKey(
    event: ReactKeyboardEvent<HTMLElement>,
    item: WorkItemCardData,
    bucket: OverviewBucket,
  ) {
    if (!keyboardMove || keyboardMove.item.id !== item.id) return;
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (
      ["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A"].includes(
        (event.target as HTMLElement).tagName,
      )
    )
      return;
    const columns: OverviewBucket[] = ["today", "tomorrow", "inbox"];
    if (event.key === "Escape") {
      event.preventDefault();
      queryClient.setQueryData(overviewKey, keyboardMove.snapshot);
      setKeyboardMove(null);
      setFeedback({ kind: "success", text: "Перемещение отменено" });
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const { target, beforeId, afterId } = keyboardMove;
      setKeyboardMove(null);
      moveMutation.mutate({
        item,
        source: bucket,
        target,
        beforeId,
        afterId,
        keyboard: true,
      });
      return;
    }
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const current = queryClient.getQueryData<OverviewResponse>(overviewKey);
    if (!current) return;
    const target = keyboardMove.target;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const index = columns.indexOf(target) + (event.key === "ArrowLeft" ? -1 : 1);
      if (index < 0 || index >= columns.length) return;
      const next = columns[index];
      if (!next) return;
      queryClient.setQueryData(overviewKey, updateOverviewMove(current, item, next));
      setKeyboardMove({ ...keyboardMove, target: next, beforeId: null, afterId: null });
      requestAnimationFrame(() =>
        document
          .querySelector<HTMLElement>(`[data-overview-item-id="${item.id}"]`)
          ?.focus(),
      );
      setFeedback({
        kind: "success",
        text: `Задача перемещена в колонку ${bucketLabels[next]}`,
      });
      return;
    }
    const currentIndex = current[target].items.findIndex(
      (entry) => "item" in entry && entry.item?.id === item.id,
    );
    const nextIndex = currentIndex + (event.key === "ArrowUp" ? -1 : 1);
    if (currentIndex < 0 || nextIndex < 0 || nextIndex >= current[target].items.length)
      return;
    const nextEntry = current[target].items[nextIndex];
    if (!nextEntry || !("item" in nextEntry) || !nextEntry.item) return;
    const reordered = [...current[target].items];
    const [removed] = reordered.splice(currentIndex, 1);
    if (!removed) return;
    reordered.splice(nextIndex, 0, removed);
    queryClient.setQueryData<OverviewResponse>(overviewKey, {
      ...current,
      [target]: { ...current[target], items: reordered },
    });
    setKeyboardMove({
      ...keyboardMove,
      beforeId: event.key === "ArrowUp" ? nextEntry.item.id : null,
      afterId: event.key === "ArrowDown" ? nextEntry.item.id : null,
    });
    setFeedback({
      kind: "success",
      text:
        event.key === "ArrowUp"
          ? "Задача поднята на одну позицию"
          : "Задача опущена на одну позицию",
    });
  }

  function renderWorkItem(entry: OverviewWorkItem, bucket: OverviewBucket) {
    return (
      <OverviewWorkItemRow
        key={entry.item.id}
        entry={entry}
        bucket={bucket}
        dateTimePreferences={dateTimePreferences}
        moveDisabled={moveMutation.isPending}
        moving={keyboardMove?.item.id === entry.item.id}
        onMoveModeChange={(active) =>
          active ? beginKeyboardMove(entry.item, bucket) : setKeyboardMove(null)
        }
        onMoveKey={keyboardMoveKey}
        onMove={(item, source, target) =>
          moveMutation.mutate({ item, source, target, keyboard: false })
        }
        onCompleted={(original, completedItem, source) => {
          queryClient.setQueryData<OverviewResponse | undefined>(overviewKey, (current) =>
            moveToCompleted(current, original, completedItem, source),
          );
          void refresh();
        }}
        onReopened={(item, source) => {
          queryClient.setQueryData<OverviewResponse | undefined>(overviewKey, (current) =>
            moveToActive(current, item, source),
          );
          void refresh();
        }}
      />
    );
  }

  function renderCompleted(item: WorkItemCardData, bucket: OverviewBucket) {
    return (
      <OverviewWorkItemRow
        key={item.id}
        entry={{ item, needs_inbox: false }}
        bucket={bucket}
        dateTimePreferences={dateTimePreferences}
        completed
        onMove={() => undefined}
        onCompleted={() => undefined}
        onReopened={(reopened, source) => {
          queryClient.setQueryData<OverviewResponse | undefined>(overviewKey, (current) =>
            moveToActive(current, reopened, source),
          );
          void refresh();
        }}
      />
    );
  }

  const counts = query.data?.workspace_counts;
  return (
    <section className="overview-board" aria-labelledby="overview-board-title">
      <h1 id="overview-board-title" className="sr-only">
        Обзор
      </h1>
      <div className="overview-board__frame">
        <header className="overview-topbar">
          <Link className="overview-brand" to="/overview" aria-label="FlowMate — обзор">
            <Waves size={31} aria-hidden />
            <span>FlowMate</span>
          </Link>
          <WorkspaceScopeFilter scope={scope} counts={counts} onChange={setScope} />
          <div className="overview-topbar__actions">
            <label className="overview-search">
              <Search size={17} aria-hidden />
              <span className="sr-only">Поиск задач на доске</span>
              <input
                ref={searchInput}
                type="search"
                aria-label="Поиск задач на доске"
                value={search}
                placeholder="Поиск задач…"
                onChange={(event) => setSearch(event.target.value)}
              />
              {windows && <kbd>Ctrl K</kbd>}
            </label>
            <button
              className="overview-primary-button"
              type="button"
              onClick={() => focusComposer("auto")}
            >
              <Plus size={20} aria-hidden /> Новая задача
            </button>
          </div>
        </header>

        <form
          className="overview-composer"
          onSubmit={(event) => {
            event.preventDefault();
            if (captureTextValue.trim() && !captureMutation.isPending)
              captureMutation.mutate();
          }}
        >
          <Plus size={25} aria-hidden />
          <label className="overview-composer__input">
            <span className="sr-only">Текст новой задачи</span>
            <input
              ref={composerInput}
              value={captureTextValue}
              maxLength={10_000}
              placeholder="Например: завтра в 10 подготовить отчёт"
              disabled={captureMutation.isPending}
              onChange={(event) => {
                setCaptureTextValue(event.target.value);
                setCaptureId(crypto.randomUUID());
              }}
            />
          </label>
          {captureBucket !== "auto" && (
            <span className="overview-composer__target">
              В: {bucketLabels[captureBucket]}
            </span>
          )}
          <label className="overview-composer__workspace">
            {captureWorkspace === "work" ? (
              <BriefcaseBusiness size={16} aria-hidden />
            ) : (
              <House size={16} aria-hidden />
            )}
            <span className="sr-only">Пространство задачи</span>
            <select
              value={captureWorkspace}
              disabled={captureMutation.isPending}
              onChange={(event) =>
                setCaptureWorkspace(event.target.value as "work" | "personal")
              }
            >
              <option value="work">Работа</option>
              <option value="personal">Личное</option>
            </select>
          </label>
          <button
            type="submit"
            disabled={!captureTextValue.trim() || captureMutation.isPending}
          >
            {captureMutation.isPending ? "Разбираем задачу…" : "Добавить"}
          </button>
        </form>

        {feedback && (
          <p
            className={`overview-feedback overview-feedback--${feedback.kind}`}
            role={feedback.kind === "error" ? "alert" : "status"}
            aria-live="polite"
          >
            {feedback.text}
          </p>
        )}

        {query.isPending ? (
          <p className="overview-board__state" role="status">
            Собираем обзор…
          </p>
        ) : query.isError ? (
          <div className="overview-board__state" role="alert">
            <p>Не удалось загрузить обзор.</p>
            <button type="button" onClick={() => void query.refetch()}>
              Повторить
            </button>
          </div>
        ) : (
          <div className="overview-grid">
            <OverviewColumn
              bucket="today"
              title="Сегодня"
              subtitle={boardDate(0, dateTimePreferences.timezone)}
              icon={<Check size={18} />}
              data={query.data.today}
              empty="На сегодня всё разобрано."
              to={workspacePath("/today", scope)}
              completedOpen={completedOpen.today}
              onToggleCompleted={() =>
                setCompletedOpen((value) => ({ ...value, today: !value.today }))
              }
              onAdd={() => focusComposer("today")}
              completedChildren={query.data.today.completed_items.map((item) =>
                renderCompleted(item, "today"),
              )}
            >
              {query.data.today.items.map((entry) => renderWorkItem(entry, "today"))}
            </OverviewColumn>
            <OverviewColumn
              bucket="tomorrow"
              title="Завтра"
              subtitle={boardDate(1, dateTimePreferences.timezone)}
              icon={<Check size={18} />}
              data={query.data.tomorrow}
              empty="На завтра ничего не запланировано."
              to={workspacePath("/tomorrow", scope)}
              completedOpen={completedOpen.tomorrow}
              onToggleCompleted={() =>
                setCompletedOpen((value) => ({ ...value, tomorrow: !value.tomorrow }))
              }
              onAdd={() => focusComposer("tomorrow")}
              completedChildren={query.data.tomorrow.completed_items.map((item) =>
                renderCompleted(item, "tomorrow"),
              )}
            >
              {query.data.tomorrow.items.map((entry) => renderWorkItem(entry, "tomorrow"))}
            </OverviewColumn>
            <OverviewColumn
              bucket="inbox"
              title="Входящие"
              icon={<Inbox size={18} />}
              data={query.data.inbox}
              empty="Входящие разобраны."
              to={workspacePath("/inbox", scope)}
              completedOpen={completedOpen.inbox}
              onToggleCompleted={() =>
                setCompletedOpen((value) => ({ ...value, inbox: !value.inbox }))
              }
              onAdd={() => focusComposer("inbox")}
              completedChildren={query.data.inbox.completed_items.map((item) =>
                renderCompleted(item, "inbox"),
              )}
            >
              {query.data.inbox.items.map((entry) =>
                entry.kind === "work_item" ? (
                  renderWorkItem({ item: entry.item, needs_inbox: true }, "inbox")
                ) : (
                  <InboxEntry
                    key={`${entry.kind}-${entry.id}`}
                    item={entry}
                    scope={scope}
                  />
                ),
              )}
            </OverviewColumn>
          </div>
        )}
      </div>
    </section>
  );
}
