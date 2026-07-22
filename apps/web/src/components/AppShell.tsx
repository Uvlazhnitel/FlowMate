import * as Avatar from "@radix-ui/react-avatar";
import * as Tooltip from "@radix-ui/react-tooltip";
import {
  CalendarDays,
  CircleHelp,
  Clock3,
  Inbox,
  LayoutDashboard,
  ListChecks,
  Settings,
  Tags,
  Users,
} from "lucide-react";
import type { ComponentType } from "react";
import { NavLink, Outlet, useMatch } from "react-router-dom";

import type { AuthenticatedUser } from "../api/auth";

interface NavigationItem {
  to: string;
  label: string;
  icon: ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
}

const navigationItems: NavigationItem[] = [
  { to: "/dashboard", label: "Обзор", icon: LayoutDashboard },
  { to: "/today", label: "Сегодня", icon: CalendarDays },
  { to: "/inbox", label: "Входящие", icon: Inbox },
  { to: "/planner-queue", label: "Планирование", icon: ListChecks },
  { to: "/agenda", label: "Повестка", icon: CircleHelp },
  { to: "/timeline", label: "Лента", icon: Clock3 },
  { to: "/topics", label: "Темы", icon: Tags },
  { to: "/people", label: "Люди", icon: Users },
  { to: "/settings", label: "Настройки", icon: Settings },
];

function Navigation({ mobile = false }: { mobile?: boolean }) {
  return (
    <nav className={mobile ? "mobile-nav" : "sidebar-nav"} aria-label="Основная навигация">
      {navigationItems.map((item) => {
        return <NavigationLink key={item.to} item={item} mobile={mobile} />;
      })}
    </nav>
  );
}

function NavigationLink({ item, mobile }: { item: NavigationItem; mobile: boolean }) {
  const Icon = item.icon;
  const isActive = useMatch({ path: item.to, end: true }) !== null;
  return (
    <Tooltip.Root delayDuration={350}>
      <Tooltip.Trigger asChild>
        <NavLink to={item.to} className={`nav-link ${isActive ? "nav-link--active" : ""}`}>
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
  return (
    <Tooltip.Provider>
      <div className="app-shell">
        <aside className="sidebar">
          <div className="brand">
            <span className="brand__mark">F</span>
            <span>FlowMate</span>
          </div>
          <Navigation />
          <div className="profile-chip">
            <Avatar.Root className="avatar">
              <Avatar.Fallback>{initials}</Avatar.Fallback>
            </Avatar.Root>
            <div>
              <strong>{name}</strong>
              <span>Личное пространство</span>
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
          <Outlet />
        </main>
        <Navigation mobile />
      </div>
    </Tooltip.Provider>
  );
}
