import { ApiError } from "../../api/client";

export const reasonLabels: Record<string, string> = {
  unresolved_draft: "Нужно уточнить",
  low_confidence: "Низкая уверенность",
  incomplete: "Не хватает данных",
  interrupted: "Диалог прерван",
  inbox_status: "Новая запись",
  missing_date: "Нет даты",
  missing_topic: "Нет темы",
  missing_person: "Нет человека",
  unstructured_note: "Неразобранная заметка",
};

export const itemTypes = [
  "task",
  "follow_up",
  "waiting",
  "question",
  "note",
  "decision",
  "agenda_item",
];

export const priorities = ["low", "normal", "high", "urgent"];

export const itemTypeLabels: Record<string, string> = {
  task: "Задача",
  follow_up: "Фоллоу-ап",
  waiting: "Ожидание",
  question: "Вопрос",
  note: "Заметка",
  decision: "Решение",
  agenda_item: "Повестка",
};

export function actionError(errors: unknown[]): string | null {
  const error = errors.find(Boolean);
  if (!error) return null;
  if (error instanceof ApiError && error.status === 409) return error.message;
  return "Действие не выполнено. Обновите данные и повторите.";
}

export function localParts(value: string | null, timezone: string) {
  if (!value) return { date: "", time: "09:00" };
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(value));
  const read = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return {
    date: `${read("year")}-${read("month")}-${read("day")}`,
    time: `${read("hour")}:${read("minute")}`,
  };
}
