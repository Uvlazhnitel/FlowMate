import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkItemCardData } from "./api/operations";
import { authenticatedUser, jsonResponse, renderApplication } from "./test/render";

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

const focusItem: WorkItemCardData = {
  id: "0283942a-a7ec-45f4-81e2-4fd5f143cdd8",
  type: "task",
  status: "active",
  title: "Главная задача",
  description: null,
  priority: "urgent",
  planner_status: "not_required",
  topic_id: null,
  topic_name: null,
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

const laterItem: WorkItemCardData = {
  ...focusItem,
  id: "52802780-c750-4077-83a9-a951055bc6ca",
  title: "Задача на вечер",
  priority: "normal",
  overdue: false,
  due_at: "2026-07-21T17:00:00Z",
  effective_at: "2026-07-21T17:00:00Z",
};

function overview({
  focus = [focusItem],
  later = [laterItem],
  inbox = 4,
  hasMore = false,
}: {
  focus?: WorkItemCardData[];
  later?: WorkItemCardData[];
  inbox?: number;
  hasMore?: boolean;
} = {}) {
  return {
    timezone: "Europe/Riga",
    summary: {
      overdue: 2,
      due_today: 3,
      follow_ups: 1,
      waiting_overdue: 1,
      questions: 1,
      inbox,
      planner_queue: 7,
    },
    focus,
    later_today: { items: later, has_more: hasMore },
  };
}

function page(items: WorkItemCardData[] = []) {
  return {
    items,
    limit: 20,
    offset: 0,
    has_more: false,
    timezone: "Europe/Riga",
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Today home", () => {
  it("renders summary, focus, and later without loading paginated sections", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("/today/overview"))
        return Promise.resolve(
          jsonResponse(
            overview({
              later: [focusItem, laterItem],
              hasMore: true,
            }),
          ),
        );
      return Promise.resolve(jsonResponse(page()));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApplication("/today");

    expect(await screen.findByRole("heading", { name: "Главное сейчас" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Позже сегодня" })).toBeVisible();
    const summary = within(document.querySelector(".today-summary")!);
    expect(summary.getByRole("link", { name: /Входящие/ })).toBeVisible();
    expect(summary.getByRole("link", { name: /Просрочено/ })).toBeVisible();
    expect(summary.getByRole("link", { name: /На сегодня/ })).toBeVisible();
    expect(screen.getAllByText("Главная задача")).toHaveLength(1);
    expect(screen.getByText("Задача на вечер")).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Показать все задачи на сегодня" }),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Посмотреть задачи на завтра" }),
    ).toHaveAttribute("href", "/tomorrow");
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        requestPath(input).includes("/api/v1/today?section="),
      ),
    ).toHaveLength(0);
  });

  it("shows an Inbox action in the empty overview", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const path = requestPath(input);
        if (path.includes("/auth/me"))
          return Promise.resolve(jsonResponse(authenticatedUser));
        return Promise.resolve(jsonResponse(overview({ focus: [], later: [], inbox: 3 })));
      }),
    );

    renderApplication("/today");

    expect(
      await screen.findByRole("heading", { name: "На сегодня всё разобрано" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Разобрать входящие" })).toHaveAttribute(
      "href",
      "/inbox",
    );
  });

  it("loads only the selected section and keeps it in the URL state", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("/today/overview"))
        return Promise.resolve(jsonResponse(overview()));
      if (path.includes("section=waiting"))
        return Promise.resolve(jsonResponse(page([laterItem])));
      return Promise.resolve(jsonResponse(page()));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApplication("/today?section=waiting");

    expect(await screen.findByText("Задача на вечер")).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Раздел Сегодня" })).toHaveValue("waiting");
    const sectionCalls = fetchMock.mock.calls.filter(([input]) =>
      requestPath(input).includes("/api/v1/today?section="),
    );
    expect(sectionCalls).toHaveLength(1);
    expect(requestPath(sectionCalls[0]![0])).toContain("section=waiting");
    expect(
      screen.queryByRole("heading", { name: "Главное сейчас" }),
    ).not.toBeInTheDocument();
  });

  it("switches from summary to the overdue section", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("/today/overview"))
        return Promise.resolve(jsonResponse(overview()));
      if (path.includes("section=overdue"))
        return Promise.resolve(jsonResponse(page([focusItem])));
      return Promise.resolve(jsonResponse(page()));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApplication("/today");
    await user.click(await screen.findByRole("link", { name: /Просрочено/ }));

    expect(screen.getByRole("combobox", { name: "Раздел Сегодня" })).toHaveValue("overdue");
    expect(
      await screen.findByRole("heading", { name: "Просрочено", level: 2 }),
    ).toBeVisible();
  });

  it("canonicalizes an unknown section to overview", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      return Promise.resolve(jsonResponse(overview()));
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApplication("/today?section=unknown");

    expect(await screen.findByRole("heading", { name: "Главное сейчас" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Раздел Сегодня" })).toHaveValue(
      "overview",
    );
    expect(
      fetchMock.mock.calls.some(([input]) =>
        requestPath(input).includes("section=unknown"),
      ),
    ).toBe(false);
  });

  it("refreshes the overview after a workspace switch", async () => {
    let workspace = "personal";
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      if (path.includes("/api/v1/workspace")) {
        workspace = "work";
        return Promise.resolve(
          jsonResponse({ ...authenticatedUser, active_workspace: "work" }),
        );
      }
      if (path.includes("/today/overview")) {
        const item = {
          ...focusItem,
          id: workspace === "work" ? "9b3a99be-c748-46a5-9bd1-0631c4585fc2" : focusItem.id,
          title: workspace === "work" ? "Рабочий фокус" : "Личный фокус",
        };
        return Promise.resolve(jsonResponse(overview({ focus: [item], later: [] })));
      }
      return Promise.resolve(jsonResponse(page()));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApplication("/today");
    expect(await screen.findByText("Личный фокус")).toBeVisible();
    const desktopSwitcher = screen
      .getAllByLabelText("Рабочее пространство")
      .find((node) => node.closest(".sidebar"));
    expect(desktopSwitcher).toBeDefined();
    await user.click(within(desktopSwitcher!).getByRole("button", { name: "Работа" }));

    expect(await screen.findByText("Рабочий фокус")).toBeVisible();
    expect(screen.queryByText("Личный фокус")).not.toBeInTheDocument();
  });
});
