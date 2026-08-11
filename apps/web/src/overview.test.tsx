import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OverviewResponse, WorkItemCardData } from "./api/operations";
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

const task: WorkItemCardData = {
  id: "0283942a-a7ec-45f4-81e2-4fd5f143cdd8",
  type: "task",
  status: "active",
  title: "Подготовить очень длинный план запуска без потери доступного полного названия",
  description: "Описание не должно появляться в обзорной строке",
  priority: "urgent",
  planner_status: "not_required",
  topic_id: null,
  topic_name: null,
  people: [],
  due_at: "2026-08-11T09:00:00Z",
  next_follow_up_at: null,
  waiting_since: null,
  completed_at: null,
  updated_at: "2026-08-11T08:00:00Z",
  effective_at: "2026-08-11T09:00:00Z",
  overdue: true,
  revision: 17,
  reminder: null,
};

function overviewResponse(): OverviewResponse {
  return {
    timezone: "Europe/Riga",
    today: {
      items: Array.from({ length: 8 }, (_, index) => ({
        item: {
          ...task,
          id: `${task.id.slice(0, -1)}${index}`,
          title: index ? `Сегодня ${index + 1}` : task.title,
        },
        needs_inbox: index === 0,
      })),
      total: 11,
      has_more: true,
    },
    tomorrow: {
      items: [
        {
          item: { ...task, id: "52802780-c750-4077-83a9-a951055bc6ca", title: "Завтра" },
          needs_inbox: false,
        },
      ],
      total: 1,
      has_more: false,
    },
    inbox: {
      items: [
        {
          id: "3195ebcf-15f4-42ef-bf5f-947589cd06bd",
          kind: "draft",
          title: "Черновик запуска",
          excerpt: "Исходная запись",
          status: "ready",
          reasons: ["unresolved_draft"],
          occurred_at: "2026-08-11T08:30:00Z",
          item_count: 2,
        },
      ],
      total: 1,
      has_more: false,
    },
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("Overview home", () => {
  it("renders three bounded columns, exact totals, and focused Inbox links", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      return Promise.resolve(
        path.includes("/auth/me")
          ? jsonResponse(authenticatedUser)
          : jsonResponse(overviewResponse()),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApplication("/overview");

    expect(await screen.findByRole("heading", { name: "Обзор", level: 1 })).toBeVisible();
    await screen.findByRole("heading", { name: "Сегодня", level: 2 });
    const columns = document.querySelectorAll(".overview-column");
    expect(columns).toHaveLength(3);
    const today = columns[0] as HTMLElement;
    expect(within(today).getAllByRole("article")).toHaveLength(8);
    expect(within(today).getByLabelText("11 записей")).toBeVisible();
    expect(within(today).getByRole("link", { name: /Показать все 11/ })).toHaveAttribute(
      "href",
      "/today",
    );
    const firstTitle = within(today).getByRole("heading", { name: task.title });
    expect(firstTitle).toHaveAttribute("title", task.title);
    expect(within(today).queryByText(task.description!)).not.toBeInTheDocument();
    expect(within(today).getByText("Нужно разобрать")).toBeVisible();
    const inboxLink = screen.getByRole("link", { name: /Черновик запуска/ });
    expect(inboxLink).toHaveAttribute(
      "href",
      "/inbox?kind=draft&focus=3195ebcf-15f4-42ef-bf5f-947589cd06bd",
    );
  });

  it("sends idempotent complete and reschedule payloads with Undo", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("/actions")) {
        const body = requestBody(init);
        return Promise.resolve(
          jsonResponse({
            changed: true,
            work_item: {
              ...task,
              status: body.action === "reopen" ? "active" : "done",
              revision: body.action === "reopen" ? 19 : 18,
            },
          }),
        );
      }
      return Promise.resolve(jsonResponse(overviewResponse()));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApplication("/overview");
    const title = await screen.findByRole("heading", { name: task.title });
    const row = title.closest("article") as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Готово" }));
    expect(await screen.findByText("Запись завершена")).toBeVisible();
    const completeCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        requestPath(input).includes("/actions") && requestBody(init).action === "complete",
    );
    const completePayload = requestBody(completeCall?.[1]);
    expect(completePayload).toMatchObject({
      action: "complete",
      expected_revision: 17,
    });
    expect(typeof completePayload.client_action_id).toBe("string");
    await user.click(screen.getByRole("button", { name: "Вернуть" }));
    expect(await screen.findByRole("heading", { name: task.title })).toBeVisible();

    const restoredRow = screen
      .getByRole("heading", { name: task.title })
      .closest("article") as HTMLElement;
    await user.click(within(restoredRow).getByRole("button", { name: "Перенести" }));
    await user.click(screen.getByRole("button", { name: "Завтра утром" }));
    await waitFor(() => {
      const rescheduleCall = fetchMock.mock.calls.find(
        ([input, init]) =>
          requestPath(input).includes("/actions") &&
          requestBody(init).action === "reschedule_preset",
      );
      const reschedulePayload = requestBody(rescheduleCall?.[1]);
      expect(reschedulePayload).toMatchObject({
        action: "reschedule_preset",
        preset: "tomorrow_morning",
        expected_revision: 17,
      });
      expect(typeof reschedulePayload.client_action_id).toBe("string");
    });
  });

  it("shows honest empty columns and keeps their full-list links", async () => {
    const empty = overviewResponse();
    empty.today = { items: [], total: 0, has_more: false };
    empty.tomorrow = { items: [], total: 0, has_more: false };
    empty.inbox = { items: [], total: 0, has_more: false };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) =>
        Promise.resolve(
          requestPath(input).includes("/auth/me")
            ? jsonResponse(authenticatedUser)
            : jsonResponse(empty),
        ),
      ),
    );

    renderApplication("/overview");

    expect(await screen.findByText("На сегодня всё разобрано.")).toBeVisible();
    expect(screen.getByText("На завтра ничего не запланировано.")).toBeVisible();
    expect(screen.getByText("Входящие разобраны.")).toBeVisible();
    expect(screen.getAllByRole("link", { name: /Открыть раздел/ })).toHaveLength(3);
  });
});
