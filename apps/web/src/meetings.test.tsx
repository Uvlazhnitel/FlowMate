import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { authenticatedUser, jsonResponse, renderApplication } from "./test/render";

const meeting = {
  id: "26abef88-4861-42dd-b250-ada521096a17",
  title: "Командная встреча",
  type: "team",
  status: "active",
  started_at: "2026-07-22T09:00:00Z",
  ended_at: null,
  summary: null,
  primary_topic_id: null,
  participants: [["a1", "Nina"]],
  topics: [["t1", "Launch"]],
  captured_note_count: 2,
  created_at: "2026-07-22T09:00:00Z",
  updated_at: "2026-07-22T09:00:00Z",
  revision: 10,
  long_running: false,
};

const capture = {
  id: "75df2dc2-081d-4dc8-aacd-c28dcb174b78",
  meeting_id: meeting.id,
  sequence: 1,
  status: "needs_clarification",
  review_status: "pending",
  revision: 20,
  source_type: "text",
  source_text: "Анна подготовит план запуска",
  context: {
    timezone: "Europe/Riga",
    captured_at: "2026-07-22T09:05:00Z",
    meeting_type: "team",
    participants: [{ id: "a1", name: "Nina" }],
    topics: [{ id: "t1", name: "Launch" }],
  },
  confidence: 0.72,
  suggested_question: "Когда должен быть готов план?",
  created_at: "2026-07-22T09:05:00Z",
  updated_at: "2026-07-22T09:05:00Z",
  items: [
    {
      id: "item-1",
      position: 1,
      type: "task",
      title: "Подготовить план",
      description: null,
      priority: "normal",
      confidence: 0.72,
      readiness: "clarification_required",
      missing_fields: ["date"],
      ambiguities: [],
      due_at: null,
      topic: null,
      people: [],
    },
  ],
};

