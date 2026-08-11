import { cleanup, screen, waitFor, within } from "@testing-library/react";
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

describe("Planner and Timeline", () => {
  it("shows Planner data and sends a server-confirmed manual transition", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (
        path.includes("/work-items/") &&
        path.includes("/actions") &&
        _init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse({ changed: true, work_item: item }));
      }
      if (path.includes("/planner-queue")) {
        return Promise.resolve(
          page([{ item, planner_status: "needs_transfer", transferred_at: null }]),
        );
      }
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApplication("/planner-queue?status=needs_transfer&q=миграция");

    expect(await screen.findByText("Подготовить миграцию")).toBeVisible();
    const plannerCard = screen
      .getByRole("heading", { name: "Подготовить миграцию" })
      .closest("article");
    expect(plannerCard).not.toBeNull();
    expect(within(plannerCard!).queryByText("Нужно перенести")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(
        "status=needs_transfer&q=%D0%BC%D0%B8%D0%B3%D1%80%D0%B0%D1%86%D0%B8%D1%8F",
      ),
      expect.anything(),
    );
    await user.click(screen.getByRole("button", { name: /Перенесено/ }));
    await waitFor(() => {
      const action = fetchMock.mock.calls.find(([input]) =>
        requestPath(input).includes("/work-items/"),
      );
      expect(requestBody(action?.[1])).toMatchObject({
        action: "planner_transferred",
        expected_revision: 10,
      });
    });
  });

  it("applies timeline filters and exposes remaining screens in mobile overflow", async () => {
    const event = {
      id: "7a525364-5948-41f8-8976-4d0324115ea2",
      entity_kind: "work_item",
      entity_id: item.id,
      event_type: "created",
      occurred_at: "2026-07-22T10:00:00Z",
      title: "Migration sync",
      work_item_type: "task",
      status: "active",
      topics: [{ id: item.topic_id, name: "Migration" }],
      people: [],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      const options = optionsResponse(path);
      if (options) return Promise.resolve(options);
      if (path.includes("/timeline")) {
        return Promise.resolve(
          jsonResponse({
            items: [event],
            limit: 30,
            offset: 0,
            has_more: false,
            timezone: "Europe/Riga",
          }),
        );
      }
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApplication(
      `/timeline?from=2026-07-01&event_type=created&topic_id=${item.topic_id}`,
    );

    const meetingTitle = await screen.findByText("Migration sync");
    expect(meetingTitle).toBeVisible();
    expect(within(meetingTitle.closest("article")!).getByText("Создано")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("event_type=created"),
      expect.anything(),
    );
    const mobileNavigation = screen
      .getAllByRole("navigation", { name: "Основная навигация" })
      .find((navigation) => navigation.classList.contains("mobile-nav"));
    expect(mobileNavigation).toBeDefined();
    await user.click(within(mobileNavigation!).getByText("Ещё"));
    for (const label of ["Завтра", "Лента", "Темы", "Очередь Planner", "Настройки"]) {
      expect(within(mobileNavigation!).getByRole("link", { name: label })).toBeVisible();
    }
  });
});
