import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, Ban, CheckCircle2, RefreshCcw } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { operationsKeys, runWorkItemAction } from "../api/operations";
import {
  getPlannerQueue,
  remainingKeys,
  type PlannerEntry,
  type PlannerStatus,
} from "../api/remaining";
import { OperationalLayout } from "../components/OperationalLayout";
import { EmptyState, ErrorState, LoadingState } from "../components/PageState";
import { StatusBadge } from "../components/WorkItemCard";
import { formatDateTime, type DateTimePreferences } from "../lib/dates";

const statusLabels: Record<PlannerStatus, string> = {
  not_required: "Не требуется",
  needs_transfer: "Нужно перенести",
  transferred: "Перенесено",
  update_required: "Нужно обновить",
  no_longer_relevant: "Больше не актуально",
};

function PlannerCard({
  entry,
  dateTimePreferences,
  onChanged,
}: {
  entry: PlannerEntry;
  dateTimePreferences: DateTimePreferences;
  onChanged: () => void;
}) {
  const mutation = useMutation({
    mutationFn: (
      action:
        | "planner_transferred"
        | "planner_not_required"
        | "planner_update_required"
        | "planner_needs_transfer",
    ) =>
      runWorkItemAction(entry.item.id, {
        action,
        client_action_id: crypto.randomUUID(),
        expected_revision: entry.item.revision,
      }),
    onSuccess: onChanged,
  });
  return (
    <article className="planner-card">
      <div className="work-card__topline">
        <StatusBadge item={entry.item} />
        <span className={`planner-status planner-status--${entry.planner_status}`}>
          {statusLabels[entry.planner_status]}
        </span>
      </div>
      <h2>{entry.item.title}</h2>
      {entry.item.description && <p>{entry.item.description}</p>}
      <div className="planner-meta">
        <span>Тема: {entry.item.topic_name ?? "без темы"}</span>
        <span>Срок: {formatDateTime(entry.item.effective_at, dateTimePreferences)}</span>
        <span>Приоритет: {entry.item.priority}</span>
        <span>Передано: {formatDateTime(entry.transferred_at, dateTimePreferences)}</span>
      </div>
      <div className="work-card__actions">
        {entry.planner_status !== "transferred" && (
          <button
            className="card-action card-action--primary"
            onClick={() => mutation.mutate("planner_transferred")}
          >
            <CheckCircle2 size={15} /> Перенесено
          </button>
        )}
        {entry.planner_status !== "not_required" && (
          <button
            className="card-action"
            onClick={() => mutation.mutate("planner_not_required")}
          >
            <Ban size={15} /> Не требуется
          </button>
        )}
        {entry.planner_status === "transferred" && (
          <button
            className="card-action"
            onClick={() => mutation.mutate("planner_update_required")}
          >
            <ArrowUpRight size={15} /> Нужно обновить
          </button>
        )}
        {entry.planner_status !== "needs_transfer" && (
          <button
            className="card-action"
            onClick={() => mutation.mutate("planner_needs_transfer")}
          >
            <RefreshCcw size={15} /> Вернуть в очередь
          </button>
        )}
      </div>
      {mutation.isError && (
        <p className="inline-error">Не удалось изменить Planner status.</p>
      )}
    </article>
  );
}

export function PlannerQueuePage({
  dateTimePreferences,
}: {
  dateTimePreferences: DateTimePreferences;
}) {
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "needs_transfer,update_required";
  const q = params.get("q") ?? "";
  const page = Number(params.get("page") ?? 0);
  const query = useQuery({
    queryKey: [...remainingKeys.all, "planner", status, q, page],
    queryFn: () => getPlannerQueue(status, q, page * 20),
  });
  const changed = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: remainingKeys.all }),
      queryClient.invalidateQueries({ queryKey: operationsKeys.all }),
    ]);
  };
  if (query.isPending) return <LoadingState label="Загружаем Planner Queue" />;
  if (query.isError)
    return (
      <ErrorState
        title="Не удалось загрузить Planner Queue"
        onRetry={() => void query.refetch()}
      />
    );
  return (
    <OperationalLayout
      eyebrow="Ручная передача"
      title="Planner Queue"
      description="Контроль переноса без фоновой интеграции с Microsoft Planner."
      controls={
        <div className="filter-row">
          <input
            className="search-field"
            aria-label="Поиск Planner Queue"
            placeholder="Найти запись"
            value={q}
            onChange={(event) =>
              setParams({
                ...(status ? { status } : {}),
                ...(event.target.value ? { q: event.target.value } : {}),
              })
            }
          />
          <select
            aria-label="Planner status"
            value={status}
            onChange={(event) =>
              setParams({ status: event.target.value, ...(q ? { q } : {}) })
            }
          >
            <option value="needs_transfer,update_required">Требует действия</option>
            {Object.entries(statusLabels).map(([value, label]) => (
              <option value={value} key={value}>
                {label}
              </option>
            ))}
            <option value="not_required,needs_transfer,transferred,update_required,no_longer_relevant">
              Все статусы
            </option>
          </select>
        </div>
      }
    >
      {!query.data.items.length ? (
        <EmptyState
          title="Очередь пуста"
          description="Для выбранного фильтра нет записей, требующих ручной передачи."
        />
      ) : (
        <div className="planner-grid">
          {query.data.items.map((entry) => (
            <PlannerCard
              entry={entry}
              dateTimePreferences={dateTimePreferences}
              onChanged={() => void changed()}
              key={entry.item.id}
            />
          ))}
        </div>
      )}
      <div className="pager">
        <button
          className="button button--secondary"
          disabled={page === 0}
          onClick={() => setParams({ status, ...(q ? { q } : {}), page: String(page - 1) })}
        >
          Назад
        </button>
        <button
          className="button button--secondary"
          disabled={!query.data.has_more}
          onClick={() => setParams({ status, ...(q ? { q } : {}), page: String(page + 1) })}
        >
          Дальше
        </button>
      </div>
    </OperationalLayout>
  );
}
