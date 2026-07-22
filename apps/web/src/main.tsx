import "@fontsource-variable/fraunces";
import "@fontsource-variable/manrope";
import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { AppRoutes } from "./app";
import { createAppQueryClient } from "./query-client";
import "./styles.css";

const queryClient = createAppQueryClient((client) => {
  if (window.location.pathname !== "/login") {
    client.clear();
    window.location.assign("/login");
  }
});

const root = document.getElementById("root");
if (!root) {
  throw new Error("Application root is missing");
}

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
