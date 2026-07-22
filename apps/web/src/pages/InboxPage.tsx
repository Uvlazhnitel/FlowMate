import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, CheckCheck, FilePenLine, Plus, Save, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  operationsKeys,
  runWorkItemAction,
  type WorkItemCardData,
} from "../api/operations";
import {
  createPerson,
  createTopic,
  getInbox,
  getSettingsPeople,
  getSettingsTopics,
  remainingKeys,
  runBulkInboxAction,
  runDraftAction,
  runNoteAction,
  updateDraftItem,
  type DraftInboxEntry,
  type DraftItemData,
  type InboxEntry,
  type SettingsPerson,
  type SettingsTopic,
} from "../api/remaining";
import { OperationalLayout } from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { WorkItemCard } from "../components/WorkItemCard";
import { formatDateTime, type DateTimePreferences } from "../lib/dates";

const reasonLabels: Record<string, string> = {
  unresolved_draft: "Нужно уточнить",
  low_confidence: "Низкая уверенность",
  incomplete: "Не хватает данных",
  interrupted: "Диалог прерван",
  inbox_status: "Новая запись",
  missing_date: "Нет даты",
  missing_topic: "Нет темы",
  missing_person: "Нет человека",
  unstructured_note: "Неразобранная заметка",
  meeting_review: "После встречи",
};

const itemTypes = [
  "task",
  "follow_up",
  "waiting",
  "question",
  "note",
  "decision",
  "agenda_item",
];
const priorities = ["low", "normal", "high", "urgent"];

function localParts(value: string | null, timezone: string) {
  if (!value) return { date: "", time: "09:00" };
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(value));
  const read = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return {
    date: `${read("year")}-${read("month")}-${read("day")}`,
    time: `${read("hour")}:${read("minute")}`,
  };
}

