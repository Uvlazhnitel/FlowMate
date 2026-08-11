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

describe("Inbox drafts", () => {
  it("keeps a low-confidence draft explicit and preserves inbox URL filters", async () => {
    document.cookie = "flowmate_csrf=test-csrf; path=/";
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const draft = {
      id: "b507cd6c-3620-427b-8145-39eb4dd2b639",
      kind: "draft",
      status: "needs_clarification",
      revision: 12,
      reasons: ["unresolved_draft", "low_confidence"],
      recoverable: true,
      source_excerpt: "Возможно, подготовить релиз",
      created_at: "2026-07-22T07:00:00Z",
      updated_at: "2026-07-22T08:00:00Z",
      expires_at: "2026-07-23T08:00:00Z",
      items: [
        {
          id: "3d221fd7-76d8-4a2f-a773-602af4910c09",
          position: 1,
          type: "task",
          title: "Подготовить релиз",
          description: null,
          priority: "normal",
          confidence: 0.42,
          readiness: "clarification_required",
          missing_fields: ["date"],
          ambiguities: [],
          due_at: null,
          topic: null,
          people: [],
        },
      ],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      const options = optionsResponse(path);
      if (options) return Promise.resolve(options);
      if (
        path.includes("/inbox/drafts/") &&
        path.includes("/actions") &&
        _init?.method === "POST"
      ) {
        return Promise.resolve(jsonResponse({ status: "confirmed", work_item_ids: [] }));
      }
      if (path.includes("/api/v1/inbox")) return Promise.resolve(page([draft]));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    renderApplication("/inbox?kind=draft&reason=low_confidence");

    expect(await screen.findByText("Подготовить релиз")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("kind=draft&reason=low_confidence"),
      expect.anything(),
    );
    await user.click(screen.getByRole("button", { name: "Подтвердить" }));
    expect(confirm).toHaveBeenCalled();
    await waitFor(() => {
      const action = fetchMock.mock.calls.find(
        ([input]) =>
          requestPath(input).includes("/inbox/drafts/") &&
          requestPath(input).includes("/actions"),
      );
      expect(requestBody(action?.[1])).toMatchObject({
        action: "confirm",
        expected_revision: 12,
        accept_uncertainty: true,
      });
    });
  });

  it("permanently deletes a draft only after confirmation and blocks repeat clicks", async () => {
    document.cookie = "flowmate_csrf=test-csrf; path=/";
    const draft = {
      id: "b507cd6c-3620-427b-8145-39eb4dd2b639",
      kind: "draft",
      status: "failed",
      revision: 31,
      reasons: ["unresolved_draft", "interrupted"],
      recoverable: false,
      source_excerpt: "Черновик для удаления",
      created_at: "2026-07-22T07:00:00Z",
      updated_at: "2026-07-22T08:00:00Z",
      expires_at: "2026-07-23T08:00:00Z",
      items: [],
    };
    let finishDelete: ((response: Response) => void) | undefined;
    const deleteResponse = new Promise<Response>((resolve) => {
      finishDelete = resolve;
    });
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path.includes("/auth/me"))
        return Promise.resolve(jsonResponse(authenticatedUser));
      const options = optionsResponse(path);
      if (options) return Promise.resolve(options);
      if (path.includes("/inbox/drafts/") && init?.method === "POST") {
        return deleteResponse;
      }
      if (path.includes("/api/v1/inbox")) return Promise.resolve(page([draft]));
      return Promise.resolve(page([]));
    });
    vi.stubGlobal("fetch", fetchMock);
    const confirm = vi
      .spyOn(window, "confirm")
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    const user = userEvent.setup();

    renderApplication("/inbox?kind=draft");

    const deleteButton = await screen.findByRole("button", { name: "Удалить" });
    await user.click(deleteButton);
    expect(
      fetchMock.mock.calls.some(([input]) => requestPath(input).includes("/actions")),
    ).toBe(false);
    await user.click(deleteButton);
    await waitFor(() => expect(deleteButton).toBeDisabled());
    const action = fetchMock.mock.calls.find(([input]) =>
      requestPath(input).includes(`/inbox/drafts/${draft.id}/actions`),
    );
    expect(requestBody(action?.[1])).toEqual({
      action: "delete",
      expected_revision: 31,
      accept_uncertainty: false,
    });
    expect(confirm).toHaveBeenLastCalledWith(
      "Удалить заметку и черновик без возможности восстановления?",
    );
    finishDelete?.(jsonResponse({ status: "deleted", id: draft.id }));
  });
});
