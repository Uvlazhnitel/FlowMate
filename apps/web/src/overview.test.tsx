import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
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
  title: "Подготовить презентацию",
  description: "Скрытое описание",
  priority: "urgent",
  planner_status: "not_required",
  topic_id: null,
  topic_name: "Запуск",
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
  workspace: "personal",
};

const inboxTask: WorkItemCardData = {
  ...task,
  id: "d317bc8f-341c-4ba0-ab8c-081cf0297650",
  status: "inbox",
  title: "Разобрать входящую задачу",
  due_at: null,
  effective_at: null,
  overdue: false,
  revision: 23,
};

const completedTask: WorkItemCardData = {
  ...task,
  id: "a317bc8f-341c-4ba0-ab8c-081cf0297650",
  status: "done",
  title: "Готовая задача",
  completed_at: "2026-08-11T10:00:00Z",
  revision: 24,
};

function overviewResponse(): OverviewResponse {
  return {
    timezone: "Europe/Riga",
    workspace_counts: { all: 4, work: 1, personal: 3 },
    today: {
      items: [{ item: task, needs_inbox: false }],
      total: 1,
      has_more: false,
      completed_items: [completedTask],
      completed_total: 1,
      completed_has_more: false,
    },
    tomorrow: {
      items: [
        {
          item: {
            ...task,
            id: "52802780-c750-4077-83a9-a951055bc6ca",
            title: "Завтрашняя задача",
            workspace: "work",
          },
          needs_inbox: false,
        },
      ],
      total: 1,
      has_more: false,
      completed_items: [],
      completed_total: 0,
      completed_has_more: false,
    },
    inbox: {
      items: [
        {
          id: inboxTask.id,
          kind: "work_item",
          item: inboxTask,
          title: inboxTask.title,
          excerpt: "",
          status: "inbox",
          reasons: ["inbox_status"],
          occurred_at: inboxTask.updated_at,
          item_count: 1,
          workspace: inboxTask.workspace,
        },
      ],
      total: 1,
      has_more: false,
      completed_items: [],
      completed_total: 0,
      completed_has_more: false,
    },
  };
}

