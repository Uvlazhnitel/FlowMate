import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CalendarClock,
  Check,
  Clock3,
  FilePlus2,
  ListPlus,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  operationsKeys,
  runWorkItemAction,
  type ActionPayload,
  type RescheduleSelection,
  type WorkItemAction,
  type WorkItemCardData,
} from "../api/operations";
import { ApiError } from "../api/client";
import { remainingKeys } from "../api/remaining";
import {
  formatDateTime,
  formatRescheduledDateTime,
  type DateTimePreferences,
} from "../lib/dates";
import { RescheduleDialog } from "./RescheduleDialog";

const typeLabels: Record<string, string> = {
  task: "Задача",
  follow_up: "Фоллоу-ап",
  waiting: "Ожидание",
  question: "Вопрос",
  decision: "Решение",
  agenda_item: "Повестка",
};

const UNDO_WINDOW_MS = 8_000;
const COMPLETION_ANIMATION_MS = 650;
const PLANNER_TYPES = new Set(["task", "follow_up", "waiting"]);

type DialogMode = "note" | "result" | "decision" | null;

function isRescheduleAction(action: WorkItemAction) {
  return ["reschedule", "reschedule_preset", "reschedule_text"].includes(action);
}

export function StatusBadge({ item }: { item: WorkItemCardData }) {
  return (
    <span className={`status-badge ${item.overdue ? "status-badge--overdue" : ""}`}>
      {item.overdue ? "Просрочено" : (typeLabels[item.type] ?? item.type)}
    </span>
  );
}

