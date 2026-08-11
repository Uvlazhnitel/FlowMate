import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkItemCardData } from "./api/operations";
import { WorkItemCard } from "./components/WorkItemCard";
import { jsonResponse } from "./test/render";

const item: WorkItemCardData = {
  id: "0283942a-a7ec-45f4-81e2-4fd5f143cdd8",
  type: "task",
  status: "active",
  title: "Подготовить запуск",
  description: null,
  priority: "high",
  planner_status: "not_required",
  topic_id: null,
  topic_name: null,
  people: [],
  due_at: "2026-07-31T09:00:00Z",
  next_follow_up_at: null,
  waiting_since: null,
  completed_at: null,
  updated_at: "2026-07-30T08:00:00Z",
  effective_at: "2026-07-31T09:00:00Z",
  overdue: false,
  revision: 17,
  reminder: null,
};

const preferences = {
  timezone: "UTC",
  dateDisplayFormat: "day_month_year" as const,
  timeDisplayFormat: "24h" as const,
};

function requestBody(init?: RequestInit): Record<string, unknown> {
  return JSON.parse(typeof init?.body === "string" ? init.body : "{}") as Record<
    string,
    unknown
  >;
}

function renderCard(agenda = false, cardItem: WorkItemCardData = item) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <WorkItemCard item={cardItem} dateTimePreferences={preferences} agenda={agenda} />
      </QueryClientProvider>,
    ),
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("reschedule dialog", () => {
  it("submits a preset immediately and reports the server date", async () => {
    const fetchMock = vi.fn<
      (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    >(() =>
      Promise.resolve(
        jsonResponse({
          changed: true,
          work_item: {
            ...item,
            due_at: "2026-08-07T15:00:00Z",
            effective_at: "2026-08-07T15:00:00Z",
            revision: 18,
          },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderCard();

    await user.click(screen.getByRole("button", { name: "Перенести" }));
    expect(screen.getByText("Сейчас:")).toHaveTextContent("31.07.2026 09:00");
    await user.click(screen.getByRole("button", { name: "Через неделю" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(requestBody(fetchMock.mock.calls[0]?.[1])).toMatchObject({
      action: "reschedule_preset",
      expected_revision: 17,
      preset: "next_week",
    });
    expect(await screen.findByRole("status")).toHaveTextContent(
      "✓ Перенесено на пятницу, 7 августа, 15:00",
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("submits natural text on Enter and preserves it after an error", async () => {
    const fetchMock = vi.fn<
      (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    >(() =>
      Promise.resolve(
        jsonResponse(
          {
            error: {
              code: "http_error",
              message:
                "Не удалось понять срок. Напишите, например: завтра утром, через час или 15 августа в 14:00.",
            },
          },
          422,
        ),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderCard();

    await user.click(screen.getByRole("button", { name: "Перенести" }));
    const phrase = screen.getByLabelText("Или напишите обычными словами");
    await user.type(phrase, "в пятницу после обеда{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось понять срок");
    expect(phrase).toHaveValue("в пятницу после обеда");
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(requestBody(fetchMock.mock.calls[0]?.[1])).toMatchObject({
      action: "reschedule_text",
      expected_revision: 17,
      phrase: "в пятницу после обеда",
    });
  });

  it("keeps exact date and time rescheduling compatible", async () => {
    const fetchMock = vi.fn<
      (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    >(() =>
      Promise.resolve(
        jsonResponse({
          changed: true,
          work_item: {
            ...item,
            effective_at: "2026-08-15T14:30:00Z",
            revision: 18,
          },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderCard();

    await user.click(screen.getByRole("button", { name: "Перенести" }));
    await user.click(screen.getByRole("button", { name: "Выбрать точную дату и время" }));
    const date = screen.getByLabelText("Дата");
    const time = screen.getByLabelText("Время");
    await user.clear(date);
    await user.type(date, "2026-08-15");
    await user.clear(time);
    await user.type(time, "14:30");
    await user.click(screen.getByRole("button", { name: "Перенести на выбранное время" }));

    await waitFor(() =>
      expect(requestBody(fetchMock.mock.calls[0]?.[1])).toMatchObject({
        action: "reschedule",
        expected_revision: 17,
        local_date: "2026-08-15",
        local_time: "14:30",
      }),
    );
  });

  it("disables closing while pending and closes with Escape afterwards", async () => {
    let resolveRequest: (response: Response) => void = () => undefined;
    const request = new Promise<Response>((resolve) => {
      resolveRequest = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(() => request),
    );
    const user = userEvent.setup();
    renderCard();

    await user.click(screen.getByRole("button", { name: "Перенести" }));
    await user.click(screen.getByRole("button", { name: "Позже сегодня" }));
    expect(screen.getByRole("button", { name: "Закрыть" })).toBeDisabled();
    expect(screen.getByLabelText("Или напишите обычными словами")).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeVisible();

    resolveRequest(
      jsonResponse({
        changed: true,
        work_item: {
          ...item,
          effective_at: "2026-07-31T12:15:00Z",
          revision: 18,
        },
      }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Перенести" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("refreshes stale data but leaves the dialog open", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(
            {
              error: {
                code: "http_error",
                message: "Work item changed",
              },
            },
            409,
          ),
        ),
      ),
    );
    const user = userEvent.setup();
    const { queryClient } = renderCard();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");

    await user.click(screen.getByRole("button", { name: "Перенести" }));
    await user.click(screen.getByRole("button", { name: "Завтра утром" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Задача уже изменилась. Данные обновлены",
    );
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["operations"] });
  });

  it("uses the same reschedule action from Agenda cards", async () => {
    const fetchMock = vi.fn<
      (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    >(() =>
      Promise.resolve(
        jsonResponse({
          changed: true,
          work_item: {
            ...item,
            effective_at: "2026-08-03T09:00:00Z",
            revision: 18,
          },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderCard(true);

    expect(screen.getByText(item.title).closest(".work-card")).not.toHaveClass(
      "work-card--compact",
    );
    expect(screen.getByRole("button", { name: "Результат" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Отложить" }));
    await user.click(screen.getByRole("button", { name: "Следующий рабочий день" }));

    await waitFor(() =>
      expect(requestBody(fetchMock.mock.calls[0]?.[1])).toMatchObject({
        action: "reschedule_preset",
        preset: "next_working_day",
      }),
    );
  });
});

describe("Planner card action", () => {
  it("hides manual Planner action for unsupported item types", () => {
    renderCard(false, { ...item, type: "question" });

    expect(
      screen.queryByRole("button", { name: "Добавить в Planner" }),
    ).not.toBeInTheDocument();
  });

  it("hides manual Planner action for an item already in the queue", () => {
    renderCard(false, { ...item, planner_status: "needs_transfer" });

    expect(
      screen.queryByRole("button", { name: "Добавить в Planner" }),
    ).not.toBeInTheDocument();
  });
});
