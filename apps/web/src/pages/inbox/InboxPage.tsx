import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { operationsKeys } from "../../api/operations";
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
  type BulkInboxAction,
  type DraftInboxAction,
  type DraftInboxEntry,
  type InboxEntry,
  type NoteInboxAction,
} from "../../api/remaining";
import { OperationalLayout } from "../../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../../components/PageState";
import type { DateTimePreferences } from "../../lib/dates";
import { DraftInboxCard } from "./DraftInboxCard";
import { InboxBulkActions } from "./InboxBulkActions";
import { InboxFilters } from "./InboxFilters";
import { NoteInboxCard } from "./NoteInboxCard";
import { WorkItemInboxCard } from "./WorkItemInboxCard";
import { actionError, reasonLabels } from "./presentation";

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
  const focus = params.get("focus") ?? "";
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
    mutationFn: ({
      draft,
      action,
    }: {
      draft: DraftInboxEntry;
      action: DraftInboxAction;
    }) => {
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
    mutationFn: ({ id, action }: { id: string; action: NoteInboxAction }) =>
      runNoteAction(id, action),
    onSuccess: () => void refresh(),
  });
  const bulkMutation = useMutation({
    mutationFn: async (action: BulkInboxAction) => {
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
  const commonKind = selectedKinds.size === 1 ? ([...selectedKinds][0] ?? null) : null;
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

  const focusedEntry = query.data?.items.find((entry) => {
    const id = entry.kind === "work_item" ? entry.item.id : entry.id;
    return entry.kind === kind && id === focus;
  });
  useEffect(() => {
    if (!focus || !focusedEntry) return;
    const target = document.getElementById(`inbox-entry-${focusedEntry.kind}-${focus}`);
    target?.focus({ preventScroll: true });
    target?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, [focus, focusedEntry]);

  if (query.isPending) return <LoadingState label="Собираем входящие" />;
  if (query.isError)
    return (
      <ErrorState
        title="Не удалось загрузить входящее"
        onRetry={() => void query.refetch()}
      />
    );
  const topics = options.data?.topics ?? [];
  const people = options.data?.people ?? [];
  return (
    <OperationalLayout
      eyebrow="Разобрать"
      title="Входящие"
      description="Здесь мы разбираем всё новое: уточняем, превращаем в записи или оставляем заметкой."
      controls={
        <InboxFilters
          kind={kind}
          reason={reason}
          onKindChange={(value) => setParams(value ? { kind: value } : {})}
          onReasonChange={(value) =>
            setParams(
              value ? { ...(kind ? { kind } : {}), reason: value } : kind ? { kind } : {},
            )
          }
        />
      }
    >
      <InboxBulkActions
        count={Object.keys(selected).length}
        commonKind={commonKind}
        pending={bulkMutation.isPending}
        onAction={(action) => bulkMutation.mutate(action)}
      />
      {focus && !focusedEntry && (
        <div className="inbox-focus-missing" role="status">
          <span>Запись уже обработана, перемещена или больше не требует разбора.</span>
          <Link to="/inbox">Показать актуальные входящие</Link>
        </div>
      )}
      {!query.data.items.length ? (
        <EmptyState
          title="Входящее разобрано"
          description="Нечего уточнять: все новые записи уже разобраны или отправлены дальше."
        />
      ) : (
        <div className="inbox-list">
          {query.data.items.map((entry) => {
            const id = entry.kind === "work_item" ? entry.item.id : entry.id;
            return (
              <article
                id={`inbox-entry-${entry.kind}-${id}`}
                className={`inbox-card inbox-card--${entry.kind} ${focus === id && kind === entry.kind ? "inbox-card--focused" : ""}`}
                key={`${entry.kind}-${id}`}
                tabIndex={focus === id && kind === entry.kind ? -1 : undefined}
              >
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
                <div className="reason-row">
                  {entry.reasons.map((value) => (
                    <span className="reason-chip" key={value}>
                      {reasonLabels[value] ?? value}
                    </span>
                  ))}
                </div>
                {entry.kind === "draft" && (
                  <DraftInboxCard
                    entry={entry}
                    timezone={timezone}
                    topics={topics}
                    people={people}
                    pending={draftMutation.isPending}
                    onAction={(action) => draftMutation.mutate({ draft: entry, action })}
                    onSaved={() => void refresh()}
                  />
                )}
                {entry.kind === "note" && (
                  <NoteInboxCard
                    entry={entry}
                    dateTimePreferences={dateTimePreferences}
                    pending={noteMutation.isPending}
                    onAction={(action) => noteMutation.mutate({ id: entry.id, action })}
                  />
                )}
                {entry.kind === "work_item" && (
                  <WorkItemInboxCard
                    entry={entry}
                    dateTimePreferences={dateTimePreferences}
                    timezone={timezone}
                    topics={topics}
                    people={people}
                    onSaved={() => void refresh()}
                  />
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
      {actionError([draftMutation.error, noteMutation.error, bulkMutation.error]) && (
        <p className="inline-error">
          {actionError([draftMutation.error, noteMutation.error, bulkMutation.error])}
        </p>
      )}
    </OperationalLayout>
  );
}