function DraftItemEditor({
  draft,
  item,
  timezone,
  topics,
  people,
  onSaved,
}: {
  draft: DraftInboxEntry;
  item: DraftItemData;
  timezone: string;
  topics: SettingsTopic[];
  people: SettingsPerson[];
  onSaved: () => void;
}) {
  const initialDate = localParts(item.due_at, timezone);
  const [title, setTitle] = useState(item.title);
  const [description, setDescription] = useState(item.description ?? "");
  const [type, setType] = useState(item.type);
  const [priority, setPriority] = useState(item.priority);
  const [topicId, setTopicId] = useState(item.topic?.id ?? "");
  const [personIds, setPersonIds] = useState(item.people.map((person) => person.id));
  const [localDate, setLocalDate] = useState(initialDate.date);
  const [localTime, setLocalTime] = useState(initialDate.time);
  const mutation = useMutation({
    mutationFn: () =>
      updateDraftItem(draft.id, item.id, {
        expected_revision: draft.revision,
        item_type: type,
        title,
        description: description || null,
        priority,
        topic_id: topicId || null,
        person_ids: personIds,
        local_date: localDate || null,
        local_time: localDate ? localTime : null,
      }),
    onSuccess: onSaved,
  });
  return (
    <form
      className="editor-grid"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate();
      }}
    >
      <label>
        Заголовок
        <input value={title} required onChange={(event) => setTitle(event.target.value)} />
      </label>
      <label>
        Тип
        <select value={type} onChange={(event) => setType(event.target.value)}>
          {itemTypes.map((value) => (
            <option value={value} key={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
      <label>
        Приоритет
        <select value={priority} onChange={(event) => setPriority(event.target.value)}>
          {priorities.map((value) => (
            <option value={value} key={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
      <label>
        Тема
        <select value={topicId} onChange={(event) => setTopicId(event.target.value)}>
          <option value="">Без темы</option>
          {topics
            .filter((topic) => topic.is_active)
            .map((topic) => (
              <option value={topic.id} key={topic.id}>
                {topic.name}
              </option>
            ))}
        </select>
      </label>
      <label>
        Люди
        <select
          multiple
          value={personIds}
          onChange={(event) =>
            setPersonIds([...event.target.selectedOptions].map((option) => option.value))
          }
        >
          {people
            .filter((person) => person.is_active)
            .map((person) => (
              <option value={person.id} key={person.id}>
                {person.display_name}
              </option>
            ))}
        </select>
      </label>
      <label>
        Дата
        <input
          type="date"
          value={localDate}
          onChange={(event) => setLocalDate(event.target.value)}
        />
      </label>
      <label>
        Время
        <input
          type="time"
          value={localTime}
          onChange={(event) => setLocalTime(event.target.value)}
        />
      </label>
      <label className="editor-grid__wide">
        Описание
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      {mutation.isError && <p className="inline-error">Не удалось сохранить изменения.</p>}
      <button className="button button--primary" disabled={mutation.isPending}>
        <Save size={16} aria-hidden /> Сохранить item
      </button>
    </form>
  );
}

function WorkItemEditor({
  item,
  timezone,
  topics,
  people,
  onSaved,
}: {
  item: WorkItemCardData;
  timezone: string;
  topics: SettingsTopic[];
  people: SettingsPerson[];
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(item.title);
  const [description, setDescription] = useState(item.description ?? "");
  const [type, setType] = useState(item.type);
  const [priority, setPriority] = useState(item.priority);
  const [topicId, setTopicId] = useState(item.topic_id ?? "");
  const [personIds, setPersonIds] = useState(item.people.map((person) => person[0]));
  const initialDate = localParts(item.effective_at, timezone);
  const [localDate, setLocalDate] = useState(initialDate.date);
  const [localTime, setLocalTime] = useState(initialDate.time);
  const mutation = useMutation({
    mutationFn: () =>
      runWorkItemAction(item.id, {
        action: "edit",
        client_action_id: crypto.randomUUID(),
        expected_revision: item.revision,
        title,
        description: description || null,
        item_type: type,
        priority,
        topic_id: topicId || null,
        person_ids: personIds,
        date_changed: true,
        local_date: localDate || undefined,
        local_time: localDate ? localTime : undefined,
      }),
    onSuccess: onSaved,
  });
  return (
    <form
      className="editor-grid"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate();
      }}
    >
      <label>
        Заголовок
        <input value={title} onChange={(event) => setTitle(event.target.value)} />
      </label>
      <label>
        Тип
        <select value={type} onChange={(event) => setType(event.target.value)}>
          {itemTypes
            .filter((value) => value !== "note")
            .map((value) => (
              <option value={value} key={value}>
                {value}
              </option>
            ))}
        </select>
      </label>
      <label>
        Приоритет
        <select value={priority} onChange={(event) => setPriority(event.target.value)}>
          {priorities.map((value) => (
            <option value={value} key={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
      <label>
        Тема
        <select value={topicId} onChange={(event) => setTopicId(event.target.value)}>
          <option value="">Без темы</option>
          {topics
            .filter((topic) => topic.is_active)
            .map((topic) => (
              <option value={topic.id} key={topic.id}>
                {topic.name}
              </option>
            ))}
        </select>
      </label>
      <label>
        Люди
        <select
          multiple
          value={personIds}
          onChange={(event) =>
            setPersonIds([...event.target.selectedOptions].map((option) => option.value))
          }
        >
          {people
            .filter((person) => person.is_active)
            .map((person) => (
              <option value={person.id} key={person.id}>
                {person.display_name}
              </option>
            ))}
        </select>
      </label>
      <label>
        Дата
        <input
          type="date"
          value={localDate}
          onChange={(event) => setLocalDate(event.target.value)}
        />
      </label>
      <label>
        Время
        <input
          type="time"
          value={localTime}
          disabled={!localDate}
          onChange={(event) => setLocalTime(event.target.value)}
        />
      </label>
      <label className="editor-grid__wide">
        Описание
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      {mutation.isError && <p className="inline-error">Не удалось сохранить запись.</p>}
      <button className="button button--primary" disabled={mutation.isPending}>
        Сохранить
      </button>
    </form>
  );
}

export function InboxPage({
  dateTimePreferences,
}: {
  dateTimePreferences: DateTimePreferences;
}) {
  const timezone = dateTimePreferences.timezone;
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") ?? "";
  const reason = params.get("reason") ?? "";
  const page = Number(params.get("page") ?? 0);
  const [selected, setSelected] = useState<Record<string, InboxEntry>>({});
  const query = useQuery({
    queryKey: [...remainingKeys.all, "inbox", kind, reason, page],
    queryFn: () => getInbox(kind, reason, page * 20),
  });
  const options = useQuery({
    queryKey: [...remainingKeys.all, "inbox-options"],
    queryFn: async () => {
      const [topics, people] = await Promise.all([
        getSettingsTopics(),
        getSettingsPeople(),
      ]);
      return { topics: topics.items, people: people.items };
    },
  });
  const refresh = async () => {
    setSelected({});
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: remainingKeys.all }),
      queryClient.invalidateQueries({ queryKey: operationsKeys.all }),
    ]);
  };
  const draftMutation = useMutation({
    mutationFn: ({ draft, action }: { draft: DraftInboxEntry; action: string }) => {
      const uncertain = draft.items.some(
        (item) => item.readiness !== "ready" || item.confidence < 0.8,
      );
      if (
        action === "confirm" &&
        uncertain &&
        !window.confirm("Черновик содержит неопределённость. Подтвердить явно?")
      ) {
        return Promise.reject(new Error("cancelled"));
      }
      if (
        action === "cancel" &&
        !window.confirm("Отменить черновик? Исходная заметка останется в архиве.")
      ) {
        return Promise.reject(new Error("cancelled"));
      }
      return runDraftAction(
        draft.id,
        action,
        draft.revision,
        action === "confirm" && uncertain,
      );
    },
    onSuccess: () => void refresh(),
  });
  const noteMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "keep" | "archive" }) =>
      runNoteAction(id, action),
    onSuccess: () => void refresh(),
  });
  const bulkMutation = useMutation({
    mutationFn: async (action: string) => {
      const values = Object.values(selected);
      return runBulkInboxAction(
        action,
        values.map((entry) =>
          entry.kind === "work_item"
            ? {
                kind: entry.kind,
                id: entry.item.id,
                expected_revision: entry.item.revision,
                client_action_id: crypto.randomUUID(),
              }
            : entry.kind === "draft"
              ? {
                  kind: entry.kind,
                  id: entry.id,
                  expected_revision: entry.revision,
                }
              : { kind: entry.kind, id: entry.id },
        ),
      );
    },
    onSuccess: () => void refresh(),
  });
  const selectedKinds = new Set(Object.values(selected).map((entry) => entry.kind));
  const commonKind = selectedKinds.size === 1 ? [...selectedKinds][0] : null;
  async function addTopic() {
    const name = window.prompt("Название новой темы");
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

  if (query.isPending) return <LoadingState label="Собираем входящие" />;
  if (query.isError)
    return (
      <ErrorState title="Не удалось загрузить Inbox" onRetry={() => void query.refetch()} />
    );
  const topics = options.data?.topics ?? [];
  const people = options.data?.people ?? [];
  return (
    <OperationalLayout
      eyebrow="Разобрать"
      title="Inbox"
      description="Неопределённые записи остаются здесь, пока вы явно не решите, что с ними делать."
      controls={
        <div className="filter-row">
          <select
            aria-label="Тип Inbox"
            value={kind}
            onChange={(event) =>
              setParams(event.target.value ? { kind: event.target.value } : {})
            }
          >
            <option value="">Все типы</option>
            <option value="draft">AI drafts</option>
            <option value="work_item">Записи</option>
            <option value="note">Заметки</option>
            <option value="meeting_review">Итоги встреч</option>
          </select>
          <select
            aria-label="Причина"
            value={reason}
            onChange={(event) =>
              setParams(
                event.target.value
                  ? { ...(kind ? { kind } : {}), reason: event.target.value }
                  : kind
                    ? { kind }
                    : {},
              )
            }
          >
            <option value="">Все причины</option>
            {Object.entries(reasonLabels).map(([value, label]) => (
              <option value={value} key={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
      }
    >
      {Object.keys(selected).length > 0 && (
        <div className="bulk-bar" role="region" aria-label="Групповые действия">
          <strong>Выбрано: {Object.keys(selected).length}</strong>
          {commonKind === "draft" && (
            <button
              className="button button--danger"
              onClick={() => {
                if (window.confirm("Отменить выбранные черновики без удаления заметок?"))
                  bulkMutation.mutate("cancel");
              }}
            >
              Отменить выбранные
            </button>
          )}
          {commonKind === "note" && (
            <>
              <button
                className="button button--secondary"
                onClick={() => bulkMutation.mutate("keep")}
              >
                Оставить заметками
              </button>
              <button
                className="button button--danger"
                onClick={() => {
                  if (window.confirm("Архивировать выбранные заметки?"))
                    bulkMutation.mutate("archive");
                }}
              >
                В архив
              </button>
            </>
          )}
          {commonKind === "work_item" && (
            <button
              className="button button--danger"
              onClick={() => {
                if (window.confirm("Архивировать выбранные записи?"))
                  bulkMutation.mutate("archive");
              }}
            >
              Архивировать
            </button>
          )}
          {!commonKind && <span className="muted-copy">Выберите записи одного типа.</span>}
        </div>
      )}
      {!query.data.items.length ? (
        <EmptyState
          title="Inbox разобран"
          description="Неопределённых записей и неструктурированных заметок нет."
        />
      ) : (
        <div className="inbox-list">
          {query.data.items.map((entry) => {
            const id = entry.kind === "work_item" ? entry.item.id : entry.id;
            return (
              <article
                className={`inbox-card inbox-card--${entry.kind}`}
                key={`${entry.kind}-${id}`}
              >
                {entry.kind !== "meeting_review" && (
                  <label className="select-control">
                    <input
                      type="checkbox"
                      checked={Boolean(selected[id])}
                      onChange={(event) =>
                        setSelected((current) => {
                          const next = { ...current };
                          if (event.target.checked) next[id] = entry;
                          else delete next[id];
                          return next;
                        })
                      }
                    />
                    <span className="sr-only">Выбрать запись</span>
                  </label>
                )}
                <div className="reason-row">
                  {entry.reasons.map((value) => (
                    <span className="reason-chip" key={value}>
                      {reasonLabels[value] ?? value}
                    </span>
                  ))}
                </div>
                {entry.kind === "draft" && (
                  <>
                    <div className="inbox-card__heading">
                      <div>
                        <span className="directory-kicker">AI draft · {entry.status}</span>
                        <h2>{entry.items[0]?.title ?? "Черновик"}</h2>
                      </div>
                      <span>
                        {Math.round(
                          Math.min(...entry.items.map((item) => item.confidence), 1) * 100,
                        )}
                        %
                      </span>
                    </div>
                    <p>{entry.source_excerpt}</p>
                    {entry.items.map((item) => (
                      <details className="edit-panel" key={item.id}>
                        <summary>
                          <FilePenLine size={16} aria-hidden /> {item.position}.{" "}
                          {item.title}
                        </summary>
                        <DraftItemEditor
                          draft={entry}
                          item={item}
                          timezone={timezone}
                          topics={topics}
                          people={people}
                          onSaved={() => void refresh()}
                        />
                      </details>
                    ))}
                    <div className="work-card__actions">
                      <button
                        className="card-action card-action--primary"
                        onClick={() =>
                          draftMutation.mutate({ draft: entry, action: "confirm" })
                        }
                      >
                        <CheckCheck size={15} /> Подтвердить
                      </button>
                      {entry.recoverable &&
                        ["expired", "failed"].includes(entry.status) && (
                          <button
                            className="card-action"
                            onClick={() =>
                              draftMutation.mutate({ draft: entry, action: "recover" })
                            }
                          >
                            Восстановить
                          </button>
                        )}
                      <button
                        className="card-action"
                        onClick={() =>
                          draftMutation.mutate({ draft: entry, action: "save_as_note" })
                        }
                      >
                        <Save size={15} /> Оставить заметкой
                      </button>
                      <button
                        className="card-action card-action--danger"
                        onClick={() =>
                          draftMutation.mutate({ draft: entry, action: "cancel" })
                        }
                      >
                        <XCircle size={15} /> Отменить
                      </button>
                    </div>
                  </>
                )}
                {entry.kind === "note" && (
                  <>
                    <span className="directory-kicker">
                      {entry.source} ·
                      {formatDateTime(entry.created_at, dateTimePreferences)}
                    </span>
                    <h2>Неразобранная заметка</h2>
                    <p>{entry.excerpt}</p>
                    <div className="work-card__actions">
                      <button
                        className="card-action card-action--primary"
                        onClick={() =>
                          noteMutation.mutate({ id: entry.id, action: "keep" })
                        }
                      >
                        <Save size={15} /> Оставить
                      </button>
                      <button
                        className="card-action card-action--danger"
                        onClick={() => {
                          if (window.confirm("Архивировать заметку? Текст сохранится."))
                            noteMutation.mutate({ id: entry.id, action: "archive" });
                        }}
                      >
                        <Archive size={15} /> В архив
                      </button>
                    </div>
                  </>
                )}
                {entry.kind === "work_item" && (
                  <>
                    <WorkItemCard
                      item={entry.item}
                      dateTimePreferences={dateTimePreferences}
                    />
                    <details className="edit-panel">
                      <summary>
                        <FilePenLine size={16} /> Заполнить поля
                      </summary>
                      <WorkItemEditor
                        item={entry.item}
                        timezone={timezone}
                        topics={topics}
                        people={people}
                        onSaved={() => void refresh()}
                      />
                    </details>
                  </>
                )}
                {entry.kind === "meeting_review" && (
                  <>
                    <span className="directory-kicker">
                      {entry.category} ·{" "}
                      {formatDateTime(entry.created_at, dateTimePreferences)}
                    </span>
                    <h2>{entry.title}</h2>
                    <p>Встреча: {entry.meeting_title}</p>
                    <Link
                      className="button button--primary"
                      to={`/meetings/${entry.meeting_id}`}
                    >
                      Открыть итог встречи
                    </Link>
                  </>
                )}
              </article>
            );
          })}
        </div>
      )}
      <div className="pager">
        <button
          className="button button--secondary"
          disabled={page === 0}
          onClick={() =>
            setParams({
              ...(kind ? { kind } : {}),
              ...(reason ? { reason } : {}),
              page: String(page - 1),
            })
          }
        >
          Назад
        </button>
        <button
          className="button button--secondary"
          disabled={!query.data.has_more}
          onClick={() =>
            setParams({
              ...(kind ? { kind } : {}),
              ...(reason ? { reason } : {}),
              page: String(page + 1),
            })
          }
        >
          Дальше
        </button>
      </div>
      <div className="quick-create">
        <button className="text-action" onClick={() => void addTopic()}>
          <Plus size={15} /> Новая тема
        </button>
        <button className="text-action" onClick={() => void addPerson()}>
          <Plus size={15} /> Новый человек
        </button>
      </div>
      {(draftMutation.isError || bulkMutation.isError) && (
        <p className="inline-error">Действие не выполнено. Обновите данные и повторите.</p>
      )}
    </OperationalLayout>
  );
}
