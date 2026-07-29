import { describe, expect, it, vi } from "vitest";

import { formatDateTime, formatRelative, formatRescheduledDateTime } from "./dates";

describe("date display preferences", () => {
  it("uses day-first dates and a 24-hour clock", () => {
    expect(
      formatDateTime("2026-07-22T15:04:00Z", {
        timezone: "UTC",
        dateDisplayFormat: "day_month_year",
        timeDisplayFormat: "24h",
      }),
    ).toBe("22.07.2026 15:04");
  });

  it("uses year-first dates and a 12-hour clock", () => {
    expect(
      formatDateTime("2026-07-22T15:04:00Z", {
        timezone: "UTC",
        dateDisplayFormat: "year_month_day",
        timeDisplayFormat: "12h",
      }),
    ).toBe("2026-07-22 03:04 PM");
  });

  it("keeps the relative just-now label", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-22T15:04:30Z"));
    expect(
      formatRelative("2026-07-22T15:04:00Z", {
        timezone: "UTC",
        dateDisplayFormat: "day_month_year",
        timeDisplayFormat: "24h",
      }),
    ).toBe("только что");
    vi.useRealTimers();
  });

  it("formats a reschedule confirmation with the real weekday and time", () => {
    expect(
      formatRescheduledDateTime("2026-08-07T15:00:00Z", {
        timezone: "UTC",
        dateDisplayFormat: "day_month_year",
        timeDisplayFormat: "24h",
      }),
    ).toBe("пятницу, 7 августа, 15:00");
  });
});
