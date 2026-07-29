import { CalendarDays, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { ReschedulePreset, RescheduleSelection } from "../api/operations";
import { formatDateTime, type DateTimePreferences } from "../lib/dates";

const presets: { value: ReschedulePreset; label: string }[] = [
  { value: "later_today", label: "Позже сегодня" },
  { value: "tomorrow_morning", label: "Завтра утром" },
  { value: "next_working_day", label: "Следующий рабочий день" },
  { value: "next_week", label: "Через неделю" },
];

function currentLocalParts(
  value: string | null,
  preferences: DateTimePreferences,
): { date: string; time: string } {
  if (!value) return { date: "", time: "09:00" };
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: preferences.timezone,
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

export function RescheduleDialog({
  dialogId,
  currentDueAt,
  dateTimePreferences,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  dialogId: string;
  currentDueAt: string | null;
  dateTimePreferences: DateTimePreferences;
  pending: boolean;
  error: string | null;
  onSubmit: (selection: RescheduleSelection) => void;
  onCancel: () => void;
}) {
  const initial = currentLocalParts(currentDueAt, dateTimePreferences);
  const [phrase, setPhrase] = useState("");
  const [showExact, setShowExact] = useState(false);
  const [localDate, setLocalDate] = useState(initial.date);
  const [localTime, setLocalTime] = useState(initial.time);
  const phraseInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    phraseInput.current?.focus();
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !pending) onCancel();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onCancel, pending]);

  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        className="action-dialog reschedule-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${dialogId}-title`}
        aria-describedby={`${dialogId}-current`}
        aria-busy={pending}
      >
        <button
          className="dialog-close"
          type="button"
          aria-label="Закрыть"
          disabled={pending}
          onClick={onCancel}
        >
          <X size={18} aria-hidden />
        </button>
        <p className="reschedule-dialog__eyebrow">Новый срок</p>
        <h2 id={`${dialogId}-title`}>Перенести задачу</h2>
        <p id={`${dialogId}-current`} className="reschedule-dialog__current">
          Сейчас: <strong>{formatDateTime(currentDueAt, dateTimePreferences)}</strong>
        </p>

        <div className="reschedule-presets" aria-label="Быстрый выбор срока">
          {presets.map((preset) => (
            <button
              key={preset.value}
              className="reschedule-preset"
              type="button"
              disabled={pending}
              onClick={() =>
                onSubmit({ action: "reschedule_preset", preset: preset.value })
              }
            >
              {preset.label}
            </button>
          ))}
        </div>

        <form
          className="reschedule-text"
          onSubmit={(event) => {
            event.preventDefault();
            const normalized = phrase.trim();
            if (normalized) {
              onSubmit({ action: "reschedule_text", phrase: normalized });
            }
          }}
        >
          <label htmlFor={`${dialogId}-phrase`}>Или напишите обычными словами</label>
          <div className="reschedule-text__row">
            <input
              ref={phraseInput}
              id={`${dialogId}-phrase`}
              value={phrase}
              maxLength={500}
              disabled={pending}
              placeholder="в пятницу после обеда"
              onChange={(event) => setPhrase(event.target.value)}
            />
            <button type="submit" disabled={pending || !phrase.trim()}>
              Перенести
            </button>
          </div>
        </form>

        <button
          className="reschedule-exact-toggle"
          type="button"
          aria-expanded={showExact}
          disabled={pending}
          onClick={() => setShowExact((value) => !value)}
        >
          <CalendarDays size={17} aria-hidden />
          Выбрать точную дату и время
        </button>

        {showExact && (
          <div className="reschedule-exact">
            <label>
              Дата
              <input
                type="date"
                value={localDate}
                disabled={pending}
                onChange={(event) => setLocalDate(event.target.value)}
              />
            </label>
            <label>
              Время
              <input
                type="time"
                value={localTime}
                disabled={pending}
                onChange={(event) => setLocalTime(event.target.value)}
              />
            </label>
            <button
              className="button button--primary button--wide"
              type="button"
              disabled={pending || !localDate || !localTime}
              onClick={() =>
                onSubmit({
                  action: "reschedule",
                  local_date: localDate,
                  local_time: localTime,
                })
              }
            >
              Перенести на выбранное время
            </button>
          </div>
        )}

        {error && (
          <p className="reschedule-dialog__error" role="alert">
            {error}
          </p>
        )}
        <button
          className="reschedule-cancel"
          type="button"
          disabled={pending}
          onClick={onCancel}
        >
          Отмена
        </button>
      </section>
    </div>
  );
}
