import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Bot, LogOut, Plus, Save, ShieldCheck, Smartphone, Volume2 } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { logout, sessionQueryKey } from "../api/auth";
import {
  createPerson,
  createTopic,
  getSettings,
  getSettingsPeople,
  getSettingsTopics,
  remainingKeys,
  updatePerson,
  updatePreferences,
  updateTopic,
  type PreferencesData,
  type SettingsPerson,
  type SettingsResponse,
  type SettingsTopic,
} from "../api/remaining";
import { InstallButton } from "../components/InstallButton";
import { LoadMoreButton, OperationalLayout } from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";

const dirtyForms = new Set<symbol>();
let warningSubscribers = 0;

function beforeUnload(event: BeforeUnloadEvent) {
  if (!dirtyForms.size) return;
  event.preventDefault();
}

function warnForLink(event: MouseEvent) {
  if (!dirtyForms.size) return;
  const target = event.target instanceof Element ? event.target.closest("a[href]") : null;
  if (target && !window.confirm("Есть несохранённые изменения. Покинуть страницу?")) {
    event.preventDefault();
    event.stopPropagation();
  }
}

function useUnsavedWarning(dirty: boolean) {
  const source = useRef(Symbol("settings-form"));
  useEffect(() => {
    const currentSource = source.current;
    if (dirty) dirtyForms.add(currentSource);
    else dirtyForms.delete(currentSource);
    return () => {
      dirtyForms.delete(currentSource);
    };
  }, [dirty]);
  useEffect(() => {
    const installListeners = warningSubscribers === 0;
    warningSubscribers += 1;
    if (installListeners) {
      window.addEventListener("beforeunload", beforeUnload);
      document.addEventListener("click", warnForLink, true);
    }
    return () => {
      warningSubscribers -= 1;
      if (warningSubscribers) return;
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", warnForLink, true);
    };
  }, []);
}

function TopicEditor({ topic }: { topic: SettingsTopic }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(topic.name);
  const [description, setDescription] = useState(topic.description ?? "");
  const [aliases, setAliases] = useState(topic.aliases.join(", "));
  const [active, setActive] = useState(topic.is_active);
  const dirty =
    name !== topic.name ||
    description !== (topic.description ?? "") ||
    aliases !== topic.aliases.join(", ") ||
    active !== topic.is_active;
  useUnsavedWarning(dirty);
  const mutation = useMutation({
    mutationFn: () =>
      updateTopic(topic.id, {
        name,
        description: description || null,
        aliases: aliases
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
        is_active: active,
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: remainingKeys.all }),
  });
  return (
    <form
      className="entity-editor"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate();
      }}
    >
      <input
        aria-label={`Название темы ${topic.name}`}
        required
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <input
        aria-label={`Описание темы ${topic.name}`}
        placeholder="Описание"
        value={description}
        onChange={(event) => setDescription(event.target.value)}
      />
      <input
        aria-label={`Алиасы темы ${topic.name}`}
        placeholder="Алиасы через запятую"
        value={aliases}
        onChange={(event) => setAliases(event.target.value)}
      />
      <label className="toggle-field">
        <input
          type="checkbox"
          checked={active}
          onChange={(event) => setActive(event.target.checked)}
        />{" "}
        Активна
      </label>
      <button className="text-action" disabled={mutation.isPending}>
        <Save size={15} /> Сохранить
      </button>
      {mutation.isError && <span className="inline-error">Ошибка сохранения</span>}
    </form>
  );
}

