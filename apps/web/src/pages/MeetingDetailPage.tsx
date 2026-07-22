import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, Inbox, RefreshCw, Send, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  addMeetingAgendaItem,
  answerMeetingReviewItem,
  getMeetingCaptures,
  getMeetingDetail,
  meetingsKeys,
  runMeetingReviewAction,
  runMeetingReviewItemAction,
  setMeetingAgendaOutcome,
  type MeetingReviewData,
} from "../api/meetings";
import { getPeople, getTopics, operationsKeys } from "../api/operations";
import { OperationalLayout } from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { formatDateTime, type DateTimePreferences } from "../lib/dates";
import { CaptureCard } from "./MeetingsPage";

const categoryLabels: Record<string, string> = {
  task: "Задача",
  follow_up: "Follow-up",
  waiting: "Ожидание",
  answered_question: "Отвеченный вопрос",
  unresolved_question: "Открытый вопрос",
  note: "Заметка",
  decision: "Решение",
  agenda_item: "Повестка",
};

export function MeetingDetailPage({
  dateTimePreferences,
}: {
  dateTimePreferences: DateTimePreferences;
}) {
  const meetingId = useParams().id ?? "";
  const queryClient = useQueryClient();
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [agendaTitle, setAgendaTitle] = useState("");
  const [agendaResults, setAgendaResults] = useState<Record<string, string>>({});
  const detail = useQuery({
    queryKey: meetingsKeys.detail(meetingId),
    queryFn: () => getMeetingDetail(meetingId),
    enabled: Boolean(meetingId),
  });
  const captures = useInfiniteQuery({
    queryKey: meetingsKeys.captures(meetingId),
    queryFn: ({ pageParam }) => getMeetingCaptures(meetingId, pageParam),
    initialPageParam: 0,
    enabled: Boolean(meetingId),
    getNextPageParam: (page) => (page.has_more ? page.offset + page.limit : undefined),
  });
  const people = useQuery({
    queryKey: [...operationsKeys.all, "meeting-detail-people"],
    queryFn: () => getPeople("", 0),
  });
  const topics = useQuery({
    queryKey: [...operationsKeys.all, "meeting-detail-topics"],
    queryFn: () => getTopics("", 0),
  });
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: meetingsKeys.detail(meetingId) }),
      queryClient.invalidateQueries({ queryKey: meetingsKeys.captures(meetingId) }),
      queryClient.invalidateQueries({ queryKey: meetingsKeys.recent }),
    ]);
  };
  const reviewAction = useMutation({
    mutationFn: ({
      review,
      action,
    }: {
      review: MeetingReviewData;
      action: "retry" | "confirm_ready" | "complete_with_inbox";
    }) => runMeetingReviewAction(meetingId, review, action),
    onSuccess: refresh,
  });
  const itemAction = useMutation({
    mutationFn: ({
      review,
      itemId,
      action,
    }: {
      review: MeetingReviewData;
      itemId: string;
      action: "exclude" | "include" | "planner_on" | "planner_off" | "inbox";
    }) => runMeetingReviewItemAction(meetingId, review, itemId, action),
    onSuccess: refresh,
  });
  const clarification = useMutation({
    mutationFn: ({ review, itemId }: { review: MeetingReviewData; itemId: string }) =>
      answerMeetingReviewItem(meetingId, review, itemId, answers[itemId] ?? ""),
    onSuccess: refresh,
  });
  const agenda = useMutation({
    mutationFn: ({
      entryId,
      outcome,
    }: {
      entryId: string;
      outcome: "discussed" | "answered" | "deferred" | "unresolved";
    }) =>
      setMeetingAgendaOutcome(meetingId, entryId, outcome, agendaResults[entryId] || null),
    onSuccess: refresh,
  });
  const addAgenda = useMutation({
    mutationFn: () => addMeetingAgendaItem(meetingId, agendaTitle),
    onSuccess: async () => {
      setAgendaTitle("");
      await refresh();
    },
  });

  if (detail.isPending) return <LoadingState label="Загружаем итог встречи" />;
  if (detail.isError)
    return (
      <ErrorState
        title="Не удалось загрузить встречу"
        onRetry={() => void detail.refetch()}
      />
    );
  const { meeting, review } = detail.data;
  const captureItems = captures.data?.pages.flatMap((page) => page.items) ?? [];
  const decisions = review?.items.filter((item) => item.category === "decision") ?? [];
  const unresolved =
    review?.items.filter((item) =>
      ["clarification_required", "inbox"].includes(item.status),
    ) ?? [];
  const nextActions =
    review?.items.filter((item) =>
      ["task", "follow_up", "waiting"].includes(item.category),
    ) ?? [];

  return (
    <OperationalLayout
      eyebrow="Meeting Review"
      title={meeting.title}
      description={`${meeting.type} · ${formatDateTime(meeting.started_at ?? meeting.created_at, dateTimePreferences)}`}
      controls={
        <Link className="button button--secondary" to="/meetings">
          <ArrowLeft size={15} /> Встречи
        </Link>
      }
    >
      <section className="meeting-review-hero">
        <div>
          <span className={`status-badge status-badge--${meeting.status}`}>
            {meeting.status}
          </span>
          <p>{review?.summary ?? "Итог ещё формируется."}</p>
        </div>
        {review && (
          <div className="meeting-review-actions">
            {review.status === "failed" && (
              <button
                className="button button--primary"
                onClick={() => reviewAction.mutate({ review, action: "retry" })}
              >
                <RefreshCw size={15} /> Повторить
              </button>
            )}
            {review.status === "review_required" && (
              <>
                <button
                  className="button button--primary"
                  onClick={() => reviewAction.mutate({ review, action: "confirm_ready" })}
                >
                  <CheckCircle2 size={15} /> Подтвердить готовые
                </button>
                <button
                  className="button button--secondary"
                  onClick={() =>
                    reviewAction.mutate({ review, action: "complete_with_inbox" })
                  }
                >
                  <Inbox size={15} /> Неполное в Inbox
                </button>
              </>
            )}
          </div>
        )}
        {(reviewAction.isError ||
          itemAction.isError ||
          clarification.isError ||
          agenda.isError ||
          addAgenda.isError) && (
          <p className="inline-error">
            Изменение не сохранено. Обновите данные и повторите.
          </p>
        )}
      </section>

      <section className="meeting-review-grid">
        <article>
          <span>Участники</span>
          <strong>
            {meeting.participants.map(([, name]) => name).join(", ") || "Не указаны"}
          </strong>
        </article>
        <article>
          <span>Темы</span>
          <strong>
            {meeting.topics.map(([, name]) => name).join(", ") || "Не указаны"}
          </strong>
        </article>
        <article>
          <span>Пунктов</span>
          <strong>{meeting.captured_note_count}</strong>
        </article>
        <article>
          <span>Решений</span>
          <strong>{decisions.length}</strong>
        </article>
      </section>

      <section className="meeting-review-section">
        <div className="section-heading">
          <h2>Итоговые пункты</h2>
          <span>{review?.items.length ?? 0}</span>
        </div>
        {!review?.items.length ? (
          <EmptyState
            title="Пунктов пока нет"
            description="Review ещё не сформировал предложения."
          />
        ) : (
          review.items.map((item) => (
            <article className={`review-item review-item--${item.status}`} key={item.id}>
              <header>
                <span className="directory-kicker">
                  №{item.position} · {categoryLabels[item.category] ?? item.category}
                </span>
                <span className="status-badge">{item.status}</span>
              </header>
              <h3>{item.title}</h3>
              {item.suggested_next_action && <p>{item.suggested_next_action}</p>}
              {item.clarification_question && item.status === "clarification_required" && (
                <div className="review-clarification">
                  <strong>{item.clarification_question}</strong>
                  <textarea
                    value={answers[item.id] ?? ""}
                    onChange={(event) =>
                      setAnswers((current) => ({
                        ...current,
                        [item.id]: event.target.value,
                      }))
                    }
                  />
                  <button
                    className="button button--primary"
                    disabled={!answers[item.id]?.trim()}
                    onClick={() => clarification.mutate({ review, itemId: item.id })}
                  >
                    <Send size={14} /> Ответить
                  </button>
                </div>
              )}
              {review.status === "review_required" && (
                <footer>
                  <button
                    className="button button--secondary"
                    onClick={() =>
                      itemAction.mutate({
                        review,
                        itemId: item.id,
                        action: item.status === "excluded" ? "include" : "exclude",
                      })
                    }
                  >
                    {item.status === "excluded" ? (
                      <CheckCircle2 size={14} />
                    ) : (
                      <XCircle size={14} />
                    )}{" "}
                    {item.status === "excluded" ? "Вернуть" : "Исключить"}
                  </button>
                  {["task", "follow_up", "waiting"].includes(item.category) && (
                    <button
                      className="button button--secondary"
                      onClick={() =>
                        itemAction.mutate({
                          review,
                          itemId: item.id,
                          action: item.planner_requested ? "planner_off" : "planner_on",
                        })
                      }
                    >
                      {item.planner_requested ? "Убрать из Planner" : "В Planner Queue"}
                    </button>
                  )}
                </footer>
              )}
            </article>
          ))
        )}
      </section>

      <section className="meeting-review-section">
        <div className="section-heading">
          <h2>Agenda</h2>
          <span>{review?.agenda.length ?? 0}</span>
        </div>
        {review?.agenda.map((entry) => (
          <article className="agenda-review-item" key={entry.id}>
            <div>
              <strong>{entry.title}</strong>
              <span>{entry.outcome}</span>
            </div>
            <textarea
              placeholder="Результат или ответ"
              value={agendaResults[entry.id] ?? entry.result ?? ""}
              onChange={(event) =>
                setAgendaResults((current) => ({
                  ...current,
                  [entry.id]: event.target.value,
                }))
              }
            />
            <div className="agenda-review-actions">
              {(["discussed", "answered", "deferred", "unresolved"] as const).map(
                (outcome) => (
                  <button
                    className="button button--secondary"
                    key={outcome}
                    onClick={() => agenda.mutate({ entryId: entry.id, outcome })}
                  >
                    {outcome}
                  </button>
                ),
              )}
            </div>
          </article>
        ))}
        {review?.status === "review_required" && (
          <form
            className="agenda-add"
            onSubmit={(event) => {
              event.preventDefault();
              addAgenda.mutate();
            }}
          >
            <input
              required
              value={agendaTitle}
              onChange={(event) => setAgendaTitle(event.target.value)}
              placeholder="Новый пункт повестки"
            />
            <button className="button button--primary">Добавить</button>
          </form>
        )}
      </section>

      <section className="meeting-review-columns">
        <div>
          <div className="section-heading">
            <h2>Решения</h2>
          </div>
          {decisions.length ? (
            decisions.map((item) => (
              <p key={item.id}>
                <strong>{item.title}</strong>
                {item.consequences.length ? ` — ${item.consequences.join("; ")}` : ""}
              </p>
            ))
          ) : (
            <p className="muted-copy">Решений нет.</p>
          )}
        </div>
        <div>
          <div className="section-heading">
            <h2>Следующие действия</h2>
          </div>
          {nextActions.length ? (
            nextActions.map((item) => (
              <p key={item.id}>
                <strong>{item.title}</strong> · {item.status}
                {item.planner_requested ? " · Planner" : ""}
              </p>
            ))
          ) : (
            <p className="muted-copy">Действий нет.</p>
          )}
        </div>
        <div>
          <div className="section-heading">
            <h2>Неразобранное</h2>
          </div>
          {unresolved.length ? (
            unresolved.map((item) => (
              <p key={item.id}>
                {item.title} · {item.status}
              </p>
            ))
          ) : (
            <p className="muted-copy">Все пункты разобраны.</p>
          )}
        </div>
      </section>

      <section className="meeting-review-section">
        <div className="section-heading">
          <h2>Результирующие записи</h2>
          <span>{review?.results.length ?? 0}</span>
        </div>
        {review?.results.map((item) => (
          <article className="result-row" key={item.id}>
            <strong>{item.title}</strong>
            <span>
              {item.type} · {item.status} · {item.planner_status}
            </span>
          </article>
        ))}
      </section>

      <section className="meeting-review-section">
        <div className="section-heading">
          <h2>Хронология capture</h2>
        </div>
        {captures.isPending ? (
          <LoadingState label="Загружаем записи" />
        ) : captures.isError ? (
          <ErrorState
            title="Не удалось загрузить записи"
            onRetry={() => void captures.refetch()}
          />
        ) : !captureItems.length ? (
          <EmptyState
            title="Записей нет"
            description="У встречи нет сохранённых capture-пунктов."
          />
        ) : (
          captureItems.map((capture) => (
            <CaptureCard
              key={capture.id}
              capture={capture}
              meetingId={meetingId}
              people={people.data?.items ?? []}
              topics={topics.data?.items ?? []}
              onChanged={() => void refresh()}
            />
          ))
        )}
        {captures.hasNextPage && (
          <button
            className="button button--secondary"
            onClick={() => void captures.fetchNextPage()}
          >
            Загрузить ещё
          </button>
        )}
      </section>

      <section className="meeting-review-section">
        <div className="section-heading">
          <h2>Timeline</h2>
        </div>
        {!review?.timeline.length ? (
          <EmptyState
            title="История встречи пока пуста"
            description="События появятся после обработки встречи."
          />
        ) : (
          review.timeline.map((event) => (
            <article className="timeline-row" key={event.id}>
              <span>{formatDateTime(event.created_at, dateTimePreferences)}</span>
              <strong>{event.event_type}</strong>
              <span>
                {event.previous_status ?? "—"} → {event.new_status}
              </span>
            </article>
          ))
        )}
      </section>
    </OperationalLayout>
  );
}
