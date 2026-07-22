import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  CalendarClock,
  MessageSquareText,
  Play,
  Save,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import {
  getActiveMeeting,
  getMeetingCaptures,
  getRecentMeetings,
  meetingsKeys,
  removeMeetingCapture,
  runMeetingAction,
  startMeeting,
  updateMeetingCaptureItem,
  type MeetingCardData,
  type MeetingCaptureData,
  type MeetingType,
} from "../api/meetings";
import { getPeople, getTopics, operationsKeys } from "../api/operations";
import { OperationalLayout } from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { formatDateTime, type DateTimePreferences } from "../lib/dates";
import type { DraftItemData } from "../api/remaining";

const typeLabels: Record<MeetingType, string> = {
  lead: "С руководителем",
  team: "Командная",
  client_sync: "Клиентский sync",
  steering: "Steering",
  one_to_one: "Один на один",
  other: "Другая",
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

function CaptureItemEditor({
  meetingId,
  capture,
  item,
  people,
  topics,
  onSaved,
}: {
  meetingId: string;
  capture: MeetingCaptureData;
  item: DraftItemData;
  people: { id: string; display_name: string }[];
  topics: { id: string; name: string }[];
  onSaved: () => void;
}) {
  const initialDate = localParts(item.due_at, capture.context.timezone);
  const [title, setTitle] = useState(item.title);
  const [description, setDescription] = useState(item.description ?? "");
  const [itemType, setItemType] = useState(item.type);
  const [priority, setPriority] = useState(item.priority);
  const [topicId, setTopicId] = useState(item.topic?.id ?? "");
  const [personIds, setPersonIds] = useState(item.people.map((person) => person.id));
  const [localDate, setLocalDate] = useState(initialDate.date);
  const [localTime, setLocalTime] = useState(initialDate.time);
  const mutation = useMutation({
    mutationFn: () =>
      updateMeetingCaptureItem(meetingId, capture.id, item.id, {
        expected_revision: capture.revision,
        item_type: itemType,
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
      className="editor-grid meeting-capture__editor"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate();
      }}
    >
      <label>
        Заголовок
        <input required value={title} onChange={(event) => setTitle(event.target.value)} />
      </label>
      <label>
        Тип
        <select value={itemType} onChange={(event) => setItemType(event.target.value)}>
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
          {topics.map((topic) => (
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
          {people.map((person) => (
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
      {mutation.isError && <p className="inline-error">Не удалось сохранить пункт.</p>}
      <button className="button button--primary" disabled={mutation.isPending}>
        <Save size={15} aria-hidden /> Сохранить
      </button>
    </form>
  );
}

export function CaptureCard({
  capture,
  meetingId,
  people,
  topics,
  onChanged,
}: {
  capture: MeetingCaptureData;
  meetingId: string;
  people: { id: string; display_name: string }[];
  topics: { id: string; name: string }[];
  onChanged: () => void;
}) {
  const remove = useMutation({
    mutationFn: () => removeMeetingCapture(capture),
    onSuccess: onChanged,
  });
  return (
    <article className="meeting-capture">
      <header>
        <div>
          <span className="directory-kicker">
            Пункт №{capture.sequence} ·{" "}
            {capture.source_type === "voice" ? "голос" : "текст"}
          </span>
          <p className="meeting-capture__source">{capture.source_text}</p>
        </div>
        <div className="meeting-capture__status">
          <span className="status-badge">{capture.status}</span>
          {capture.confidence !== null && (
            <span>{Math.round(capture.confidence * 100)}%</span>
          )}
        </div>
      </header>
      {capture.suggested_question && (
        <p className="meeting-capture__question">
          Отложенное уточнение: {capture.suggested_question}
        </p>
      )}
      {capture.items.length ? (
        capture.items.map((item) => (
          <CaptureItemEditor
            key={item.id}
            meetingId={meetingId}
            capture={capture}
            item={item}
            people={people}
            topics={topics}
            onSaved={onChanged}
          />
        ))
      ) : (
        <p className="muted-copy">Структурирование ещё не завершено.</p>
      )}
      {remove.isError && <p className="inline-error">Не удалось убрать пункт.</p>}
      <button
        className="button button--secondary"
        disabled={remove.isPending}
        onClick={() => {
          if (window.confirm(`Убрать пункт №${capture.sequence} из обработки?`))
            remove.mutate();
        }}
      >
        <Trash2 size={15} aria-hidden /> Убрать
      </button>
    </article>
  );
}

function MeetingCard({
  meeting,
  preferences,
  active = false,
  onEnd,
  onCancel,
}: {
  meeting: MeetingCardData;
  preferences: DateTimePreferences;
  active?: boolean;
  onEnd?: () => void;
  onCancel?: () => void;
}) {
  return (
    <article className={`meeting-card ${active ? "meeting-card--active" : ""}`}>
      <header>
        <div>
          <span className="directory-kicker">{typeLabels[meeting.type]}</span>
          <h2>{meeting.title}</h2>
        </div>
        <span className={`status-badge status-badge--${meeting.status}`}>
          {meeting.status}
        </span>
      </header>
      <p>
        <CalendarClock size={15} aria-hidden />{" "}
        {formatDateTime(meeting.started_at ?? meeting.created_at, preferences)}
        {meeting.ended_at ? ` — ${formatDateTime(meeting.ended_at, preferences)}` : ""}
      </p>
      <div className="meeting-links">
        {meeting.participants.map(([id, name]) => (
          <span key={id}>{name}</span>
        ))}
        {meeting.topics.map(([id, name]) => (
          <span key={id}>#{name}</span>
        ))}
      </div>
      <p>
        <MessageSquareText size={15} aria-hidden /> {meeting.captured_note_count} заметок
      </p>
      {meeting.summary && <p>{meeting.summary}</p>}
      {active && (
        <footer className="meeting-actions">
          <button className="button button--primary" onClick={onEnd}>
            <Square size={15} /> Завершить
          </button>
          <button className="button button--secondary" onClick={onCancel}>
            <X size={15} /> Отменить
          </button>
        </footer>
      )}
      {!active && meeting.status !== "planned" && (
        <Link
          className="button button--secondary meeting-card__link"
          to={`/meetings/${meeting.id}`}
        >
          Открыть итог
        </Link>
      )}
    </article>
  );
}

export function MeetingsPage({
  dateTimePreferences,
}: {
  dateTimePreferences: DateTimePreferences;
}) {
  const queryClient = useQueryClient();
  const [type, setType] = useState<MeetingType>("team");
  const [title, setTitle] = useState("");
  const [personSearch, setPersonSearch] = useState("");
  const [topicSearch, setTopicSearch] = useState("");
  const [participantIds, setParticipantIds] = useState<string[]>([]);
  const [topicIds, setTopicIds] = useState<string[]>([]);
  const [primaryTopicId, setPrimaryTopicId] = useState("");
  const dirty = Boolean(
    title || participantIds.length || topicIds.length || type !== "team",
  );
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty) event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const active = useQuery({ queryKey: meetingsKeys.active, queryFn: getActiveMeeting });
  const recent = useInfiniteQuery({
    queryKey: meetingsKeys.recent,
    queryFn: ({ pageParam }) => getRecentMeetings(pageParam),
    initialPageParam: 0,
    getNextPageParam: (page) => (page.has_more ? page.offset + page.limit : undefined),
  });
  const activeMeetingId = active.data?.meeting?.id ?? "";
  const captures = useInfiniteQuery({
    queryKey: meetingsKeys.captures(activeMeetingId),
    queryFn: ({ pageParam }) => getMeetingCaptures(activeMeetingId, pageParam),
    initialPageParam: 0,
    enabled: Boolean(activeMeetingId),
    getNextPageParam: (page) => (page.has_more ? page.offset + page.limit : undefined),
  });
  const people = useQuery({
    queryKey: [...operationsKeys.all, "meeting-people", personSearch],
    queryFn: () => getPeople(personSearch, 0),
  });
  const topics = useQuery({
    queryKey: [...operationsKeys.all, "meeting-topics", topicSearch],
    queryFn: () => getTopics(topicSearch, 0),
  });
  const refresh = async () => queryClient.invalidateQueries({ queryKey: meetingsKeys.all });
  const start = useMutation({
    mutationFn: () =>
      startMeeting({
        client_action_id: crypto.randomUUID(),
        type,
        title: title.trim() || null,
        participant_ids: participantIds,
        topic_ids: topicIds,
        primary_topic_id: primaryTopicId || null,
      }),
    onSuccess: async () => {
      setTitle("");
      setParticipantIds([]);
      setTopicIds([]);
      setPrimaryTopicId("");
      setType("team");
      await refresh();
    },
  });
  const action = useMutation({
    mutationFn: ({ meeting, name }: { meeting: MeetingCardData; name: "end" | "cancel" }) =>
      runMeetingAction(meeting, name),
    onSuccess: refresh,
  });
  if (active.isPending || recent.isPending)
    return <LoadingState label="Загружаем встречи" />;
  if (active.isError || recent.isError)
    return (
      <ErrorState
        title="Не удалось загрузить встречи"
        onRetry={() => {
          void active.refetch();
          void recent.refetch();
        }}
      />
    );
  const recentItems = recent.data.pages.flatMap((page) => page.items);
  function submit(event: FormEvent) {
    event.preventDefault();
    start.mutate();
  }
  function toggle(values: string[], id: string, setValues: (value: string[]) => void) {
    setValues(
      values.includes(id) ? values.filter((value) => value !== id) : [...values, id],
    );
  }
  return (
    <OperationalLayout
      eyebrow="Активный контекст"
      title="Встречи"
      description="Контекст разговора, участники и заметки остаются в одном месте."
    >
      {active.data.meeting ? (
        <>
          {active.data.meeting.long_running && (
            <div className="meeting-warning" role="status">
              Встреча активна больше 12 часов. Завершите её, когда будете готовы.
            </div>
          )}
          <MeetingCard
            meeting={active.data.meeting}
            preferences={dateTimePreferences}
            active
            onEnd={() => action.mutate({ meeting: active.data.meeting!, name: "end" })}
            onCancel={() => {
              if (window.confirm("Отменить активную встречу?"))
                action.mutate({ meeting: active.data.meeting!, name: "cancel" });
            }}
          />
          <section className="meeting-captures">
            <div className="section-heading">
              <h2>Зафиксированные пункты</h2>
            </div>
            {captures.isPending ? (
              <LoadingState label="Загружаем пункты встречи" />
            ) : captures.isError ? (
              <ErrorState
                title="Не удалось загрузить пункты"
                onRetry={() => void captures.refetch()}
              />
            ) : !captures.data.pages.some((page) => page.items.length) ? (
              <EmptyState
                title="Пунктов пока нет"
                description="Отправьте боту текст или голосовое сообщение."
              />
            ) : (
              captures.data.pages
                .flatMap((page) => page.items)
                .map((capture) => (
                  <CaptureCard
                    key={capture.id}
                    capture={capture}
                    meetingId={active.data.meeting!.id}
                    people={people.data?.items ?? []}
                    topics={topics.data?.items ?? []}
                    onChanged={() =>
                      void queryClient.invalidateQueries({
                        queryKey: meetingsKeys.captures(active.data.meeting!.id),
                      })
                    }
                  />
                ))
            )}
            {captures.hasNextPage && (
              <button
                className="button button--secondary"
                disabled={captures.isFetchingNextPage}
                onClick={() => void captures.fetchNextPage()}
              >
                Загрузить ещё
              </button>
            )}
          </section>
        </>
      ) : (
        <form className="meeting-start-form" onSubmit={submit}>
          <div className="section-heading">
            <h2>Начать встречу</h2>
            {dirty && <span className="unsaved-badge">Не сохранено</span>}
          </div>
          <label>
            Тип
            <select
              value={type}
              onChange={(event) => setType(event.target.value as MeetingType)}
            >
              {Object.entries(typeLabels).map(([value, label]) => (
                <option value={value} key={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Название
            <input
              value={title}
              maxLength={500}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Необязательно"
            />
          </label>
          <label>
            Поиск участников
            <input
              value={personSearch}
              onChange={(event) => setPersonSearch(event.target.value)}
            />
          </label>
          <div className="meeting-option-list">
            {people.data?.items.map((person) => (
              <label key={person.id}>
                <input
                  type="checkbox"
                  checked={participantIds.includes(person.id)}
                  onChange={() => toggle(participantIds, person.id, setParticipantIds)}
                />
                {person.display_name}
              </label>
            ))}
          </div>
          <label>
            Поиск тем
            <input
              value={topicSearch}
              onChange={(event) => setTopicSearch(event.target.value)}
            />
          </label>
          <div className="meeting-option-list">
            {topics.data?.items.map((topic) => (
              <label key={topic.id}>
                <input
                  type="checkbox"
                  checked={topicIds.includes(topic.id)}
                  onChange={() => {
                    toggle(topicIds, topic.id, setTopicIds);
                    if (primaryTopicId === topic.id) setPrimaryTopicId("");
                  }}
                />
                {topic.name}
              </label>
            ))}
          </div>
          {topicIds.length > 0 && (
            <label>
              Основная тема
              <select
                value={primaryTopicId}
                onChange={(event) => setPrimaryTopicId(event.target.value)}
              >
                <option value="">Не выбрана</option>
                {topics.data?.items
                  .filter((topic) => topicIds.includes(topic.id))
                  .map((topic) => (
                    <option value={topic.id} key={topic.id}>
                      {topic.name}
                    </option>
                  ))}
              </select>
            </label>
          )}
          {start.isError && (
            <p className="inline-error">
              Не удалось начать встречу. Проверьте выбранный контекст.
            </p>
          )}
          <button className="button button--primary" disabled={start.isPending}>
            <Play size={16} /> Начать
          </button>
        </form>
      )}
      <section className="meetings-recent">
        <div className="section-heading">
          <h2>Недавние встречи</h2>
        </div>
        {!recentItems.length ? (
          <EmptyState
            title="Встреч пока нет"
            description="Начните первую встречу, чтобы собрать активный контекст."
          />
        ) : (
          recentItems.map((meeting) => (
            <MeetingCard
              key={meeting.id}
              meeting={meeting}
              preferences={dateTimePreferences}
            />
          ))
        )}
        {recent.hasNextPage && (
          <button
            className="button button--secondary"
            onClick={() => void recent.fetchNextPage()}
            disabled={recent.isFetchingNextPage}
          >
            Загрузить ещё
          </button>
        )}
      </section>
    </OperationalLayout>
  );
}
