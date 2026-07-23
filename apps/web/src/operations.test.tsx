import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkItemCardData } from "./api/operations";
import { authenticatedUser, jsonResponse, renderApplication } from "./test/render";

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

function requestBody(init?: RequestInit): string {
  return typeof init?.body === "string" ? init.body : "{}";
}

const workItem: WorkItemCardData = {
  id: "0283942a-a7ec-45f4-81e2-4fd5f143cdd8",
  type: "task",
  status: "active",
  title: "Подготовить запуск",
  description: "Проверить финальный список",
  priority: "high",
  topic_id: "c46a29ef-bfed-440c-b289-5a17d7808a78",
  topic_name: "Launch",
  people: [],
  due_at: "2026-07-21T09:00:00Z",
  next_follow_up_at: null,
  waiting_since: null,
  completed_at: null,
  updated_at: "2026-07-21T08:00:00Z",
  effective_at: "2026-07-21T09:00:00Z",
  overdue: true,
  revision: 1,
  reminder: null,
};

function page(items: object[], hasMore = false, offset = 0) {
  return jsonResponse({
    items,
    limit: 20,
    offset,
    has_more: hasMore,
    timezone: "Europe/Riga",
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("operational screens", () => {
  it("uses the Today URL filter and supports complete with Undo", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("/actions")) {
        const body = JSON.parse(requestBody(init)) as { action: string };
        return Promise.resolve(
          jsonResponse({
            changed: true,
            work_item: {
              ...workItem,
              status: body.action === "reopen" ? "inbox" : "done",
              revision: body.action === "reopen" ? 3 : 2,
            },
          }),
        );
      }
      if (path.includes("section=overdue")) return Promise.resolve(page([workItem]));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApplication("/today?section=overdue");

    expect(await screen.findByText("Подготовить запуск")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("section=overdue"),
      expect.anything(),
    );
    await user.click(screen.getByRole("button", { name: "Готово" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Запись завершена");
    await user.click(screen.getByRole("button", { name: "Вернуть" }));
    expect(await screen.findByText("Подготовить запуск")).toBeVisible();
    const actionCalls = fetchMock.mock.calls.filter(([input]) =>
      requestPath(input).includes("/actions"),
    );
    expect(actionCalls).toHaveLength(2);
    expect(JSON.parse(requestBody(actionCalls[1]?.[1]))).toMatchObject({
      action: "reopen",
      expected_revision: 2,
    });
  });

  it("requires confirmation before cancelling a work item", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("section=overdue")) return Promise.resolve(page([workItem]));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderApplication("/today?section=overdue");

    await user.click(await screen.findByRole("button", { name: "Отменить запись" }));

    expect(confirm).toHaveBeenCalledWith("Отменить запись? Она останется в истории.");
    expect(
      fetchMock.mock.calls.some(([input]) => requestPath(input).includes("/actions")),
    ).toBe(false);
  });

  it("loads additional detail records and exposes mobile navigation", async () => {
    const secondItem = {
      ...workItem,
      id: "52802780-c750-4077-83a9-a951055bc6ca",
      title: "Второй шаг",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.endsWith("/api/v1/topics/c46a29ef-bfed-440c-b289-5a17d7808a78")) {
        return Promise.resolve(
          jsonResponse({
            id: workItem.topic_id,
            name: "Launch",
            description: "Запуск продукта",
          }),
        );
      }
      if (path.includes("/content") && path.includes("offset=20")) {
        return Promise.resolve(page([secondItem], false, 20));
      }
      if (path.includes("/content")) return Promise.resolve(page([workItem], true));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderApplication(`/topics/${workItem.topic_id}`);

    expect(await screen.findByText("Подготовить запуск")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Показать ещё" }));
    expect(await screen.findByText("Второй шаг")).toBeVisible();

    const mobileNavigation = screen
      .getAllByRole("navigation", { name: "Основная навигация" })
      .find((navigation) => navigation.classList.contains("mobile-nav"));
    expect(mobileNavigation).toBeDefined();
    const mobile = within(mobileNavigation!);
    for (const label of ["Обзор", "Сегодня", "Повестка", "Темы", "Люди"]) {
      expect(mobile.getByRole("link", { name: label })).toBeVisible();
    }
    expect(mobile.getByText("Ещё")).toBeVisible();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("offset=20"),
        expect.anything(),
      ),
    );
  });

  it("filters the people directory through URL scopes and resets pagination", async () => {
    const person = {
      id: "962ef4d1-cce0-4f4a-9085-2917115f61b9",
      display_name: "Анна",
      role: "Владелец",
      open_item_count: 2,
      follow_up_count: 1,
      waiting_count: 1,
      question_count: 0,
      last_activity: "2026-07-21T08:00:00Z",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("/api/v1/people")) return Promise.resolve(page([person]));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApplication("/people?scope=all&page=2");

    expect(await screen.findByText("Анна")).toBeVisible();
    expect(screen.getByText("2 открытых")).toBeVisible();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/scope=all.*offset=40/),
        expect.anything(),
      ),
    );

    await user.click(screen.getByRole("button", { name: "В работе" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringMatching(/scope=work.*offset=0/),
        expect.anything(),
      ),
    );
    expect(screen.getByRole("button", { name: "В работе" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("shows a scope-specific empty state for recent people", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      if (requestPath(input).includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApplication("/people?scope=recent");

    expect(
      await screen.findByText("За последние 90 дней активности с людьми не было."),
    ).toBeVisible();
  });
});
