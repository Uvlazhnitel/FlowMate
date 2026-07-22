import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LogOut, ShieldCheck, Smartphone } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { logout, sessionQueryKey } from "../api/auth";
import { InstallButton } from "../components/InstallButton";

export function SettingsPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const logoutMutation = useMutation({
    mutationFn: logout,
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: sessionQueryKey });
      void navigate("/login", { replace: true });
    },
  });

  return (
    <section className="page" aria-labelledby="page-title">
      <header className="page-heading">
        <span className="eyebrow">Система</span>
        <h1 id="page-title">Настройки</h1>
        <p>Установка приложения и управление текущей сессией.</p>
      </header>
      <div className="settings-grid">
        <article className="settings-card">
          <span className="settings-card__icon">
            <Smartphone />
          </span>
          <div>
            <h2>Приложение</h2>
            <p>Установите FlowMate, чтобы открывать его как отдельное приложение.</p>
          </div>
          <InstallButton />
        </article>
        <article className="settings-card">
          <span className="settings-card__icon">
            <ShieldCheck />
          </span>
          <div>
            <h2>Безопасная сессия</h2>
            <p>Сессия хранится в защищённой HttpOnly cookie и действует 30 дней.</p>
          </div>
          <button
            className="button button--danger"
            type="button"
            disabled={logoutMutation.isPending}
            onClick={() => logoutMutation.mutate()}
          >
            <LogOut size={17} />
            {logoutMutation.isPending ? "Выходим…" : "Выйти на этом устройстве"}
          </button>
        </article>
      </div>
    </section>
  );
}
