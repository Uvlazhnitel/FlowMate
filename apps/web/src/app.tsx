import { useQuery } from "@tanstack/react-query";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { getCurrentUser, sessionQueryKey } from "./api/auth";
import { ApiError } from "./api/client";
import { AppShell } from "./components/AppShell";
import { ErrorState, LoadingState } from "./components/PageState";
import { LoginPage } from "./pages/LoginPage";
import { PlaceholderPage, pageDefinitions } from "./pages/PlaceholderPage";
import { SettingsPage } from "./pages/SettingsPage";

function ProtectedApplication() {
  const location = useLocation();
  const session = useQuery({
    queryKey: sessionQueryKey,
    queryFn: getCurrentUser,
  });

  if (session.isPending) {
    return <LoadingState label="Проверяем сессию" fullPage />;
  }
  if (session.error instanceof ApiError && session.error.status === 401) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (session.isError) {
    return (
      <ErrorState
        title="Не удалось открыть FlowMate"
        description="Проверьте соединение и повторите попытку."
        onRetry={() => void session.refetch()}
        fullPage
      />
    );
  }
  return <AppShell user={session.data} />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedApplication />}>
        <Route index element={<Navigate to="/dashboard" replace />} />
        {pageDefinitions.map((page) => (
          <Route
            key={page.path}
            path={page.path.slice(1)}
            element={<PlaceholderPage page={page} />}
          />
        ))}
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
