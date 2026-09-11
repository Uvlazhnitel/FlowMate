import { describe, expect, it, vi } from "vitest";

import {
  formatDateTime,
  formatOverviewDue,
  formatRelative,
  formatRescheduledDateTime,
} from "./dates";

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

  it("uses provenance to keep Overview due labels compact", () => {
    const preferences = {
      timezone: "UTC",
      dateDisplayFormat: "day_month_year" as const,
      timeDisplayFormat: "24h" as const,
    };
    expect(formatOverviewDue("2026-08-15T23:59:59Z", preferences, true, false)).toBe(
      "15.08.2026",
    );
    expect(formatOverviewDue("2026-08-15T14:30:00Z", preferences, true, true)).toBe(
      "15.08.2026 14:30",
    );
    expect(formatOverviewDue("2026-08-15T23:59:59Z", preferences, false, false)).toBe("");
  });
});
