import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError } from "./api/client";

export function createAppQueryClient(
  onSessionExpired: (client: QueryClient) => void,
): QueryClient {
  const handleError = (error: Error) => {
    if (error instanceof ApiError && error.status === 401) {
      onSessionExpired(client);
    }
  };
  const client = new QueryClient({
    queryCache: new QueryCache({ onError: handleError }),
    mutationCache: new MutationCache({ onError: handleError }),
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: 60_000,
      },
      mutations: { retry: false },
    },
  });
  return client;
}
