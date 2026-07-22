import { afterEach, expect, it, vi } from "vitest";

import { apiRequest } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "flowmate_csrf=; Max-Age=0; path=/";
});

it("uses credentials and disables browser caching", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ status: "ok" }), {
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await apiRequest<{ status: string }>("/api/example");

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/example",
    expect.objectContaining({ credentials: "include", cache: "no-store" }),
  );
});

it("adds CSRF only to mutating requests", async () => {
  document.cookie = "flowmate_csrf=token-from-cookie; path=/";
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);

  await apiRequest<void>("/api/example", { method: "DELETE" });

  const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
  expect(new Headers(request.headers).get("X-CSRF-Token")).toBe("token-from-cookie");
});
