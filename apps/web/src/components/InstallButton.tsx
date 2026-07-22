import { Download, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { useRegisterSW } from "virtual:pwa-register/react";

interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

export function InstallButton() {
  const [installPrompt, setInstallPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const { needRefresh, updateServiceWorker } = useRegisterSW();

  useEffect(() => {
    const capture = (event: Event) => {
      event.preventDefault();
      setInstallPrompt(event as BeforeInstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", capture);
    return () => window.removeEventListener("beforeinstallprompt", capture);
  }, []);

  if (needRefresh[0]) {
    return (
      <button
        className="button button--secondary"
        type="button"
        onClick={() => void updateServiceWorker(true)}
      >
        <RefreshCw size={17} aria-hidden="true" />
        Обновить приложение
      </button>
    );
  }
  if (!installPrompt) {
    return <p className="settings-hint">Установка появится, когда браузер разрешит её.</p>;
  }
  return (
    <button
      className="button button--secondary"
      type="button"
      onClick={() => {
        void installPrompt.prompt();
        void installPrompt.userChoice.finally(() => setInstallPrompt(null));
      }}
    >
      <Download size={17} aria-hidden="true" />
      Установить FlowMate
    </button>
  );
}