function PersonEditor({ person }: { person: SettingsPerson }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(person.display_name);
  const [role, setRole] = useState(person.role ?? "");
  const [notes, setNotes] = useState(person.notes ?? "");
  const [aliases, setAliases] = useState(person.aliases.join(", "));
  const [active, setActive] = useState(person.is_active);
  const dirty =
    name !== person.display_name ||
    role !== (person.role ?? "") ||
    notes !== (person.notes ?? "") ||
    aliases !== person.aliases.join(", ") ||
    active !== person.is_active;
  useUnsavedWarning(dirty);
  const mutation = useMutation({
    mutationFn: () =>
      updatePerson(person.id, {
        display_name: name,
        role: role || null,
        notes: notes || null,
        aliases: aliases
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
        is_active: active,
      }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: remainingKeys.all }),
  });
  return (
    <form
      className="entity-editor"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate();
      }}
    >
      <input
        aria-label={`Имя ${person.display_name}`}
        required
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <input
        aria-label={`Роль ${person.display_name}`}
        placeholder="Роль"
        value={role}
        onChange={(event) => setRole(event.target.value)}
      />
      <input
        aria-label={`Алиасы ${person.display_name}`}
        placeholder="Алиасы через запятую"
        value={aliases}
        onChange={(event) => setAliases(event.target.value)}
      />
      <textarea
        aria-label={`Заметки ${person.display_name}`}
        placeholder="Заметки"
        value={notes}
        onChange={(event) => setNotes(event.target.value)}
      />
      <label className="toggle-field">
        <input
          type="checkbox"
          checked={active}
          onChange={(event) => setActive(event.target.checked)}
        />{" "}
        Активен
      </label>
      <button className="text-action" disabled={mutation.isPending}>
        <Save size={15} /> Сохранить
      </button>
      {mutation.isError && <span className="inline-error">Ошибка сохранения</span>}
    </form>
  );
}

