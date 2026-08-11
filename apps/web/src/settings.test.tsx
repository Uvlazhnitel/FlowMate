import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkItemCardData } from "./api/operations";
import { authenticatedUser, jsonResponse, renderApplication } from "./test/render";

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

function requestBody(init?: RequestInit): Record<string, unknown> {
  return JSON.parse(typeof init?.body === "string" ? init.body : "{}") as Record<
    string,
    unknown
  >;
}

function page(items: object[], hasMore = false) {
  return jsonResponse({ items, limit: 20, offset: 0, has_more: hasMore });
}

const item: WorkItemCardData = {
  id: "0283942a-a7ec-45f4-81e2-4fd5f143cdd8",
  type: "task",
  status: "active",
  title: "Подготовить миграцию",
  description: "Перенести карточку вручную",
  priority: "high",
  planner_status: "not_required",
  topic_id: "c46a29ef-bfed-440c-b289-5a17d7808a78",
  topic_name: "Migration",
  people: [],
  due_at: "2026-07-24T09:00:00Z",
  next_follow_up_at: null,
  waiting_since: null,
  completed_at: null,
  updated_at: "2026-07-22T08:00:00Z",
  effective_at: "2026-07-24T09:00:00Z",
  overdue: false,
  revision: 10,
  reminder: null,
};

const settings = {
  preferences: {
    timezone: "Europe/Riga",
    morning_digest_enabled: true,
    morning_digest_time: "09:00:00",
    evening_digest_enabled: false,
    evening_digest_time: "18:00:00",
    quiet_hours_enabled: true,
    quiet_hours_start: "22:00:00",
    quiet_hours_end: "08:00:00",
    default_reminder_time: "09:00:00",
    default_snooze_minutes: 60,
    send_empty_digests: false,
    date_display_format: "day_month_year",
    time_display_format: "24h",
  },
  providers: { ai_configured: true, speech_configured: false },
};

const topics = [
  {
    id: item.topic_id,
    name: "Migration",
    description: null,
    aliases: ["move"],
    is_active: true,
  },
];
const people = [
  {
    id: "19386434-7152-4ab2-a82c-293032ec2105",
    display_name: "Nina",
    role: "Owner",
    notes: null,
    aliases: ["n"],
    is_active: true,
  },
];

function optionsResponse(path: string): Response | undefined {
  if (path.includes("/settings/topics")) return page(topics);
  if (path.includes("/settings/people")) return page(people);
  return undefined;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.cookie = "flowmate_csrf=; Max-Age=0; path=/";
});

describe("Settings", () => {
  it("shows provider booleans and saves validated settings with a dirty warning", async () => {
    document.cookie = "flowmate_csrf=test-csrf; path=/";
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      const options = optionsResponse(path);
      if (options) return Promise.resolve(options);
      if (path.includes("/settings/preferences") && init?.method === "PUT") {
        return Promise.resolve(
          jsonResponse({
            ...settings,
            preferences: { ...settings.preferences, ...requestBody(init) },
          }),
        );
      }
      if (path.endsWith("/api/v1/settings")) return Promise.resolve(jsonResponse(settings));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApplication("/settings");

    expect(await screen.findByText("AI provider")).toBeVisible();
    expect(
      screen.getByText("Ключи и модели никогда не передаются в браузер."),
    ).toBeVisible();
    expect(screen.getByLabelText("Время напоминаний по умолчанию")).toHaveValue("09:00");
    const timezone = screen.getByLabelText("Часовой пояс");
    await user.clear(timezone);
    await user.type(timezone, "America/New_York");
    expect(screen.getByText("Не сохранено")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Сохранить настройки" }));
    await waitFor(() => {
      const update = fetchMock.mock.calls.find(
        ([input, init]) =>
          requestPath(input).includes("/settings/preferences") && init?.method === "PUT",
      );
      expect(requestBody(update?.[1])).toMatchObject({ timezone: "America/New_York" });
      expect(new Headers(update?.[1]?.headers).get("X-CSRF-Token")).toBe("test-csrf");
    });
    expect(screen.queryByText("Не сохранено")).not.toBeInTheDocument();
  });

  it("loads Settings entities incrementally and guards dirty entity forms", async () => {
    const secondTopic = {
      ...topics[0],
      id: "cf9fd976-a85e-4d09-80dd-04e09b880fec",
      name: "Operations",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.endsWith("/api/v1/settings")) return Promise.resolve(jsonResponse(settings));
      if (path.includes("/settings/topics")) {
        return Promise.resolve(
          path.includes("offset=25")
            ? jsonResponse({ items: [secondTopic], limit: 25, offset: 25, has_more: false })
            : jsonResponse({ items: topics, limit: 25, offset: 0, has_more: true }),
        );
      }
      if (path.includes("/settings/people")) return Promise.resolve(page([]));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();

    renderApplication("/settings");

    expect(await screen.findByDisplayValue("Migration")).toBeVisible();
    expect(
      screen.getByText(
        "Утренний и вечерний дайджесты объединяют личные и рабочие записи в одном сообщении.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Утренний дайджест")).toBeVisible();
    expect(screen.getByText("Людей пока нет")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Показать ещё" }));
    expect(await screen.findByDisplayValue("Operations")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("offset=25"),
      expect.anything(),
    );

    await user.type(screen.getByLabelText("Название темы Migration"), " updated");
    await user.click(screen.getAllByRole("link", { name: "Сегодня" })[0]!);
    expect(confirm).toHaveBeenCalledWith(
      "Есть несохранённые изменения. Покинуть страницу?",
    );
    expect(screen.getByRole("heading", { name: "Настройки" })).toBeVisible();
  });
});
