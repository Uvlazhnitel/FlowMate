import type { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "./api/client";
import { createAppQueryClient } from "./query-client";

describe("createAppQueryClient", () => {
  it("clears cached data and expires the frontend session on 401", async () => {
    const onSessionExpired = vi.fn((expiredClient: QueryClient) => expiredClient.clear());
    const client = createAppQueryClient(onSessionExpired);
    client.setQueryData(["private-data"], { secret: false });

    await expect(
      client.fetchQuery({
        queryKey: ["expired-request"],
        queryFn: () => Promise.reject(new ApiError(401, "unauthorized", "Expired")),
      }),
    ).rejects.toMatchObject({ status: 401 });

    expect(client.getQueryData(["private-data"])).toBeUndefined();
    expect(onSessionExpired).toHaveBeenCalledOnce();
  });
});