function setupFetch(
  response = overviewResponse(),
  action?: (body: Record<string, unknown>) => Response | Promise<Response>,
) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = requestPath(input);
    if (path.includes("/auth/me")) return Promise.resolve(jsonResponse(authenticatedUser));
    if (path.includes("/actions") && action)
      return Promise.resolve(action(requestBody(init)));
    if (path.includes("/captures/text"))
      return Promise.resolve(
        jsonResponse(
          {
            client_capture_id: requestBody(init).client_capture_id,
            duplicate: false,
            disposition: "created",
            draft_id: crypto.randomUUID(),
            work_item_ids: [crypto.randomUUID()],
          },
          201,
        ),
      );
    return Promise.resolve(jsonResponse(response));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("Overview board", () => {
  it("renders three columns, real counts, one workspace badge, and focusable tasks", async () => {
    setupFetch();
    renderApplication("/overview");

    expect(await screen.findByRole("heading", { name: "Обзор", level: 1 })).toBeVisible();
    await screen.findByRole("heading", { name: "Сегодня", level: 2 });
    expect(document.querySelector(".overview-board__frame")).toBeVisible();
    expect(document.querySelectorAll(".overview-column")).toHaveLength(3);
    expect(screen.getByRole("button", { name: /^Все\s*4$/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const row = screen.getByRole("heading", { name: task.title }).closest("article")!;
    expect(within(row).getAllByText("Личное")).toHaveLength(1);
    expect(within(row).queryByText("Запуск")).not.toBeInTheDocument();
    expect(within(row).queryByText("Срочно")).not.toBeInTheDocument();
    expect(row).toHaveAttribute("tabindex", "0");
    expect(row).not.toHaveAttribute("draggable");
  });

  it("moves a task with the keyboard and confirms once", async () => {
    let actionBody: Record<string, unknown> = {};
    setupFetch(overviewResponse(), (body) => {
      actionBody = body;
      return jsonResponse({ changed: true, work_item: { ...task, revision: 18 } });
    });
    renderApplication("/overview");

    const row = (await screen.findByRole("heading", { name: task.title })).closest(
      "article",
    )!;
    fireEvent.keyDown(row, { key: " " });
    await screen.findByText(/Режим перемещения/);
    fireEvent.keyDown(row, { key: "ArrowRight" });
    const movedRow = await screen.findByRole("heading", { name: task.title });
    fireEvent.keyDown(movedRow.closest("article")!, { key: "Enter" });

    await waitFor(() =>
      expect(actionBody).toMatchObject({
        action: "move_keyboard",
        target: "tomorrow",
        expected_revision: 17,
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Перемещение сохранено");
  });

  it("filters the view without changing the creation workspace", async () => {
    const fetchMock = setupFetch();
    const user = userEvent.setup();
    renderApplication("/overview");

    await screen.findByRole("heading", { name: task.title });
    await user.click(screen.getByRole("button", { name: /^Работа\s*1$/ }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          requestPath(input).includes("/api/v1/overview?workspace=work"),
        ),
      ).toBe(true),
    );
    expect(screen.getByRole("combobox", { name: "Пространство задачи" })).toHaveValue(
      "personal",
    );
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          requestPath(input).includes("/api/v1/workspace") && init?.method === "PUT",
      ),
    ).toBe(false);
  });

  it("debounces server search and supports Ctrl+K only on Windows", async () => {
    vi.spyOn(window.navigator, "platform", "get").mockReturnValue("Win32");
    const fetchMock = setupFetch();
    const user = userEvent.setup();
    renderApplication("/overview");

    const search = await screen.findByRole("searchbox", { name: "Поиск задач на доске" });
    expect(screen.getByText("Ctrl K")).toBeVisible();
    await user.type(search, "отчёт");
    expect(fetchMock.mock.calls.some(([input]) => requestPath(input).includes("q="))).toBe(
      false,
    );
    await waitFor(
      () =>
        expect(
          fetchMock.mock.calls.some(([input]) =>
            requestPath(input).includes("q=%D0%BE%D1%82%D1%87%D1%91%D1%82"),
          ),
        ).toBe(true),
      { timeout: 1_000 },
    );
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(search).toHaveFocus();
  });

  it("submits a column-scoped idempotent capture and keeps its id on retry", async () => {
    let attempts = 0;
    const captureBodies: Record<string, unknown>[] = [];
    const response = overviewResponse();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("/captures/text")) {
        const body = requestBody(init);
        captureBodies.push(body);
        attempts += 1;
        if (attempts === 1)
          return Promise.resolve(
            jsonResponse(
              { error: { code: "ai_failed", message: "AI временно недоступен" } },
              502,
            ),
          );
        return Promise.resolve(
          jsonResponse(
            {
              client_capture_id: body.client_capture_id,
              duplicate: false,
              disposition: "created",
              draft_id: crypto.randomUUID(),
              work_item_ids: [crypto.randomUUID()],
            },
            201,
          ),
        );
      }
      return Promise.resolve(jsonResponse(response));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderApplication("/overview");

    await screen.findByRole("heading", { name: "Сегодня" });
    await user.click(screen.getByRole("button", { name: "Добавить задачу в «Завтра»" }));
    const input = screen.getByRole("textbox", { name: "Текст новой задачи" });
    expect(input).toHaveFocus();
    await user.type(input, "Позвонить врачу");
    await user.click(screen.getByRole("button", { name: "Добавить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("AI временно недоступен");
    expect(input).toHaveValue("Позвонить врачу");
    await user.click(screen.getByRole("button", { name: "Добавить" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Задача добавлена");
    expect(captureBodies).toHaveLength(2);
    expect(captureBodies[0]).toMatchObject({
      text: "Позвонить врачу",
      workspace: "personal",
      target_bucket: "tomorrow",
    });
    expect(captureBodies[1]?.client_capture_id).toBe(captureBodies[0]?.client_capture_id);
  });

  it("moves by menu with keyboard support and preserves workspace", async () => {
    let actionBody: Record<string, unknown> = {};
    const fetchMock = setupFetch(overviewResponse(), (body) => {
      actionBody = body;
      return jsonResponse({ changed: true, work_item: { ...task, revision: 18 } });
    });
    const user = userEvent.setup();
    renderApplication("/overview");

    const row = (await screen.findByRole("heading", { name: task.title })).closest(
      "article",
    )!;
    const trigger = within(row).getByRole("button", { name: "Ещё действия" });
    trigger.focus();
    await user.keyboard("{ArrowDown}");
    const tomorrow = await within(row).findByRole("menuitem", { name: "Завтра" });
    expect(tomorrow).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(actionBody).toMatchObject({
      action: "move_bucket",
      target: "tomorrow",
      expected_revision: 17,
    });
    expect(within(row).getByText("Личное")).toBeVisible();
    expect(await screen.findByRole("status")).toHaveTextContent("Перемещено в «Завтра»");
    expect(fetchMock).toHaveBeenCalled();
  });

  it("rolls an optimistic move back after a stale revision", async () => {
    setupFetch(overviewResponse(), () =>
      jsonResponse({ error: { code: "conflict", message: "Work item changed" } }, 409),
    );
    const user = userEvent.setup();
    renderApplication("/overview");

    const row = (await screen.findByRole("heading", { name: inboxTask.title })).closest(
      "article",
    )!;
    await user.click(within(row).getByRole("button", { name: "Ещё действия" }));
    await user.click(within(row).getByRole("menuitem", { name: "Завтра" }));

    expect(
      await screen.findByText("Задача уже изменилась. Обзор обновлён — повторите перенос."),
    ).toBeVisible();
    const inbox = screen.getByRole("heading", { name: "Входящие" }).closest("section")!;
    expect(within(inbox).getByRole("heading", { name: inboxTask.title })).toBeVisible();
  });

  it("shows completed-today items in a collapsible section and reopens them", async () => {
    setupFetch(overviewResponse(), () =>
      jsonResponse({
        changed: true,
        work_item: { ...completedTask, status: "active", completed_at: null, revision: 25 },
      }),
    );
    const user = userEvent.setup();
    renderApplication("/overview");

    const today = (await screen.findByRole("heading", { name: "Сегодня" })).closest(
      "section",
    )!;
    expect(within(today).getByLabelText("2 записей")).toBeVisible();
    expect(
      within(today).queryByRole("heading", { name: completedTask.title }),
    ).not.toBeInTheDocument();
    await user.click(within(today).getByRole("button", { name: /Выполнено/ }));
    const completed = within(today).getByRole("heading", { name: completedTask.title });
    await user.click(
      within(completed.closest("article")!).getByRole("button", { name: "Вернуть задачу" }),
    );
    await waitFor(() =>
      expect(
        within(today).getByRole("heading", { name: completedTask.title }),
      ).toBeVisible(),
    );
  });
});
