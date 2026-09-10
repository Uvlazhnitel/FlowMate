import * as Avatar from "@radix-ui/react-avatar";
import * as Tooltip from "@radix-ui/react-tooltip";
import {
  CalendarDays,
  CalendarRange,
  CircleHelp,
  Clock3,
  Inbox,
  ListChecks,
  LayoutDashboard,
  MoreHorizontal,
  Settings,
  Tags,
  Users,
} from "lucide-react";
import type { ComponentType } from "react";
import { NavLink, Outlet, useLocation, useMatch } from "react-router-dom";

import { type AuthenticatedUser } from "../api/auth";
import {
  normalizeWorkspaceScope,
  workspacePath,
  type WorkspaceScope,
} from "../lib/workspaceScope";

interface NavigationItem {
  to: string;
  label: string;
  icon: ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
}

const navigationItems: NavigationItem[] = [
  { to: "/overview", label: "Обзор", icon: LayoutDashboard },
  { to: "/today", label: "Сегодня", icon: CalendarDays },
  { to: "/tomorrow", label: "Завтра", icon: CalendarRange },
  { to: "/inbox", label: "Входящие", icon: Inbox },
  { to: "/agenda", label: "Повестка", icon: CircleHelp },
  { to: "/timeline", label: "Лента", icon: Clock3 },
  { to: "/topics", label: "Темы", icon: Tags },
  { to: "/people", label: "Люди", icon: Users },
  { to: "/planner-queue", label: "Очередь Planner", icon: ListChecks },
  { to: "/settings", label: "Настройки", icon: Settings },
];

const primaryMobilePaths = new Set(["/overview", "/today", "/inbox", "/agenda"]);
const workspaceScopePaths = new Set(["/overview", "/today", "/tomorrow", "/inbox"]);

function Navigation({
  mobile = false,
  workspaceScope,
}: {
  mobile?: boolean;
  workspaceScope?: WorkspaceScope;
}) {
  const visible = mobile
    ? navigationItems.filter((item) => primaryMobilePaths.has(item.to))
    : navigationItems;
  const overflow = navigationItems.filter((item) => !primaryMobilePaths.has(item.to));
  return (
    <nav className={mobile ? "mobile-nav" : "sidebar-nav"} aria-label="Основная навигация">
      {visible.map((item) => {
        return (
          <NavigationLink
            key={item.to}
            item={item}
            mobile={mobile}
            workspaceScope={workspaceScope}
          />
        );
      })}
      {mobile && (
        <details className="mobile-more">
          <summary>
            <MoreHorizontal size={19} aria-hidden />
            <span>Ещё</span>
          </summary>
          <div>
            {overflow.map((item) => (
              <NavigationLink
                key={item.to}
                item={item}
                mobile
                workspaceScope={workspaceScope}
              />
            ))}
          </div>
        </details>
      )}
    </nav>
  );
}

function NavigationLink({
  item,
  mobile,
  workspaceScope,
}: {
  item: NavigationItem;
  mobile: boolean;
  workspaceScope?: WorkspaceScope;
}) {
  const Icon = item.icon;
  const isActive = useMatch({ path: item.to, end: true }) !== null;
  const target =
    workspaceScope && workspaceScopePaths.has(item.to)
      ? workspacePath(item.to, workspaceScope)
      : item.to;
  return (
    <Tooltip.Root delayDuration={350}>
      <Tooltip.Trigger asChild>
        <NavLink to={target} className={`nav-link ${isActive ? "nav-link--active" : ""}`}>
          <Icon size={19} aria-hidden />
          <span>{item.label}</span>
        </NavLink>
      </Tooltip.Trigger>
      {!mobile && (
        <Tooltip.Content side="right" className="tooltip">
          {item.label}
        </Tooltip.Content>
      )}
    </Tooltip.Root>
  );
}

export function AppShell({ user }: { user: AuthenticatedUser }) {
  const name = user.display_name?.trim() || "Владелец";
  const initials = name.slice(0, 2).toUpperCase();
  const location = useLocation();
  const overviewMode = location.pathname === "/overview";
  const workspaceScope = workspaceScopePaths.has(location.pathname)
    ? normalizeWorkspaceScope(new URLSearchParams(location.search).get("workspace"))
    : undefined;
  return (
    <Tooltip.Provider>
      <div className={`app-shell ${overviewMode ? "app-shell--overview" : ""}`}>
        <aside className="sidebar">
          <div className="brand">
            <span className="brand__mark">F</span>
            <span>FlowMate</span>
          </div>
          <Navigation workspaceScope={workspaceScope} />
          <div className="profile-chip">
            <Avatar.Root className="avatar">
              <Avatar.Fallback>{initials}</Avatar.Fallback>
            </Avatar.Root>
            <div>
              <strong>{name}</strong>
              <span>{user.active_workspace === "personal" ? "Личное" : "Работа"}</span>
            </div>
          </div>
        </aside>
        <main className="main-content">
          <header className="mobile-header">
            <div className="brand">
              <span className="brand__mark">F</span>
              <span>FlowMate</span>
            </div>
            <Avatar.Root className="avatar">
              <Avatar.Fallback>{initials}</Avatar.Fallback>
            </Avatar.Root>
          </header>
          <Outlet context={user} />
        </main>
        <Navigation mobile workspaceScope={workspaceScope} />
      </div>
    </Tooltip.Provider>
  );
}
