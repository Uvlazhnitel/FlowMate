import { act, cleanup, screen, waitFor, within } from "@testing-library/react";
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
  planner_status: "not_required",
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

function overview(focus: object[] = [], later: object[] = []) {
  return jsonResponse({
    timezone: "Europe/Riga",
    summary: {
      overdue: focus.length,
      due_today: later.length,
      follow_ups: 0,
      waiting_overdue: 0,
      questions: 0,
      inbox: 0,
      planner_queue: 0,
    },
    focus,
    later_today: { items: later, has_more: false },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
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
      if (path.includes("/today/overview")) return Promise.resolve(overview());
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
    expect(await screen.findByRole("status")).toHaveTextContent("Выполнено");
    expect(screen.getByRole("button", { name: "Готово" })).toBeDisabled();
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Запись завершена"),
    );
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
      if (path.includes("/today/overview")) return Promise.resolve(overview());
      if (path.includes("section=overdue")) return Promise.resolve(page([workItem]));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderApplication("/today?section=overdue");

    const card = (await screen.findByText("Подготовить запуск")).closest(".work-card");
    const compactCard = within(card as HTMLElement);
    await user.click(compactCard.getByRole("button", { name: "Ещё действия" }));
    await user.click(compactCard.getByRole("menuitem", { name: "Отменить запись" }));

    expect(confirm).toHaveBeenCalledWith("Отменить запись? Она останется в истории.");
    expect(
      fetchMock.mock.calls.some(([input]) => requestPath(input).includes("/actions")),
    ).toBe(false);
  });

  it("adds an eligible work item to Planner explicitly", async () => {
    let plannerStatus: WorkItemCardData["planner_status"] = workItem.planner_status;
    let actionPayload: Record<string, unknown> = {};
    let resolveAction: (response: Response) => void = () => undefined;
    const actionResponse = new Promise<Response>((resolve) => {
      resolveAction = resolve;
    });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("/actions")) {
        actionPayload = JSON.parse(requestBody(init)) as Record<string, unknown>;
        return actionResponse;
      }
      if (path.includes("/today/overview")) return Promise.resolve(overview());
      if (path.includes("section=overdue")) {
        return Promise.resolve(page([{ ...workItem, planner_status: plannerStatus }]));
      }
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderApplication("/today?section=overdue");

    const card = (await screen.findByText("Подготовить запуск")).closest(".work-card");
    const compactCard = within(card as HTMLElement);
    const moreButton = compactCard.getByRole("button", { name: "Ещё действия" });
    await user.click(moreButton);
    const addButton = compactCard.getByRole("menuitem", {
      name: "Добавить в Planner",
    });
    await user.click(addButton);
    expect(moreButton).toHaveAttribute("aria-disabled", "true");
    expect(actionPayload).toMatchObject({
      action: "planner_needs_transfer",
      expected_revision: 1,
    });

    plannerStatus = "needs_transfer";
    resolveAction(
      jsonResponse({
        changed: true,
        work_item: { ...workItem, planner_status: plannerStatus, revision: 2 },
      }),
    );
    await waitFor(() => expect(moreButton).toHaveAttribute("aria-disabled", "false"));
    await user.click(moreButton);
    expect(
      compactCard.queryByRole("menuitem", { name: "Добавить в Planner" }),
    ).not.toBeInTheDocument();
  });

  it("keeps note and reminder payloads in the compact actions menu", async () => {
    const reminderItem: WorkItemCardData = {
      ...workItem,
      description:
        "Длинное описание остаётся полностью доступным и не обрезается в компактной карточке.",
      reminder: {
        id: "a61200a7-f193-4a48-9411-86f76462fd96",
        effective_at: "2026-07-21T08:30:00Z",
        revision: 4,
      },
    };
    const actionPayloads: Record<string, unknown>[] = [];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("/actions")) {
        actionPayloads.push(JSON.parse(requestBody(init)) as Record<string, unknown>);
        return Promise.resolve(
          jsonResponse({
            changed: true,
            work_item: { ...reminderItem, revision: 2 },
          }),
        );
      }
      if (path.includes("/today/overview")) return Promise.resolve(overview());
      if (path.includes("section=overdue")) return Promise.resolve(page([reminderItem]));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderApplication("/today?section=overdue");

    const card = (await screen.findByText("Подготовить запуск")).closest(".work-card");
    const compactCard = within(card as HTMLElement);
    await user.click(compactCard.getByRole("button", { name: "Ещё действия" }));
    await user.click(compactCard.getByRole("menuitem", { name: "Заметка" }));
    await user.type(screen.getByRole("textbox", { name: "Текст" }), "Контекст запуска");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() =>
      expect(actionPayloads[0]).toMatchObject({
        action: "add_note",
        content: "Контекст запуска",
        expected_revision: 1,
      }),
    );

    await user.click(compactCard.getByRole("button", { name: "Ещё действия" }));
    await user.click(compactCard.getByRole("menuitem", { name: "Отложить напоминание" }));
    await waitFor(() =>
      expect(actionPayloads[1]).toMatchObject({
        action: "snooze",
        duration_minutes: 60,
        reminder_id: reminderItem.reminder?.id,
        reminder_revision: 4,
        expected_revision: 1,
      }),
    );
    expect(compactCard.getByText(reminderItem.description as string)).toBeVisible();
  });

  it("refreshes operational data after the Undo window expires", async () => {
    let resolveAction: (response: Response) => void = () => undefined;
    const actionResponse = new Promise<Response>((resolve) => {
      resolveAction = resolve;
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("/actions")) return actionResponse;
      if (path.includes("/today/overview")) return Promise.resolve(overview());
      if (path.includes("section=overdue")) return Promise.resolve(page([workItem]));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApplication("/today?section=overdue");
    const completeButton = await screen.findByRole("button", { name: "Готово" });
    const user = userEvent.setup();
    await user.click(completeButton);
    vi.useFakeTimers();
    await act(async () => {
      resolveAction(
        jsonResponse({
          changed: true,
          work_item: {
            ...workItem,
            status: "done",
            revision: 2,
          },
        }),
      );
      await Promise.resolve();
    });
    expect(screen.getByRole("status")).toHaveTextContent("Выполнено");
    expect(screen.queryByText("Запись завершена")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(649);
    });
    expect(screen.queryByText("Запись завершена")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(screen.getByRole("status")).toHaveTextContent("Запись завершена");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(7_999);
    });
    expect(screen.getByRole("status")).toHaveTextContent("Запись завершена");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        requestPath(input).includes("/today/overview"),
      ),
    ).toHaveLength(2);
  });

  it("keeps non-complete terminal actions immediate", async () => {
    const waitingItem: WorkItemCardData = {
      ...workItem,
      id: "43f060bd-77e1-45e3-b07d-57a60e310768",
      type: "waiting",
      title: "Получить подтверждение",
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("/actions")) {
        const body = JSON.parse(requestBody(init)) as { action: string };
        expect(body.action).toBe("waiting_received");
        return Promise.resolve(
          jsonResponse({
            changed: true,
            work_item: { ...waitingItem, status: "done", revision: 2 },
          }),
        );
      }
      if (path.includes("/today/overview")) return Promise.resolve(overview());
      if (path.includes("section=overdue")) return Promise.resolve(page([waitingItem]));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderApplication("/today?section=overdue");

    await user.click(await screen.findByRole("button", { name: "Получено" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Запись завершена");
    expect(screen.queryByText("Выполнено")).not.toBeInTheDocument();
  });

  it("does not animate or repeat a failed complete action", async () => {
    let resolveAction: (response: Response) => void = () => undefined;
    const actionResponse = new Promise<Response>((resolve) => {
      resolveAction = resolve;
    });
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("/actions")) return actionResponse;
      if (path.includes("/today/overview")) return Promise.resolve(overview());
      if (path.includes("section=overdue")) return Promise.resolve(page([workItem]));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderApplication("/today?section=overdue");

    const completeButton = await screen.findByRole("button", { name: "Готово" });
    await user.click(completeButton);
    expect(completeButton).toBeDisabled();
    await user.click(completeButton);
    expect(
      fetchMock.mock.calls.filter(([input]) => requestPath(input).includes("/actions")),
    ).toHaveLength(1);

    resolveAction(jsonResponse({ detail: "stale revision" }, 409));

    expect(
      await screen.findByText(
        "Не удалось выполнить действие. Обновите данные и повторите.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("Выполнено")).not.toBeInTheDocument();
    expect(screen.queryByText("Запись завершена")).not.toBeInTheDocument();
  });

  it("skips the completion transition when reduced motion is preferred", async () => {
    vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
      matches: query === "(prefers-reduced-motion: reduce)",
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("/actions")) {
        return Promise.resolve(
          jsonResponse({
            changed: true,
            work_item: { ...workItem, status: "done", revision: 2 },
          }),
        );
      }
      if (path.includes("/today/overview")) return Promise.resolve(overview());
      if (path.includes("section=overdue")) return Promise.resolve(page([workItem]));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderApplication("/today?section=overdue");

    await user.click(await screen.findByRole("button", { name: "Готово" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Запись завершена");
    expect(screen.queryByText("Выполнено")).not.toBeInTheDocument();
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
    expect(screen.getByText("Подготовить запуск").closest(".work-card")).not.toHaveClass(
      "work-card--compact",
    );
    await user.click(screen.getByRole("button", { name: "Показать ещё" }));
    expect(await screen.findByText("Второй шаг")).toBeVisible();

    const mobileNavigation = screen
      .getAllByRole("navigation", { name: "Основная навигация" })
      .find((navigation) => navigation.classList.contains("mobile-nav"));
    expect(mobileNavigation).toBeDefined();
    const mobile = within(mobileNavigation!);
    for (const label of ["Обзор", "Сегодня", "Входящие", "Повестка"]) {
      expect(mobile.getByRole("link", { name: label })).toBeVisible();
    }
    expect(mobile.getByRole("link", { name: "Люди" })).not.toBeVisible();
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
