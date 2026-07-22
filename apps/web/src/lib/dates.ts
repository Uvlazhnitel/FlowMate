export interface DateTimePreferences {
  timezone: string;
  dateDisplayFormat: "day_month_year" | "year_month_day";
  timeDisplayFormat: "24h" | "12h";
}

export function formatDateTime(
  value: string | null,
  preferences: DateTimePreferences,
): string {
  if (!value) return "Без даты";
  const instant = new Date(value);
  const date = new Intl.DateTimeFormat(
    preferences.dateDisplayFormat === "year_month_day" ? "sv-SE" : "ru-RU",
    {
      timeZone: preferences.timezone,
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    },
  ).format(instant);
  const time = new Intl.DateTimeFormat("ru-RU", {
    timeZone: preferences.timezone,
    hour: "2-digit",
    minute: "2-digit",
    hour12: preferences.timeDisplayFormat === "12h",
  }).format(instant);
  return `${date} ${time}`;
}

export function formatRelative(value: string, preferences: DateTimePreferences): string {
  const formatted = formatDateTime(value, preferences);
  const delta = Date.now() - new Date(value).getTime();
  if (delta >= 0 && delta < 60_000) return "только что";
  return formatted;
}
