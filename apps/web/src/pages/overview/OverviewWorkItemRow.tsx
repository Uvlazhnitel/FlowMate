import { Check, Clock3, RotateCcw } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

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
}: {
  entry: OverviewWorkItem;
  dateTimePreferences: DateTimePreferences;
}) {
  const { item } = entry;
  const queryClient = useQueryClient();
  const [rescheduleOpen, setRescheduleOpen] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [undoItem, setUndoItem] = useState<WorkItemCardData | null>(null);
  const [undoError, setUndoError] = useState(false);
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) void refresh();
    },
  });

  function act(action: WorkItemAction, extra: Partial<ActionPayload> = {}) {
    mutation.mutate({ action, expected_revision: item.revision, ...extra });
  }

  function submitReschedule(selection: RescheduleSelection) {
    mutation.mutate({ ...selection, expected_revision: item.revision });
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
    mutation.error instanceof ApiError && mutation.error.status === 409
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

  return (
    <article className="overview-task-row" aria-busy={mutation.isPending}>
      <div className="overview-row__badges">
        <span
          className={
            item.overdue ? "overview-badge overview-badge--overdue" : "overview-badge"
          }
        >
          {item.overdue ? "Просрочено" : (typeLabels[item.type] ?? item.type)}
        </span>
        {entry.needs_inbox && <span className="overview-badge">Нужно разобрать</span>}
        {priorityLabel && (
          <span className={`overview-priority overview-priority--${item.priority}`}>
            {priorityLabel}
          </span>
        )}
      </div>
      <h3 title={item.title}>{item.title}</h3>
      <p className="overview-row__meta">
        {formatDateTime(item.effective_at, dateTimePreferences)}
      </p>
      <div className="overview-row__actions">
        <button
          className="overview-action overview-action--primary"
          type="button"
          disabled={mutation.isPending}
          onClick={() => act(primaryAction)}
        >
          <Check size={14} aria-hidden /> {primaryLabel}
        </button>
        <button
          className="overview-action"
          type="button"
          disabled={mutation.isPending}
          onClick={() => {
            mutation.reset();
            setRescheduleOpen(true);
          }}
        >
          <Clock3 size={14} aria-hidden /> Перенести
        </button>
      </div>
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
