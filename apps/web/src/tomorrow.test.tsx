import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkItemCardData } from "./api/operations";
import { authenticatedUser, jsonResponse, renderApplication } from "./test/render";

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

const tomorrowItem: WorkItemCardData = {
  id: "9f2c2d2c-e6f9-4c73-8e45-86cf75bd816a",
  type: "task",
  status: "active",
  title: "Подготовить завтрашний отчёт",
  description: null,
  priority: "high",
  planner_status: "not_required",
  topic_id: null,
  topic_name: null,
  people: [],
  due_at: "2026-08-03T08:00:00Z",
  next_follow_up_at: null,
  waiting_since: null,
  completed_at: null,
  updated_at: "2026-08-02T08:00:00Z",
  effective_at: "2026-08-03T08:00:00Z",
  overdue: false,
  revision: 1,
  reminder: null,
};

function page(items: WorkItemCardData[], offset: number, hasMore: boolean) {
  return {
    items,
    limit: 20,
    offset,
    has_more: hasMore,
    timezone: "Europe/Riga",
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Tomorrow page", () => {
  it("renders tomorrow items and loads the next page", async () => {
    const secondItem = {
      ...tomorrowItem,
      id: "2edb5f34-e310-4d88-a90a-854a23dd3bb4",
      title: "Позвонить клиенту",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("offset=20"))
        return Promise.resolve(jsonResponse(page([secondItem], 20, false)));
      return Promise.resolve(jsonResponse(page([tomorrowItem], 0, true)));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApplication("/tomorrow");

    expect(await screen.findByRole("heading", { name: "Завтра" })).toBeVisible();
    expect(await screen.findByText("Подготовить завтрашний отчёт")).toBeVisible();
    const card = screen.getByText("Подготовить завтрашний отчёт").closest(".work-card");
    expect(card).toHaveClass("work-card--compact");
    expect(card?.closest(".work-list")).toHaveClass("work-list--compact-grid");
    expect(screen.getByText("Высокий")).toBeVisible();
    expect(screen.getByRole("link", { name: "Вернуться к сегодня" })).toHaveAttribute(
      "href",
      "/today",
    );

    await userEvent.click(screen.getByRole("button", { name: "Показать ещё" }));
    expect(await screen.findByText("Позвонить клиенту")).toBeVisible();
  });

  it("shows a decisive empty state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = requestPath(input);
        return Promise.resolve(
          path.includes("/auth/me")
            ? jsonResponse(authenticatedUser)
            : jsonResponse(page([], 0, false)),
        );
      }),
    );

    renderApplication("/tomorrow");

    expect(await screen.findByText("На завтра ничего не запланировано")).toBeVisible();
  });
});