function pathOf(input: RequestInfo | URL) {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

function jsonBody(init: RequestInit | undefined): Record<string, unknown> {
  if (typeof init?.body !== "string") throw new Error("Expected a JSON request body");
  return JSON.parse(init.body) as Record<string, unknown>;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Meetings", () => {
  it("shows an active meeting and ends it with a server-confirmed action", async () => {
    document.cookie = "flowmate_csrf=test-csrf; path=/";
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathOf(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.endsWith("/meetings/active"))
        return Promise.resolve(jsonResponse({ meeting }));
      if (path.includes("/captures"))
        return Promise.resolve(
          jsonResponse({ items: [], limit: 20, offset: 0, has_more: false }),
        );
      if (
        path.includes("/meetings?") ||
        path.includes("/people") ||
        path.includes("/topics")
      ) {
        return Promise.resolve(
          jsonResponse({ items: [], limit: 20, offset: 0, has_more: false }),
        );
      }
      if (path.includes("/actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({ meeting: { ...meeting, status: "completed" } }),
        );
      }
      return Promise.resolve(jsonResponse({}));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApplication("/meetings");
    expect(await screen.findByRole("heading", { name: "Командная встреча" })).toBeVisible();
    expect(screen.getByText("2 заметок")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Завершить" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/actions"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("renders the empty start form", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = pathOf(input);
        if (path.includes("/auth/me"))
          return Promise.resolve(jsonResponse(authenticatedUser));
        if (path.endsWith("/meetings/active"))
          return Promise.resolve(jsonResponse({ meeting: null }));
        return Promise.resolve(
          jsonResponse({ items: [], limit: 20, offset: 0, has_more: false }),
        );
      }),
    );
    renderApplication("/meetings");
    expect(await screen.findByRole("heading", { name: "Начать встречу" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Встреч пока нет" })).toBeVisible();
  });

  it("starts a meeting with an optional title", async () => {
    document.cookie = "flowmate_csrf=test-csrf; path=/";
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathOf(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.endsWith("/meetings/active"))
        return Promise.resolve(jsonResponse({ meeting: null }));
      if (path.endsWith("/meetings") && init?.method === "POST")
        return Promise.resolve(jsonResponse({ meeting }));
      return Promise.resolve(
        jsonResponse({ items: [], limit: 20, offset: 0, has_more: false }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApplication("/meetings");
    await user.type(await screen.findByLabelText("Название"), "Weekly sync");
    await user.click(screen.getByRole("button", { name: "Начать" }));

    await waitFor(() => {
      const request = fetchMock.mock.calls.find(
        ([input, init]) => pathOf(input).endsWith("/meetings") && init?.method === "POST",
      );
      expect(jsonBody(request?.[1])).toMatchObject({
        type: "team",
        title: "Weekly sync",
      });
    });
  });

  it("confirms cancellation before sending the action", async () => {
    document.cookie = "flowmate_csrf=test-csrf; path=/";
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathOf(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.endsWith("/meetings/active"))
        return Promise.resolve(jsonResponse({ meeting }));
      if (path.includes("/captures"))
        return Promise.resolve(
          jsonResponse({ items: [], limit: 20, offset: 0, has_more: false }),
        );
      if (path.includes("/actions") && init?.method === "POST")
        return Promise.resolve(
          jsonResponse({ meeting: { ...meeting, status: "cancelled" } }),
        );
      return Promise.resolve(
        jsonResponse({ items: [], limit: 20, offset: 0, has_more: false }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApplication("/meetings");
    await user.click(await screen.findByRole("button", { name: "Отменить" }));

    expect(confirm).toHaveBeenCalledWith("Отменить активную встречу?");
    await waitFor(() => {
      const request = fetchMock.mock.calls.find(([input]) =>
        pathOf(input).includes("/actions"),
      );
      expect(jsonBody(request?.[1])).toMatchObject({ action: "cancel" });
    });
  });

  it("shows, edits and removes an immutable meeting capture", async () => {
    document.cookie = "flowmate_csrf=test-csrf; path=/";
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathOf(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.endsWith("/meetings/active"))
        return Promise.resolve(
          jsonResponse({ meeting: { ...meeting, long_running: true } }),
        );
      if (path.includes("/captures") && init?.method === "PATCH")
        return Promise.resolve(jsonResponse({ capture }));
      if (path.includes("/captures") && init?.method === "POST")
        return Promise.resolve(
          jsonResponse({ capture: { ...capture, review_status: "removed" } }),
        );
      if (path.includes("/captures"))
        return Promise.resolve(
          jsonResponse({ items: [capture], limit: 20, offset: 0, has_more: false }),
        );
      return Promise.resolve(
        jsonResponse({ items: [], limit: 20, offset: 0, has_more: false }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApplication("/meetings");
    expect(await screen.findByText("Анна подготовит план запуска")).toBeVisible();
    expect(screen.getByText(/Отложенное уточнение/)).toBeVisible();
    expect(screen.getByText(/активна больше 12 часов/)).toBeVisible();
    expect(screen.queryByDisplayValue("Анна подготовит план запуска")).toBeNull();
    await user.clear(screen.getByLabelText("Заголовок"));
    await user.type(screen.getByLabelText("Заголовок"), "Обновлённый план");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => {
      const request = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
      expect(jsonBody(request?.[1])).toMatchObject({ title: "Обновлённый план" });
    });
    await user.click(screen.getByRole("button", { name: "Убрать" }));
    expect(confirm).toHaveBeenCalled();
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true),
    );
  });

  it("renders meeting review sections and server-confirmed actions", async () => {
    document.cookie = "flowmate_csrf=test-csrf; path=/";
    const review = {
      id: "review-1",
      meeting_id: meeting.id,
      status: "review_required",
      summary: "Согласовали план запуска.",
      suggested_next_actions: ["Подготовить план"],
      counts: { task: 1, decision: 1 },
      revision: 41,
      last_error_code: null,
      items: [
        {
          id: "review-item-1",
          position: 1,
          category: "task",
          status: "ready",
          title: "Подготовить план",
          source_capture_id: capture.id,
          source_draft_item_id: "item-1",
          suggested_next_action: "Nina готовит план",
          consequences: [],
          clarification_question: null,
          planner_requested: false,
          result_work_item_id: null,
          result_note_id: null,
        },
        {
          id: "review-item-2",
          position: 2,
          category: "decision",
          status: "clarification_required",
          title: "Выбрать вариант A",
          source_capture_id: capture.id,
          source_draft_item_id: "item-2",
          suggested_next_action: null,
          consequences: ["План строится по варианту A"],
          clarification_question: "Вариант A подтверждён?",
          planner_requested: false,
          result_work_item_id: null,
          result_note_id: null,
        },
      ],
      agenda: [],
      results: [],
      timeline: [],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = pathOf(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.endsWith(`/meetings/${meeting.id}`))
        return Promise.resolve(
          jsonResponse({
            meeting: {
              ...meeting,
              status: "review_required",
              ended_at: "2026-07-22T10:00:00Z",
            },
            review,
          }),
        );
      if (path.includes("/captures"))
        return Promise.resolve(
          jsonResponse({ items: [capture], limit: 20, offset: 0, has_more: false }),
        );
      if (path.endsWith("/review/agenda") && init?.method === "POST")
        return Promise.resolve(jsonResponse({ detail: "failed" }, 500));
      if (init?.method === "POST") return Promise.resolve(jsonResponse({ review }));
      return Promise.resolve(
        jsonResponse({ items: [], limit: 20, offset: 0, has_more: false }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApplication(`/meetings/${meeting.id}`);
    expect(await screen.findByText("Согласовали план запуска.")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Agenda" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Решения" })).toBeVisible();
    expect(screen.getByText("Вариант A подтверждён?")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Timeline" })).toBeVisible();
    expect(screen.getByText("История встречи пока пуста")).toBeVisible();
    await user.type(screen.getByPlaceholderText("Новый пункт повестки"), "Риски запуска");
    await user.click(screen.getByRole("button", { name: "Добавить" }));
    expect(
      await screen.findByText("Изменение не сохранено. Обновите данные и повторите."),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "В Planner Queue" }));
    await waitFor(() => {
      const request = fetchMock.mock.calls.find(
        ([input, init]) =>
          pathOf(input).includes("review/items") && init?.method === "POST",
      );
      expect(jsonBody(request?.[1])).toMatchObject({ action: "planner_on" });
    });
  });
});
