export function formatDateTime(value: string | null, timezone: string): string {
  if (!value) return "Без даты";
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: timezone,
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function formatRelative(value: string, timezone: string): string {
  const formatted = formatDateTime(value, timezone);
  const delta = Date.now() - new Date(value).getTime();
  if (delta >= 0 && delta < 60_000) return "только что";
  return formatted;
}
