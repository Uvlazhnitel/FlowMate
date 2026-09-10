import { ArrowLeftRight, Check, Clock3, MoreVertical, RotateCcw } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type DragEvent } from "react";

import {
  operationsKeys,
  runWorkItemAction,
  type ActionPayload,
  type OverviewWorkItem,
  type RescheduleSelection,
  type WorkItemAction,
  type WorkItemCardData,
} from "../../api/operations";
import { ApiError } from "../../api/client";
import { remainingKeys } from "../../api/remaining";
import { formatDateTime, type DateTimePreferences } from "../../lib/dates";
import { RescheduleDialog } from "../../components/RescheduleDialog";
import { SubtaskChecklist } from "../../components/SubtaskChecklist";
import { WorkspaceBadge } from "../../components/WorkspaceBadge";
import { InlineTitleEditor } from "../../components/InlineTitleEditor";

const typeLabels: Record<string, string> = {
  task: "Задача",
  follow_up: "Фоллоу-ап",
  waiting: "Ожидание",
  question: "Вопрос",
};

const priorityLabels: Record<string, string> = {
  urgent: "Срочно",
  high: "Высокий",
  low: "Низкий",
};

const UNDO_WINDOW_MS = 8_000;

export function OverviewWorkItemRow({
  entry,
  dateTimePreferences,
  moveDisabled = false,
  dragging = false,
  onDragStart,
  onDragEnd,
}: {
  entry: OverviewWorkItem;
  dateTimePreferences: DateTimePreferences;
  moveDisabled?: boolean;
  dragging?: boolean;
  onDragStart?: (event: DragEvent<HTMLElement>) => void;
  onDragEnd?: () => void;
}) {
  const { item } = entry;
  const queryClient = useQueryClient();
  const [rescheduleOpen, setRescheduleOpen] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [undoItem, setUndoItem] = useState<WorkItemCardData | null>(null);
  const [undoError, setUndoError] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const moreMenu = useRef<HTMLDetailsElement | null>(null);

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: operationsKeys.all }),
      queryClient.invalidateQueries({ queryKey: remainingKeys.all }),
    ]);
  }

  useEffect(
    () => () => {
      if (undoTimer.current !== null) clearTimeout(undoTimer.current);
    },
    [],
  );

  const mutation = useMutation({
    mutationFn: (payload: Omit<ActionPayload, "client_action_id">) =>
      runWorkItemAction(item.id, {
        ...payload,
        client_action_id: crypto.randomUUID(),
      }),
    onSuccess: (response, variables) => {
      if (
        ["complete", "waiting_received"].includes(variables.action) &&
        response.work_item
      ) {
        setUndoItem(response.work_item);
        setHidden(true);
        undoTimer.current = setTimeout(() => {
          undoTimer.current = null;
          setUndoItem(null);
          void refresh();
        }, UNDO_WINDOW_MS);
        return;
      }
      if (variables.action.startsWith("reschedule")) setRescheduleOpen(false);
      void refresh();
    },
    onError: (error, variables) => {
      if (
        (error instanceof ApiError && error.status === 409) ||
        variables.action === "move_workspace"
      ) {
        void refresh();
      }
    },
  });

  function act(action: WorkItemAction, extra: Partial<ActionPayload> = {}) {
    mutation.mutate({ action, expected_revision: item.revision, ...extra });
  }

  function submitReschedule(selection: RescheduleSelection) {
    mutation.mutate({ ...selection, expected_revision: item.revision });
  }

  function moveWorkspace() {
    if (moreMenu.current) moreMenu.current.open = false;
    setMoreOpen(false);
    const target = item.workspace === "work" ? "personal" : "work";
    const targetLabel = target === "work" ? "Работа" : "Личное";
    if (
      window.confirm(
        `Переместить запись в «${targetLabel}»? Тема будет сопоставлена по имени или снята.`,
      )
    ) {
      act("move_workspace", { target });
    }
  }

  function editTitle(title: string) {
    act("edit_title", { title });
  }

  function clearTitle() {
    if (window.confirm("Пустой заголовок архивирует эту запись. Продолжить?")) {
      act("edit_title", { title: "" });
    }
  }

  async function undo() {
    if (!undoItem) return;
    if (undoTimer.current !== null) {
      clearTimeout(undoTimer.current);
      undoTimer.current = null;
    }
    setUndoError(false);
    try {
      await runWorkItemAction(item.id, {
        action: "reopen",
        client_action_id: crypto.randomUUID(),
        expected_revision: undoItem.revision,
      });
      setHidden(false);
      setUndoItem(null);
      await refresh();
    } catch {
      setUndoError(true);
    }
  }

  if (hidden) {
    return (
      <div className="overview-undo" role="status">
        <span>Запись завершена</span>
        <button type="button" onClick={() => void undo()}>
          <RotateCcw size={14} aria-hidden /> Вернуть
        </button>
        {undoError && <span className="inline-error">Не удалось вернуть запись.</span>}
      </div>
    );
  }

  const primaryAction: WorkItemAction =
    item.type === "waiting" ? "waiting_received" : "complete";
  const primaryLabel = item.type === "waiting" ? "Получено" : "Готово";
  const priorityLabel = priorityLabels[item.priority];
  const staleError =
    mutation.variables?.action === "move_workspace" && mutation.error instanceof ApiError
      ? mutation.error.message
      : mutation.error instanceof ApiError && mutation.error.status === 409
        ? "Запись уже изменилась. Обзор обновлён."
        : mutation.isError && !rescheduleOpen
          ? "Не удалось выполнить действие."
          : null;
  const rescheduleError =
    rescheduleOpen && mutation.isError
      ? mutation.error instanceof ApiError && mutation.error.status === 409
        ? "Задача уже изменилась. Обзор обновлён — выберите срок ещё раз."
        : mutation.error instanceof ApiError
          ? mutation.error.message
          : "Не удалось перенести задачу. Попробуйте ещё раз."
      : null;

  function runPrimaryAction() {
    const openSubtasks = (item.subtasks ?? []).filter(
      (subtask) => subtask.status !== "done" && subtask.status !== "cancelled",
    ).length;
    if (
      openSubtasks > 0 &&
      !window.confirm(`Завершить запись и открытые подпункты (${openSubtasks})?`)
    ) {
      return;
    }
    act(primaryAction);
  }

  return (
    <article
      className={`overview-task-row ${dragging ? "overview-row--dragging" : ""}`}
      aria-busy={mutation.isPending || moveDisabled}
      draggable={Boolean(onDragStart) && !moveDisabled}
      onDragStart={(event) => {
        if ((event.target as Element).closest("button, summary, input, select, textarea")) {
          event.preventDefault();
          return;
        }
        onDragStart?.(event);
      }}
      onDragEnd={onDragEnd}
    >
      <div className="overview-row__badges">
        <button
          className="completion-checkbox completion-checkbox--top completion-checkbox--compact"
          type="button"
          aria-label={primaryLabel}
          title={primaryLabel}
          disabled={mutation.isPending}
          onClick={runPrimaryAction}
        >
          <Check size={14} aria-hidden />
        </button>
        <span
          className={
            item.overdue ? "overview-badge overview-badge--overdue" : "overview-badge"
          }
        >
          {item.overdue ? "Просрочено" : (typeLabels[item.type] ?? item.type)}
        </span>
        <WorkspaceBadge workspace={item.workspace} />
        {entry.needs_inbox && <span className="overview-badge">Нужно разобрать</span>}
        {priorityLabel && (
          <span className={`overview-priority overview-priority--${item.priority}`}>
            {priorityLabel}
          </span>
        )}
        <details className="card-more card-more--top" ref={moreMenu} open={moreOpen}>
          <summary
            className="overview-action overview-action--icon"
            role="button"
            aria-label="Ещё действия"
            title="Ещё действия"
            aria-expanded={moreOpen}
            aria-disabled={mutation.isPending}
            onClick={(event) => {
              event.preventDefault();
              if (mutation.isPending) return;
              const nextOpen = !moreOpen;
              if (moreMenu.current) moreMenu.current.open = nextOpen;
              setMoreOpen(nextOpen);
            }}
          >
            <MoreVertical size={17} aria-hidden />
          </summary>
          <div className="card-more__menu" role="menu" hidden={!moreOpen}>
            <button className="overview-action" type="button" role="menuitem" disabled={mutation.isPending} onClick={() => { mutation.reset(); setRescheduleOpen(true); }}>
              <Clock3 size={14} aria-hidden /> Перенести
            </button>
            <button className="overview-action" type="button" role="menuitem" disabled={mutation.isPending} onClick={moveWorkspace}>
              <ArrowLeftRight size={14} aria-hidden /> {item.workspace === "work" ? "В личное" : "В работу"}
            </button>
          </div>
        </details>
      </div>
      <h3 title={item.title}>
        <InlineTitleEditor
          value={item.title}
          pending={mutation.isPending || moveDisabled}
          onSave={editTitle}
          onEmpty={clearTitle}
        />
      </h3>
      <p className="overview-row__meta">
        {formatDateTime(item.effective_at, dateTimePreferences)}
      </p>
      <SubtaskChecklist item={item} compact />
      {staleError && (
        <p className="inline-error" role="alert">
          {staleError}
        </p>
      )}
      {rescheduleOpen && (
        <RescheduleDialog
          dialogId={`overview-reschedule-${item.id}`}
          currentDueAt={item.effective_at}
          dateTimePreferences={dateTimePreferences}
          pending={mutation.isPending}
          error={rescheduleError}
          onSubmit={submitReschedule}
          onCancel={() => setRescheduleOpen(false)}
        />
      )}
    </article>
  );
}
