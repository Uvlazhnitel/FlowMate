import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { authenticatedUser, jsonResponse, renderApplication } from "./test/render";

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

function emptyOperationalResponse(path: string): Response {
  if (path.includes("/dashboard")) {
    return jsonResponse({
      timezone: "Europe/Riga",
      summary: {
        overdue: 0,
        due_today: 0,
        follow_ups: 0,
        waiting_overdue: 0,
        questions: 0,
        inbox: 0,
        planner_queue: 0,
      },
      recommended: [],
      activity: [],
      deadlines: [],
    });
  }
  return jsonResponse({
    items: [],
    limit: 20,
    offset: 0,
    has_more: false,
    timezone: "Europe/Riga",
  });
}

function stubEmptyApplication() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      return Promise.resolve(
        path.includes("/api/v1/auth/me")
          ? jsonResponse(authenticatedUser)
          : emptyOperationalResponse(path),
      );
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.cookie = "flowmate_csrf=; Max-Age=0; path=/";
});

describe("protected application", () => {
  it("shows a loading state while the session is being checked", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => undefined)));

    renderApplication("/dashboard");

    expect(await screen.findByRole("status")).toHaveTextContent("Проверяем сессию");
  });

  it("redirects an expired session to login", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse(
            { error: { code: "unauthorized", message: "Session expired" } },
            401,
          ),
        ),
    );

    renderApplication("/today");

    expect(await screen.findByRole("heading", { name: "Войти в FlowMate" })).toBeVisible();
  });

  it.each([
    ["/dashboard", "Обзор"],
    ["/today", "Сегодня"],
    ["/topics", "Темы"],
    ["/people", "Люди"],
    ["/agenda", "Повестка"],
    ["/inbox", "Входящие"],
    ["/planner-queue", "Очередь планирования"],
    ["/timeline", "Лента"],
    ["/settings", "Настройки"],
  ])("renders protected route %s", async (path, title) => {
    stubEmptyApplication();

    renderApplication(path);

    expect(await screen.findByRole("heading", { name: title, level: 1 })).toBeVisible();
    expect(screen.getAllByText("FlowMate")).toHaveLength(2);
  });

  it("uses an honest empty state on foundation pages", async () => {
    stubEmptyApplication();

    renderApplication("/dashboard");

    expect(
      await screen.findByRole("heading", { name: "Всё спокойно", level: 2 }),
    ).toBeVisible();
  });

  it("shows a retry state for an API outage", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    renderApplication("/dashboard");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось открыть FlowMate",
    );
    expect(screen.getByRole("button", { name: "Повторить" })).toBeVisible();
  });
});

describe("login and logout", () => {
  it("requests and verifies a Telegram login code", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ status: "code_sent", expires_in_seconds: 600 }, 202),
      )
      .mockResolvedValueOnce(jsonResponse(authenticatedUser))
      .mockImplementation((input: RequestInfo | URL) =>
        Promise.resolve(emptyOperationalResponse(requestPath(input))),
      );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderApplication("/login");

    await user.click(screen.getByRole("button", { name: /Получить код/ }));
    const input = await screen.findByLabelText("Код из сообщения");
    await user.type(input, "123456");
    await user.click(screen.getByRole("button", { name: "Продолжить" }));

    expect(await screen.findByRole("heading", { name: "Обзор", level: 1 })).toBeVisible();
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/auth/session",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
  });

  it("sends the CSRF cookie value when logging out", async () => {
    document.cookie = "flowmate_csrf=csrf-value; path=/";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(authenticatedUser))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderApplication("/settings");

    await user.click(
      await screen.findByRole("button", { name: /Выйти на этом устройстве/ }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const request = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(new Headers(request.headers).get("X-CSRF-Token")).toBe("csrf-value");
    expect(await screen.findByRole("heading", { name: "Войти в FlowMate" })).toBeVisible();
  });
});
