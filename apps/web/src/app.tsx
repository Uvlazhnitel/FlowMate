import { useQuery } from "@tanstack/react-query";
import { Navigate, Route, Routes, useLocation, useOutletContext } from "react-router-dom";

import { getCurrentUser, sessionQueryKey } from "./api/auth";
import { ApiError } from "./api/client";
import { AppShell } from "./components/AppShell";
import { ErrorState, LoadingState } from "./components/PageState";
import { LoginPage } from "./pages/LoginPage";
import { InboxPage } from "./pages/InboxPage";
import { PlannerQueuePage } from "./pages/PlannerQueuePage";
import { PlaceholderPage, pageDefinitions } from "./pages/PlaceholderPage";
import { SettingsPage } from "./pages/SettingsPage";
import { AgendaPage } from "./pages/AgendaPage";
import { ContextDetailPage } from "./pages/ContextDetailPage";
import { DashboardPage } from "./pages/DashboardPage";
import { PeoplePage } from "./pages/PeoplePage";
import { TodayPage } from "./pages/TodayPage";
import { TimelinePage } from "./pages/TimelinePage";
import { TopicsPage } from "./pages/TopicsPage";
import { MeetingsPage } from "./pages/MeetingsPage";
import { MeetingDetailPage } from "./pages/MeetingDetailPage";
import type { DateTimePreferences } from "./lib/dates";

function UserRoute({
  page,
}: {
  page:
    | "dashboard"
    | "today"
    | "topics"
    | "people"
    | "agenda"
    | "topic"
    | "person"
    | "inbox"
    | "planner"
    | "timeline"
    | "meetings"
    | "meeting";
}) {
  const user = useOutletContext<Awaited<ReturnType<typeof getCurrentUser>>>();
  const dateTimePreferences: DateTimePreferences = {
    timezone: user.timezone,
    dateDisplayFormat: user.date_display_format,
    timeDisplayFormat: user.time_display_format,
  };
  if (page === "dashboard")
    return <DashboardPage dateTimePreferences={dateTimePreferences} />;
  if (page === "today")
    return (
      <TodayPage
        dateTimePreferences={dateTimePreferences}
        defaultSnoozeMinutes={user.default_snooze_minutes}
      />
    );
  if (page === "topics") return <TopicsPage dateTimePreferences={dateTimePreferences} />;
  if (page === "people") return <PeoplePage dateTimePreferences={dateTimePreferences} />;
  if (page === "inbox") return <InboxPage dateTimePreferences={dateTimePreferences} />;
  if (page === "planner")
    return <PlannerQueuePage dateTimePreferences={dateTimePreferences} />;
  if (page === "timeline")
    return <TimelinePage dateTimePreferences={dateTimePreferences} />;
  if (page === "meetings")
    return <MeetingsPage dateTimePreferences={dateTimePreferences} />;
  if (page === "meeting")
    return <MeetingDetailPage dateTimePreferences={dateTimePreferences} />;
  if (page === "agenda")
    return (
      <AgendaPage
        dateTimePreferences={dateTimePreferences}
        defaultSnoozeMinutes={user.default_snooze_minutes}
      />
    );
  return (
    <ContextDetailPage
      kind={page === "topic" ? "topic" : "person"}
      dateTimePreferences={dateTimePreferences}
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
        <Route path="dashboard" element={<UserRoute page="dashboard" />} />
        <Route path="today" element={<UserRoute page="today" />} />
        <Route path="topics" element={<UserRoute page="topics" />} />
        <Route path="topics/:id" element={<UserRoute page="topic" />} />
        <Route path="people" element={<UserRoute page="people" />} />
        <Route path="people/:id" element={<UserRoute page="person" />} />
        <Route path="agenda" element={<UserRoute page="agenda" />} />
        <Route path="inbox" element={<UserRoute page="inbox" />} />
        <Route path="planner-queue" element={<UserRoute page="planner" />} />
        <Route path="timeline" element={<UserRoute page="timeline" />} />
        <Route path="meetings" element={<UserRoute page="meetings" />} />
        <Route path="meetings/:id" element={<UserRoute page="meeting" />} />
        {pageDefinitions
          .filter(
            (page) =>
              ![
                "/dashboard",
                "/today",
                "/topics",
                "/people",
                "/agenda",
                "/inbox",
                "/planner-queue",
                "/timeline",
                "/meetings",
              ].includes(page.path),
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
