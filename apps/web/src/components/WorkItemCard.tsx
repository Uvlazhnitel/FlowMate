import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  CalendarClock,
  Check,
  Clock3,
  FilePlus2,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";
import { useState } from "react";

import {
  operationsKeys,
  runWorkItemAction,
  type ActionPayload,
  type WorkItemAction,
  type WorkItemCardData,
} from "../api/operations";
import { formatDateTime, type DateTimePreferences } from "../lib/dates";

const typeLabels: Record<string, string> = {
  task: "Задача",
  follow_up: "Follow-up",
  waiting: "Ожидание",
  question: "Вопрос",
  decision: "Решение",
  agenda_item: "Повестка",
};

type DialogMode = "note" | "result" | "decision" | "date" | null;

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
  const [content, setContent] = useState("");
  const [localDate, setLocalDate] = useState("");
  const [localTime, setLocalTime] = useState("09:00");
  const [hidden, setHidden] = useState(false);
  const [undoItem, setUndoItem] = useState<WorkItemCardData | null>(null);
  const [undoError, setUndoError] = useState(false);

  const mutation = useMutation({
    mutationFn: (payload: Omit<ActionPayload, "client_action_id">) =>
      runWorkItemAction(item.id, {
        ...payload,
        client_action_id: crypto.randomUUID(),
      }),
    onSuccess: (response, variables) => {
      if (
        ["complete", "waiting_received", "agenda_discussed", "question_answered"].includes(
          variables.action,
        ) &&
        response.work_item
      ) {
        setHidden(true);
        setUndoItem(response.work_item);
      } else {
        void queryClient.invalidateQueries({ queryKey: operationsKeys.all });
      }
      setDialog(null);
      setContent("");
    },
  });

  function act(action: WorkItemAction, extra: Partial<ActionPayload> = {}) {
    mutation.mutate({ action, expected_revision: item.revision, ...extra });
  }

  function confirmAction(action: "cancel" | "convert_to_task", message: string) {
    if (window.confirm(message)) act(action);
  }

  async function undo() {
    if (!undoItem) return;
    setUndoError(false);
    try {
      await runWorkItemAction(item.id, {
        action: "reopen",
        client_action_id: crypto.randomUUID(),
        expected_revision: undoItem.revision,
      });
      setHidden(false);
      setUndoItem(null);
      await queryClient.invalidateQueries({ queryKey: operationsKeys.all });
    } catch {
      setUndoError(true);
    }
  }

  if (hidden) {
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

  return (
    <article className={`work-card ${item.overdue ? "work-card--overdue" : ""}`}>
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
          onClick={() => act(primaryAction)}
        >
          <Check size={15} aria-hidden /> {primaryLabel}
        </button>
        <button className="card-action" type="button" onClick={() => setDialog("date")}>
          <Clock3 size={15} aria-hidden /> {agenda ? "Отложить" : "Перенести"}
        </button>
        <button
          className="card-action"
          type="button"
          onClick={() => setDialog(agenda ? "result" : "note")}
        >
          <FilePlus2 size={15} aria-hidden /> {agenda ? "Результат" : "Заметка"}
        </button>
        {item.reminder && (
          <button
            className="card-action"
            type="button"
            onClick={() =>
              act("snooze", {
                duration_minutes: defaultSnoozeMinutes,
                reminder_id: item.reminder?.id,
                reminder_revision: item.reminder?.revision,
              })
            }
          >
            Snooze
          </button>
        )}
        {agenda && (
          <>
            <button
              className="card-action"
              type="button"
              onClick={() => setDialog("decision")}
            >
              Решение
            </button>
            <button
              className="card-action"
              type="button"
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
          onClick={() =>
            confirmAction("cancel", "Отменить запись? Она останется в истории.")
          }
        >
          <Trash2 size={15} aria-hidden />
        </button>
      </div>
      {mutation.isError && (
        <p className="inline-error">
          Не удалось выполнить действие. Обновите данные и повторите.
        </p>
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
              {dialog === "date"
                ? "Новая дата"
                : dialog === "decision"
                  ? "Зафиксировать решение"
                  : "Добавить контекст"}
            </h2>
            {dialog === "date" ? (
              <div className="date-fields">
                <label>
                  Дата
                  <input
                    type="date"
                    value={localDate}
                    onChange={(event) => setLocalDate(event.target.value)}
                  />
                </label>
                <label>
                  Время
                  <input
                    type="time"
                    value={localTime}
                    onChange={(event) => setLocalTime(event.target.value)}
                  />
                </label>
              </div>
            ) : (
              <label className="dialog-field">
                Текст
                <textarea
                  autoFocus
                  value={content}
                  onChange={(event) => setContent(event.target.value)}
                />
              </label>
            )}
            <button
              className="button button--primary button--wide"
              type="button"
              disabled={
                mutation.isPending || (dialog === "date" ? !localDate : !content.trim())
              }
              onClick={() => {
                if (dialog === "date") {
                  act(agenda ? "defer" : "reschedule", {
                    local_date: localDate,
                    local_time: localTime,
                  });
                } else {
                  const action =
                    dialog === "decision"
                      ? "add_decision"
                      : dialog === "result"
                        ? "add_result"
                        : "add_note";
                  act(action, { content });
                }
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