export function WorkItemCard({
  item,
  dateTimePreferences,
  agenda = false,
  defaultSnoozeMinutes = 60,
}: {
  item: WorkItemCardData;
  dateTimePreferences: DateTimePreferences;
  agenda?: boolean;
  defaultSnoozeMinutes?: number;
}) {
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<DialogMode>(null);
  const [rescheduleOpen, setRescheduleOpen] = useState(false);
  const [rescheduleStatus, setRescheduleStatus] = useState<string | null>(null);
  const [content, setContent] = useState("");
  const [hidden, setHidden] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [undoItem, setUndoItem] = useState<WorkItemCardData | null>(null);
  const [undoError, setUndoError] = useState(false);
  const completionTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (completionTimer.current !== null) clearTimeout(completionTimer.current);
      if (undoTimer.current !== null) clearTimeout(undoTimer.current);
    },
    [],
  );

  function startUndoWindow() {
    undoTimer.current = setTimeout(() => {
      undoTimer.current = null;
      setUndoItem(null);
      void queryClient.invalidateQueries({ queryKey: operationsKeys.all });
    }, UNDO_WINDOW_MS);
  }

  function showUndo(itemToUndo: WorkItemCardData, animateCompletion: boolean) {
    if (completionTimer.current !== null) clearTimeout(completionTimer.current);
    if (undoTimer.current !== null) clearTimeout(undoTimer.current);
    setUndoItem(itemToUndo);

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!animateCompletion || reduceMotion) {
      setHidden(true);
      startUndoWindow();
      return;
    }

    setCompleting(true);
    completionTimer.current = setTimeout(() => {
      completionTimer.current = null;
      setCompleting(false);
      setHidden(true);
      startUndoWindow();
    }, COMPLETION_ANIMATION_MS);
  }

  const mutation = useMutation({
    mutationFn: (payload: Omit<ActionPayload, "client_action_id">) =>
      runWorkItemAction(item.id, {
        ...payload,
        client_action_id: crypto.randomUUID(),
      }),
    onSuccess: (response, variables) => {
      if (variables.action.startsWith("planner_")) {
        void queryClient.invalidateQueries({ queryKey: remainingKeys.all });
      }
      if (isRescheduleAction(variables.action) && response.work_item?.effective_at) {
        setRescheduleStatus(
          `✓ Перенесено на ${formatRescheduledDateTime(
            response.work_item.effective_at,
            dateTimePreferences,
          )}`,
        );
        setRescheduleOpen(false);
      }
      if (
        ["complete", "waiting_received", "agenda_discussed", "question_answered"].includes(
          variables.action,
        ) &&
        response.work_item
      ) {
        showUndo(response.work_item, variables.action === "complete");
      } else {
        void queryClient.invalidateQueries({ queryKey: operationsKeys.all });
      }
      setDialog(null);
      setContent("");
    },
    onError: (error, variables) => {
      if (
        isRescheduleAction(variables.action) &&
        error instanceof ApiError &&
        error.status === 409
      ) {
        void queryClient.invalidateQueries({ queryKey: operationsKeys.all });
      }
    },
  });

  function act(action: WorkItemAction, extra: Partial<ActionPayload> = {}) {
    mutation.mutate({ action, expected_revision: item.revision, ...extra });
  }

  function confirmAction(action: "cancel" | "convert_to_task", message: string) {
    if (window.confirm(message)) act(action);
  }

  function openReschedule() {
    mutation.reset();
    setRescheduleStatus(null);
    setRescheduleOpen(true);
  }

  function submitReschedule(selection: RescheduleSelection) {
    mutation.mutate({
      ...selection,
      expected_revision: item.revision,
    });
  }

  const rescheduleError =
    rescheduleOpen && mutation.isError
      ? mutation.error instanceof ApiError && mutation.error.status === 409
        ? "Задача уже изменилась. Данные обновлены — выберите срок ещё раз."
        : mutation.error instanceof ApiError
          ? mutation.error.message
          : "Не удалось перенести задачу. Попробуйте ещё раз."
      : null;

  async function undo() {
    if (!undoItem) return;
    if (completionTimer.current !== null) {
      clearTimeout(completionTimer.current);
      completionTimer.current = null;
    }
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
      setCompleting(false);
      setHidden(false);
      setUndoItem(null);
      await queryClient.invalidateQueries({ queryKey: operationsKeys.all });
    } catch {
      setUndoError(true);
    }
  }

  if (hidden) {
    if (!undoItem) return null;
    return (
      <div className="undo-card" role="status">
        <span>Запись завершена</span>
        <button className="text-action" type="button" onClick={() => void undo()}>
          <RotateCcw size={15} aria-hidden /> Вернуть
        </button>
        {undoError && <span className="inline-error">Не удалось вернуть запись.</span>}
      </div>
    );
  }

  const primaryAction: WorkItemAction = agenda
    ? item.type === "question"
      ? "question_answered"
      : "agenda_discussed"
    : item.type === "waiting"
      ? "waiting_received"
      : "complete";
  const primaryLabel = agenda
    ? item.type === "question"
      ? "Отвечено"
      : "Обсуждено"
    : item.type === "waiting"
      ? "Получено"
      : "Готово";
  const interactionsDisabled = mutation.isPending || completing;

  return (
    <article
      className={`work-card ${item.overdue ? "work-card--overdue" : ""} ${completing ? "work-card--completing" : ""}`}
      aria-busy={interactionsDisabled}
    >
      {completing && (
        <div className="work-card__completion" role="status" aria-live="polite">
          <span className="work-card__completion-icon">
            <Check size={18} strokeWidth={2.5} aria-hidden />
          </span>
          <span>Выполнено</span>
        </div>
      )}
      <div className="work-card__topline">
        <StatusBadge item={item} />
        <span className={`priority priority--${item.priority}`}>{item.priority}</span>
      </div>
      <h3>{item.title}</h3>
      {item.description && <p className="work-card__description">{item.description}</p>}
      <div className="work-card__meta">
        <span>
          <CalendarClock size={14} aria-hidden />
          {formatDateTime(item.effective_at, dateTimePreferences)}
        </span>
        {item.topic_name && <span>#{item.topic_name}</span>}
        {item.people.length > 0 && (
          <span>{item.people.map((person) => person[1]).join(", ")}</span>
        )}
      </div>
      <div className="work-card__actions">
        <button
          className="card-action card-action--primary"
          type="button"
          disabled={interactionsDisabled}
          onClick={() => act(primaryAction)}
        >
          <Check size={15} aria-hidden /> {primaryLabel}
        </button>
        <button
          className="card-action"
          type="button"
          disabled={interactionsDisabled}
          onClick={openReschedule}
        >
          <Clock3 size={15} aria-hidden /> {agenda ? "Отложить" : "Перенести"}
        </button>
        <button
          className="card-action"
          type="button"
          disabled={interactionsDisabled}
          onClick={() => setDialog(agenda ? "result" : "note")}
        >
          <FilePlus2 size={15} aria-hidden /> {agenda ? "Результат" : "Заметка"}
        </button>
        {PLANNER_TYPES.has(item.type) && item.planner_status === "not_required" && (
          <button
            className="card-action"
            type="button"
            disabled={interactionsDisabled}
            onClick={() => act("planner_needs_transfer")}
          >
            <ListPlus size={15} aria-hidden /> Добавить в Planner
          </button>
        )}
        {item.reminder && (
          <button
            className="card-action"
            type="button"
            disabled={interactionsDisabled}
            onClick={() =>
              act("snooze", {
                duration_minutes: defaultSnoozeMinutes,
                reminder_id: item.reminder?.id,
                reminder_revision: item.reminder?.revision,
              })
            }
          >
            Отложить напоминание
          </button>
        )}
        {agenda && (
          <>
            <button
              className="card-action"
              type="button"
              disabled={interactionsDisabled}
              onClick={() => setDialog("decision")}
            >
              Решение
            </button>
            <button
              className="card-action"
              type="button"
              disabled={interactionsDisabled}
              onClick={() =>
                confirmAction("convert_to_task", "Преобразовать запись в задачу?")
              }
            >
              В задачу
            </button>
          </>
        )}
        <button
          className="card-action card-action--danger"
          type="button"
          aria-label="Отменить запись"
          disabled={interactionsDisabled}
          onClick={() =>
            confirmAction("cancel", "Отменить запись? Она останется в истории.")
          }
        >
          <Trash2 size={15} aria-hidden />
        </button>
      </div>
      {rescheduleStatus && (
        <p className="reschedule-status" role="status">
          {rescheduleStatus}
        </p>
      )}
      {mutation.isError && !rescheduleOpen && (
        <p className="inline-error">
          Не удалось выполнить действие. Обновите данные и повторите.
        </p>
      )}
      {rescheduleOpen && (
        <RescheduleDialog
          dialogId={`reschedule-${item.id}`}
          currentDueAt={item.effective_at}
          dateTimePreferences={dateTimePreferences}
          pending={mutation.isPending}
          error={rescheduleError}
          onSubmit={submitReschedule}
          onCancel={() => {
            if (!mutation.isPending) {
              mutation.reset();
              setRescheduleOpen(false);
            }
          }}
        />
      )}
      {dialog && (
        <div className="dialog-backdrop" role="presentation">
          <div
            className="action-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby={`dialog-${item.id}`}
          >
            <button
              className="dialog-close"
              type="button"
              aria-label="Закрыть"
              onClick={() => setDialog(null)}
            >
              <X size={18} aria-hidden />
            </button>
            <h2 id={`dialog-${item.id}`}>
              {dialog === "decision" ? "Зафиксировать решение" : "Добавить контекст"}
            </h2>
            <label className="dialog-field">
              Текст
              <textarea
                autoFocus
                value={content}
                onChange={(event) => setContent(event.target.value)}
              />
            </label>
            <button
              className="button button--primary button--wide"
              type="button"
              disabled={mutation.isPending || !content.trim()}
              onClick={() => {
                const action =
                  dialog === "decision"
                    ? "add_decision"
                    : dialog === "result"
                      ? "add_result"
                      : "add_note";
                act(action, { content });
              }}
            >
              Сохранить
            </button>
          </div>
        </div>
      )}
    </article>
  );
}