function SettingsContent({
  data,
  topics,
  people,
  topicsHasMore,
  peopleHasMore,
  loadingMoreTopics,
  loadingMorePeople,
  loadMoreTopics,
  loadMorePeople,
}: {
  data: SettingsResponse;
  topics: SettingsTopic[];
  people: SettingsPerson[];
  topicsHasMore: boolean;
  peopleHasMore: boolean;
  loadingMoreTopics: boolean;
  loadingMorePeople: boolean;
  loadMoreTopics: () => void;
  loadMorePeople: () => void;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [preferences, setPreferences] = useState<PreferencesData>(data.preferences);
  const [dirty, setDirty] = useState(false);
  useUnsavedWarning(dirty);
  const saveMutation = useMutation({
    mutationFn: () => updatePreferences(preferences),
    onSuccess: async (response) => {
      setPreferences(response.preferences);
      setDirty(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: remainingKeys.all }),
        queryClient.invalidateQueries({ queryKey: sessionQueryKey }),
      ]);
    },
  });
  const logoutMutation = useMutation({
    mutationFn: logout,
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: sessionQueryKey });
      void navigate("/login", { replace: true });
    },
  });
  function update<K extends keyof PreferencesData>(key: K, value: PreferencesData[K]) {
    setPreferences((current) => ({ ...current, [key]: value }));
    setDirty(true);
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    saveMutation.mutate();
  }
  async function addTopic() {
    const name = window.prompt("Название темы");
    if (!name) return;
    await createTopic({ name, description: null, aliases: [], is_active: true });
    await queryClient.invalidateQueries({ queryKey: remainingKeys.all });
  }
  async function addPerson() {
    const displayName = window.prompt("Имя человека");
    if (!displayName) return;
    await createPerson({
      display_name: displayName,
      role: null,
      notes: null,
      aliases: [],
      is_active: true,
    });
    await queryClient.invalidateQueries({ queryKey: remainingKeys.all });
  }
  return (
    <OperationalLayout
      eyebrow="Система"
      title="Настройки"
      description="Часовой пояс, уведомления и справочники остаются под вашим контролем."
    >
      <form className="settings-section settings-form" onSubmit={submit}>
        <div className="section-heading">
          <h2>Время и уведомления</h2>
          {dirty && <span className="unsaved-badge">Не сохранено</span>}
        </div>
        <div className="settings-fields">
          <label>
            Часовой пояс
            <input
              value={preferences.timezone}
              onChange={(event) => update("timezone", event.target.value)}
              placeholder="Europe/Riga"
            />
          </label>
          <label>
            Формат даты
            <select
              value={preferences.date_display_format}
              onChange={(event) =>
                update(
                  "date_display_format",
                  event.target.value as PreferencesData["date_display_format"],
                )
              }
            >
              <option value="day_month_year">DD.MM.YYYY</option>
              <option value="year_month_day">YYYY-MM-DD</option>
            </select>
          </label>
          <label>
            Формат времени
            <select
              value={preferences.time_display_format}
              onChange={(event) =>
                update(
                  "time_display_format",
                  event.target.value as PreferencesData["time_display_format"],
                )
              }
            >
              <option value="24h">24 часа</option>
              <option value="12h">12 часов</option>
            </select>
          </label>
          <label>
            Snooze по умолчанию, минут
            <input
              type="number"
              min="1"
              max="10080"
              value={preferences.default_snooze_minutes}
              onChange={(event) =>
                update("default_snooze_minutes", Number(event.target.value))
              }
            />
          </label>
          <label className="toggle-field">
            <input
              type="checkbox"
              checked={preferences.morning_digest_enabled}
              onChange={(event) => update("morning_digest_enabled", event.target.checked)}
            />{" "}
            Утренний digest
          </label>
          <label>
            Время утреннего digest
            <input
              type="time"
              value={preferences.morning_digest_time.slice(0, 5)}
              onChange={(event) => update("morning_digest_time", event.target.value)}
            />
          </label>
          <label className="toggle-field">
            <input
              type="checkbox"
              checked={preferences.evening_digest_enabled}
              onChange={(event) => update("evening_digest_enabled", event.target.checked)}
            />{" "}
            Вечерний digest
          </label>
          <label>
            Время вечернего digest
            <input
              type="time"
              value={preferences.evening_digest_time.slice(0, 5)}
              onChange={(event) => update("evening_digest_time", event.target.value)}
            />
          </label>
          <label className="toggle-field">
            <input
              type="checkbox"
              checked={preferences.quiet_hours_enabled}
              onChange={(event) => update("quiet_hours_enabled", event.target.checked)}
            />{" "}
            Тихие часы
          </label>
          <label>
            Начало
            <input
              type="time"
              value={preferences.quiet_hours_start.slice(0, 5)}
              onChange={(event) => update("quiet_hours_start", event.target.value)}
            />
          </label>
          <label>
            Окончание
            <input
              type="time"
              value={preferences.quiet_hours_end.slice(0, 5)}
              onChange={(event) => update("quiet_hours_end", event.target.value)}
            />
          </label>
          <label className="toggle-field">
            <input
              type="checkbox"
              checked={preferences.send_empty_digests}
              onChange={(event) => update("send_empty_digests", event.target.checked)}
            />{" "}
            Отправлять пустые digest
          </label>
        </div>
        {saveMutation.isError && (
          <p className="inline-error">
            Проверьте значения. Начало и окончание тихих часов должны отличаться.
          </p>
        )}
        <button
          className="button button--primary"
          disabled={saveMutation.isPending || !dirty}
        >
          <Save size={16} /> Сохранить настройки
        </button>
      </form>

      <section className="settings-section">
        <div className="section-heading">
          <h2>Провайдеры</h2>
        </div>
        <div className="provider-grid">
          <article>
            <Bot />
            <div>
              <strong>AI provider</strong>
              <span>{data.providers.ai_configured ? "Настроен" : "Не настроен"}</span>
            </div>
          </article>
          <article>
            <Volume2 />
            <div>
              <strong>Speech provider</strong>
              <span>{data.providers.speech_configured ? "Настроен" : "Не настроен"}</span>
            </div>
          </article>
        </div>
        <p className="muted-copy">Ключи и модели никогда не передаются в браузер.</p>
      </section>

      <section className="settings-section">
        <div className="section-heading">
          <h2>Темы</h2>
          <button className="text-action" onClick={() => void addTopic()}>
            <Plus size={15} /> Добавить
          </button>
        </div>
        {topics.length ? (
          <div className="entity-list">
            {topics.map((topic) => (
              <TopicEditor topic={topic} key={topic.id} />
            ))}
          </div>
        ) : (
          <EmptyState title="Тем пока нет" description="Создайте первую рабочую тему." />
        )}
        {topicsHasMore && (
          <LoadMoreButton loading={loadingMoreTopics} onClick={loadMoreTopics} />
        )}
      </section>

      <section className="settings-section">
        <div className="section-heading">
          <h2>Люди и алиасы</h2>
          <button className="text-action" onClick={() => void addPerson()}>
            <Plus size={15} /> Добавить
          </button>
        </div>
        {people.length ? (
          <div className="entity-list">
            {people.map((person) => (
              <PersonEditor person={person} key={person.id} />
            ))}
          </div>
        ) : (
          <EmptyState
            title="Людей пока нет"
            description="Добавьте человека и его рабочие алиасы."
          />
        )}
        {peopleHasMore && (
          <LoadMoreButton loading={loadingMorePeople} onClick={loadMorePeople} />
        )}
      </section>

      <div className="settings-grid">
        <article className="settings-card">
          <span className="settings-card__icon">
            <Smartphone />
          </span>
          <div>
            <h2>Приложение</h2>
            <p>Установите FlowMate как отдельное PWA.</p>
          </div>
          <InstallButton />
        </article>
        <article className="settings-card">
          <span className="settings-card__icon">
            <ShieldCheck />
          </span>
          <div>
            <h2>Безопасная сессия</h2>
            <p>Сессия хранится в HttpOnly cookie.</p>
          </div>
          <button
            className="button button--danger"
            type="button"
            disabled={logoutMutation.isPending}
            onClick={() => logoutMutation.mutate()}
          >
            <LogOut size={17} />{" "}
            {logoutMutation.isPending ? "Выходим…" : "Выйти на этом устройстве"}
          </button>
        </article>
      </div>
    </OperationalLayout>
  );
}

