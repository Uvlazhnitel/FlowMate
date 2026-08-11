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

describe("Inbox notes and bulk actions", () => {
  it("sends atomic bulk note deletion and displays a safe conflict", async () => {
    document.cookie = "flowmate_csrf=test-csrf; path=/";
    const notes = [
      {
        id: "3195ebcf-15f4-42ef-bf5f-947589cd06bd",
        kind: "note",
        reasons: ["unstructured_note"],
        excerpt: "Первая заметка",
        source: "manual",
        created_at: "2026-07-22T07:00:00Z",
      },
      {
        id: "43a92626-84b9-44fb-92ea-5d1961d3d7bf",
        kind: "note",
        reasons: ["unstructured_note"],
        excerpt: "Вторая заметка",
        source: "manual",
        created_at: "2026-07-22T08:00:00Z",
      },
    ];
    const safeMessage = "Заметку нельзя удалить: она уже связана с другой записью";
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      const options = optionsResponse(path);
      if (options) return Promise.resolve(options);
      if (path.includes("/inbox/bulk-actions") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({ error: { code: "conflict", message: safeMessage } }, 409),
        );
      }
      if (path.includes("/api/v1/inbox")) return Promise.resolve(page(notes));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();

    renderApplication("/inbox?kind=note");

    await screen.findByText("Первая заметка");
    for (const checkbox of screen.getAllByRole("checkbox")) await user.click(checkbox);
    await user.click(screen.getByRole("button", { name: /Удалить выбранные/ }));
    await waitFor(() => expect(screen.getByText(safeMessage)).toBeVisible());
    const action = fetchMock.mock.calls.find(([input]) =>
      requestPath(input).includes("/inbox/bulk-actions"),
    );
    expect(requestBody(action?.[1])).toEqual({
      action: "delete",
      entries: notes.map((note) => ({ kind: "note", id: note.id })),
    });
    expect(confirm).toHaveBeenCalledWith(
      "Безвозвратно удалить выбранные заметки? Восстановление невозможно.",
    );
  });
});
