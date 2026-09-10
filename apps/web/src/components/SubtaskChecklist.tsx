import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, RotateCcw } from "lucide-react";
import { useState } from "react";

import {
  createSubtask,
  operationsKeys,
  runWorkItemAction,
  type SubtaskData,
  type WorkItemCardData,
} from "../api/operations";
import { ApiError } from "../api/client";

const OPEN_STATUSES = new Set(["inbox", "planned", "active", "waiting", "snoozed"]);

export function SubtaskChecklist({
  item,
  compact = false,
}: {
  item: WorkItemCardData;
  compact?: boolean;
}) {
  const queryClient = useQueryClient();
  const serverSubtasks = item.subtasks ?? [];
  const serverSignature = serverSubtasks
    .map((subtask) => `${subtask.id}:${subtask.revision}`)
    .join("|");
  const [optimistic, setOptimistic] = useState<{
    baseSignature: string;
    subtasks: SubtaskData[];
  } | null>(null);
  const subtasks =
    optimistic?.baseSignature === serverSignature ? optimistic.subtasks : serverSubtasks;
  const [title, setTitle] = useState("");
  const [adding, setAdding] = useState(false);

  const refresh = () => queryClient.invalidateQueries({ queryKey: operationsKeys.all });

  const createMutation = useMutation({
    mutationFn: (value: string) =>
      createSubtask(item.id, {
        title: value,
        client_action_id: crypto.randomUUID(),
        expected_revision: item.revision,
      }),
    onMutate: (value) => {
      const previous = subtasks;
      setOptimistic({
        baseSignature: serverSignature,
        subtasks: [
          ...previous,
          {
            id: `pending-${crypto.randomUUID()}`,
            title: value,
            status: "active",
            completed_at: null,
            revision: 0,
            workspace: item.workspace,
          },
        ],
      });
      setTitle("");
      return { previous };
    },
    onSuccess: (response) => {
      setOptimistic({
        baseSignature: serverSignature,
        subtasks: response.work_item.subtasks ?? [],
      });
      setAdding(false);
      void refresh();
    },
    onError: (_error, _value, context) => {
      if (context)
        setOptimistic({ baseSignature: serverSignature, subtasks: context.previous });
      void refresh();
    },
  });

  const toggleMutation = useMutation({
    mutationFn: (subtask: SubtaskData) =>
      runWorkItemAction(subtask.id, {
        action: subtask.status === "done" ? "reopen" : "complete",
        client_action_id: crypto.randomUUID(),
        expected_revision: subtask.revision,
      }),
    onMutate: (subtask) => {
      const previous = subtasks;
      setOptimistic({
        baseSignature: serverSignature,
        subtasks: previous.map((value) =>
          value.id === subtask.id
            ? {
                ...value,
                status: subtask.status === "done" ? "active" : "done",
                completed_at: subtask.status === "done" ? null : new Date().toISOString(),
              }
            : value,
        ),
      });
      return { previous };
    },
    onSuccess: (response, subtask) => {
      if (response.work_item) {
        setOptimistic({
          baseSignature: serverSignature,
          subtasks: subtasks.map((value) =>
            value.id === subtask.id
              ? {
                  id: response.work_item!.id,
                  title: response.work_item!.title,
                  status: response.work_item!.status,
                  completed_at: response.work_item!.completed_at,
                  revision: response.work_item!.revision,
                  workspace: response.work_item!.workspace,
                }
              : value,
          ),
        });
      }
      void refresh();
    },
    onError: (_error, _subtask, context) => {
      if (context)
        setOptimistic({ baseSignature: serverSignature, subtasks: context.previous });
      void refresh();
    },
  });

  const done = subtasks.filter((subtask) => subtask.status === "done").length;
  const error = createMutation.error ?? toggleMutation.error;
  const canAdd = OPEN_STATUSES.has(item.status);

  function submit() {
    const normalized = title.trim();
    if (normalized && !createMutation.isPending) createMutation.mutate(normalized);
  }

  return (
    <section className={`subtasks ${compact ? "subtasks--compact" : ""}`}>
      {subtasks.length > 0 && (
        <div className="subtasks__heading">
          Подпункты · {done}/{subtasks.length}
        </div>
      )}
      {subtasks.length > 0 && (
        <ul className="subtasks__list">
          {subtasks.map((subtask) => {
            const completed = subtask.status === "done";
            const pending = subtask.id.startsWith("pending-");
            return (
              <li
                key={subtask.id}
                className={completed ? "subtask subtask--done" : "subtask"}
              >
                <button
                  type="button"
                  className="subtask__toggle"
                  aria-label={completed ? "Вернуть подпункт" : "Выполнить подпункт"}
                  disabled={pending || toggleMutation.isPending}
                  onClick={() => toggleMutation.mutate(subtask)}
                >
                  {completed ? (
                    <RotateCcw size={13} aria-hidden />
                  ) : (
                    <Check size={13} aria-hidden />
                  )}
                </button>
                <span>{subtask.title}</span>
              </li>
            );
          })}
        </ul>
      )}
      {canAdd && adding ? (
        <div className="subtasks__add">
          <input
            autoFocus
            value={title}
            maxLength={10_000}
            placeholder="Название подпункта"
            aria-label="Название подпункта"
            disabled={createMutation.isPending}
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                submit();
              } else if (event.key === "Escape") {
                setAdding(false);
                setTitle("");
                createMutation.reset();
              }
            }}
          />
          <button
            type="button"
            disabled={!title.trim() || createMutation.isPending}
            onClick={submit}
          >
            Добавить
          </button>
        </div>
      ) : canAdd ? (
        <button
          type="button"
          className="subtasks__open"
          onClick={() => {
            createMutation.reset();
            setAdding(true);
          }}
        >
          <Plus size={14} aria-hidden /> Добавить подпункт
        </button>
      ) : null}
      {error && (
        <p className="inline-error" role="alert">
          {error instanceof ApiError ? error.message : "Не удалось обновить подпункты."}
        </p>
      )}
    </section>
  );
}