export function SettingsPage() {
  const settings = useQuery({
    queryKey: [...remainingKeys.all, "settings"],
    queryFn: getSettings,
  });
  const topics = useInfiniteQuery({
    queryKey: [...remainingKeys.all, "settings-topics"],
    queryFn: ({ pageParam }) => getSettingsTopics(pageParam),
    initialPageParam: 0,
    getNextPageParam: (page) => (page.has_more ? page.offset + page.limit : undefined),
  });
  const people = useInfiniteQuery({
    queryKey: [...remainingKeys.all, "settings-people"],
    queryFn: ({ pageParam }) => getSettingsPeople(pageParam),
    initialPageParam: 0,
    getNextPageParam: (page) => (page.has_more ? page.offset + page.limit : undefined),
  });
  if (settings.isPending || topics.isPending || people.isPending)
    return <LoadingState label="Загружаем настройки" />;
  if (settings.isError || topics.isError || people.isError)
    return (
      <ErrorState
        title="Не удалось загрузить настройки"
        onRetry={() => {
          void settings.refetch();
          void topics.refetch();
          void people.refetch();
        }}
      />
    );
  return (
    <SettingsContent
      data={settings.data}
      topics={topics.data.pages.flatMap((page) => page.items)}
      people={people.data.pages.flatMap((page) => page.items)}
      topicsHasMore={topics.hasNextPage}
      peopleHasMore={people.hasNextPage}
      loadingMoreTopics={topics.isFetchingNextPage}
      loadingMorePeople={people.isFetchingNextPage}
      loadMoreTopics={() => void topics.fetchNextPage()}
      loadMorePeople={() => void people.fetchNextPage()}
    />
  );
}
