import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, KeyRound, MessageCircle, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { requestLoginCode, sessionQueryKey, verifyLoginCode } from "../api/auth";
import { ApiError } from "../api/client";

interface LocationState {
  from?: string;
}

export function LoginPage() {
  const [codeRequested, setCodeRequested] = useState(false);
  const [code, setCode] = useState("");
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const cachedUser = queryClient.getQueryData(sessionQueryKey);
  const destination = (location.state as LocationState | null)?.from ?? "/dashboard";

  const requestCode = useMutation({
    mutationFn: requestLoginCode,
    onSuccess: () => setCodeRequested(true),
  });
  const verifyCode = useMutation({
    mutationFn: verifyLoginCode,
    onSuccess: (user) => {
      queryClient.setQueryData(sessionQueryKey, user);
      void navigate(destination, { replace: true });
    },
  });

  if (cachedUser) {
    return <Navigate to="/dashboard" replace />;
  }

  const error = requestCode.error ?? verifyCode.error;
  return (
    <main className="login-page">
      <section className="login-story">
        <div className="brand brand--light">
          <span className="brand__mark brand__mark--light">F</span>
          <span>FlowMate</span>
        </div>
        <div className="login-story__content">
          <span className="eyebrow eyebrow--light">Ваш рабочий ритм</span>
          <h1>
            Ничего не потерять.
            <br />
            Никого не забыть.
          </h1>
          <p>Заметки, follow-up и решения из Telegram — в одном спокойном пространстве.</p>
        </div>
        <div className="login-proof">
          <ShieldCheck size={18} /> Код приходит только в ваш Telegram
        </div>
      </section>

      <section className="login-panel" aria-labelledby="login-title">
        <div className="login-card">
          <span className="login-card__icon">
            {codeRequested ? <KeyRound /> : <MessageCircle />}
          </span>
          <span className="eyebrow">Без паролей</span>
          <h2 id="login-title">{codeRequested ? "Введите код" : "Войти в FlowMate"}</h2>
          <p>
            {codeRequested
              ? "Мы отправили шестизначный код в Telegram. Он действует 10 минут."
              : "Запросите одноразовый код — никаких паролей и секретов в браузере."}
          </p>

          {error && (
            <div className="form-error" role="alert">
              {error instanceof ApiError ? error.message : "Не удалось выполнить запрос."}
            </div>
          )}

          {!codeRequested ? (
            <button
              className="button button--primary button--wide"
              type="button"
              disabled={requestCode.isPending}
              onClick={() => requestCode.mutate()}
            >
              {requestCode.isPending ? "Отправляем…" : "Получить код в Telegram"}
              <ArrowRight size={18} />
            </button>
          ) : (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                verifyCode.mutate(code);
              }}
            >
              <label className="code-field">
                <span>Код из сообщения</span>
                <input
                  autoComplete="one-time-code"
                  autoFocus
                  inputMode="numeric"
                  maxLength={6}
                  pattern="[0-9]{6}"
                  placeholder="000000"
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
                />
              </label>
              <button
                className="button button--primary button--wide"
                type="submit"
                disabled={code.length !== 6 || verifyCode.isPending}
              >
                {verifyCode.isPending ? "Проверяем…" : "Продолжить"}
                <ArrowRight size={18} />
              </button>
              <button
                className="text-button"
                type="button"
                disabled={requestCode.isPending}
                onClick={() => requestCode.mutate()}
              >
                Отправить новый код
              </button>
            </form>
          )}
        </div>
      </section>
    </main>
  );
}
