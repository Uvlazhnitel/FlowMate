import { useQuery } from "@tanstack/react-query";
import { Navigate, Route, Routes, useLocation, useOutletContext } from "react-router-dom";

import { getCurrentUser, sessionQueryKey } from "./api/auth";
import { ApiError } from "./api/client";
import { AppShell } from "./components/AppShell";
import { ErrorState, LoadingState } from "./components/PageState";
import { LoginPage } from "./pages/LoginPage";
import { PlaceholderPage, pageDefinitions } from "./pages/PlaceholderPage";
import { SettingsPage } from "./pages/SettingsPage";
import { AgendaPage } from "./pages/AgendaPage";
import { ContextDetailPage } from "./pages/ContextDetailPage";
import { DashboardPage } from "./pages/DashboardPage";
import { PeoplePage } from "./pages/PeoplePage";
import { TodayPage } from "./pages/TodayPage";
import { TopicsPage } from "./pages/TopicsPage";

function UserRoute({
  page,
}: {
  page: "today" | "topics" | "people" | "agenda" | "topic" | "person";
}) {
  const user = useOutletContext<Awaited<ReturnType<typeof getCurrentUser>>>();
  if (page === "today")
    return (
      <TodayPage
        timezone={user.timezone}
        defaultSnoozeMinutes={user.default_snooze_minutes}
      />
    );
  if (page === "topics") return <TopicsPage timezone={user.timezone} />;
  if (page === "people") return <PeoplePage timezone={user.timezone} />;
  if (page === "agenda")
    return (
      <AgendaPage
        timezone={user.timezone}
        defaultSnoozeMinutes={user.default_snooze_minutes}
      />
    );
  return (
    <ContextDetailPage
      kind={page === "topic" ? "topic" : "person"}
      timezone={user.timezone}
    />
  );
}

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
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="today" element={<UserRoute page="today" />} />
        <Route path="topics" element={<UserRoute page="topics" />} />
        <Route path="topics/:id" element={<UserRoute page="topic" />} />
        <Route path="people" element={<UserRoute page="people" />} />
        <Route path="people/:id" element={<UserRoute page="person" />} />
        <Route path="agenda" element={<UserRoute page="agenda" />} />
        {pageDefinitions
          .filter(
            (page) =>
              !["/dashboard", "/today", "/topics", "/people", "/agenda"].includes(
                page.path,
              ),
          )
          .map((page) => (
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
