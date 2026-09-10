import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftRight, Check, Clock3, MoreVertical } from "lucide-react";
import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent } from "react";

import { ApiError } from "../../api/client";
import {
  operationsKeys,
  runWorkItemAction,
  type ActionPayload,
  type OverviewWorkItem,
  type RescheduleSelection,
  type WorkItemAction,
  type WorkItemCardData,
} from "../../api/operations";
import { remainingKeys } from "../../api/remaining";
import { InlineTitleEditor } from "../../components/InlineTitleEditor";
import { RescheduleDialog } from "../../components/RescheduleDialog";
import { WorkspaceBadge } from "../../components/WorkspaceBadge";
import { formatDateTime, type DateTimePreferences } from "../../lib/dates";

export type OverviewBucket = "today" | "tomorrow" | "inbox";

const bucketLabels: Record<OverviewBucket, string> = {
  today: "Сегодня",
  tomorrow: "Завтра",
  inbox: "Входящие",
};

const COMPLETE_DELAY_MS = 240;

export function OverviewWorkItemRow({
  entry,
  bucket,
  dateTimePreferences,
  moveDisabled = false,
  completed = false,
  dragging = false,
  onDragStart,
  onDragEnd,
  onMove,
  onCompleted,
  onReopened,
}: {
  entry: OverviewWorkItem;
  bucket: OverviewBucket;
  dateTimePreferences: DateTimePreferences;
  moveDisabled?: boolean;
  completed?: boolean;
  dragging?: boolean;
  onDragStart?: (item: WorkItemCardData, source: OverviewBucket) => void;
  onDragEnd?: () => void;
  onMove: (item: WorkItemCardData, source: OverviewBucket, target: OverviewBucket) => void;
  onCompleted: (
    item: WorkItemCardData,
    completedItem: WorkItemCardData,
    bucket: OverviewBucket,
  ) => void;
  onReopened: (item: WorkItemCardData, bucket: OverviewBucket) => void;
}) {
  const { item } = entry;
  const queryClient = useQueryClient();
  const [rescheduleOpen, setRescheduleOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [completing, setCompleting] = useState(false);
  const menuRoot = useRef<HTMLDivElement | null>(null);
  const menuTrigger = useRef<HTMLButtonElement | null>(null);
  const menuItems = useRef<Array<HTMLButtonElement | null>>([]);
  const completionTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: operationsKeys.all }),
      queryClient.invalidateQueries({ queryKey: remainingKeys.all }),
    ]);
  }

  useEffect(() => {
    function closeOutside(event: PointerEvent) {
      if (menuRoot.current && !menuRoot.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }
    function closeWithEscape(event: globalThis.KeyboardEvent) {
      if (event.key !== "Escape" || !menuOpen) return;
      setMenuOpen(false);
      menuTrigger.current?.focus();
    }
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeWithEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeWithEscape);
    };
  }, [menuOpen]);

  useEffect(
    () => () => {
      if (completionTimer.current !== null) clearTimeout(completionTimer.current);
    },
    [],
  );

  const mutation = useMutation({
    mutationFn: (payload: Omit<ActionPayload, "client_action_id">) =>
      runWorkItemAction(item.id, {
        ...payload,
        client_action_id: crypto.randomUUID(),
      }),
    onSuccess: (response, variables) => {
      if (variables.action === "reopen" && response.work_item) {
        onReopened(response.work_item, bucket);
        return;
      }
      if (
        ["complete", "waiting_received"].includes(variables.action) &&
        response.work_item
      ) {
        setCompleting(true);
        const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        completionTimer.current = setTimeout(
          () => {
            completionTimer.current = null;
            onCompleted(item, response.work_item!, bucket);
          },
          reducedMotion ? 0 : COMPLETE_DELAY_MS,
        );
        return;
      }
      if (variables.action.startsWith("reschedule")) setRescheduleOpen(false);
      void refresh();
    },
    onError: (error, variables) => {
      if (
        (error instanceof ApiError && error.status === 409) ||
        variables.action === "move_workspace"
      ) {
        void refresh();
      }
    },
  });

  function act(action: WorkItemAction, extra: Partial<ActionPayload> = {}) {
    mutation.mutate({ action, expected_revision: item.revision, ...extra });
  }

  function closeMenu() {
    setMenuOpen(false);
  }

  function move(target: OverviewBucket) {
    closeMenu();
    onMove(item, bucket, target);
  }

  function moveWorkspace() {
    closeMenu();
    const target = item.workspace === "work" ? "personal" : "work";
    const targetLabel = target === "work" ? "Работа" : "Личное";
    if (
      window.confirm(
        `Переместить запись в «${targetLabel}»? Тема будет сопоставлена по имени или снята.`,
      )
    ) {
      act("move_workspace", { target });
    }
  }

  function handleMenuKeys(event: KeyboardEvent<HTMLDivElement>) {
    const enabled = menuItems.current.filter((button): button is HTMLButtonElement =>
      Boolean(button && !button.disabled),
    );
    if (!enabled.length) return;
    const current = enabled.indexOf(document.activeElement as HTMLButtonElement);
    let next = current;
    if (event.key === "ArrowDown") next = current < enabled.length - 1 ? current + 1 : 0;
    else if (event.key === "ArrowUp") next = current > 0 ? current - 1 : enabled.length - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = enabled.length - 1;
    else return;
    event.preventDefault();
    enabled[next]?.focus();
  }

  function runPrimaryAction() {
    if (completed) {
      act("reopen");
      return;
    }
    const openSubtasks = (item.subtasks ?? []).filter(
      (subtask) => subtask.status !== "done" && subtask.status !== "cancelled",
    ).length;
    if (
      openSubtasks > 0 &&
      !window.confirm(`Завершить запись и открытые подпункты (${openSubtasks})?`)
    ) {
      return;
    }
    act(item.type === "waiting" ? "waiting_received" : "complete");
  }

  const pending = mutation.isPending || moveDisabled;
  const actionError =
    mutation.error instanceof ApiError && mutation.error.status === 409
      ? "Запись уже изменилась. Обзор обновлён."
      : mutation.isError && !rescheduleOpen
        ? "Не удалось выполнить действие."
        : null;
  const rescheduleError =
    rescheduleOpen && mutation.isError
      ? mutation.error instanceof ApiError
        ? mutation.error.message
        : "Не удалось перенести задачу. Попробуйте ещё раз."
      : null;

  return (
    <article
      className={`overview-task-row overview-card--${item.workspace} ${completed ? "overview-task-row--completed" : ""} ${completing ? "overview-task-row--completing" : ""} ${dragging ? "overview-task-row--dragging" : ""}`}
      aria-busy={pending}
      draggable={!completed && !pending}
      onDragStart={(event: DragEvent<HTMLElement>) => {
        if (completed || pending) {
          event.preventDefault();
          return;
        }
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", item.id);
        onDragStart?.(item, bucket);
      }}
      onDragEnd={onDragEnd}
    >
      <button
        className={`overview-checkbox ${completed ? "overview-checkbox--checked" : ""}`}
        type="button"
        aria-label={completed ? "Вернуть задачу" : "Завершить задачу"}
        disabled={pending}
        onClick={runPrimaryAction}
      >
        {completed && <Check size={14} aria-hidden />}
      </button>
      <div className="overview-task-row__content">
        <h3 title={item.title}>
          <InlineTitleEditor
            value={item.title}
            pending={pending || completed}
            onSave={(title) => act("edit_title", { title })}
            onEmpty={() => {
              if (window.confirm("Пустой заголовок архивирует эту запись. Продолжить?")) {
                act("edit_title", { title: "" });
              }
            }}
          />
        </h3>
        <div className="overview-task-row__footer">
          <WorkspaceBadge workspace={item.workspace} />
          {item.effective_at && (
            <time dateTime={item.effective_at} className={item.overdue ? "is-overdue" : ""}>
              <Clock3 size={13} aria-hidden />
              {formatDateTime(item.effective_at, dateTimePreferences)}
            </time>
          )}
        </div>
        {actionError && (
          <p className="inline-error" role="alert">
            {actionError}
          </p>
        )}
      </div>
      {!completed && (
        <div className="overview-card-menu" ref={menuRoot}>
          <button
            ref={menuTrigger}
            className="overview-menu-trigger"
            type="button"
            aria-label="Ещё действия"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            disabled={pending}
            onClick={() => setMenuOpen((open) => !open)}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setMenuOpen(true);
                requestAnimationFrame(() =>
                  menuItems.current.find((button) => button && !button.disabled)?.focus(),
                );
              }
            }}
          >
            <MoreVertical size={18} aria-hidden />
          </button>
          {menuOpen && (
            <div
              className="overview-card-menu__popup"
              role="menu"
              onKeyDown={handleMenuKeys}
            >
              <span className="overview-card-menu__label">Переместить в</span>
              {(["today", "tomorrow", "inbox"] as const).map((target, index) => (
                <button
                  key={target}
                  ref={(node) => {
                    menuItems.current[index] = node;
                  }}
                  type="button"
                  role="menuitem"
                  disabled={target === bucket || pending}
                  onClick={() => move(target)}
                >
                  {bucketLabels[target]}
                </button>
              ))}
              <span className="overview-card-menu__separator" />
              <button
                ref={(node) => {
                  menuItems.current[3] = node;
                }}
                type="button"
                role="menuitem"
                onClick={() => {
                  closeMenu();
                  mutation.reset();
                  setRescheduleOpen(true);
                }}
              >
                Перенести на другую дату
              </button>
              <button
                ref={(node) => {
                  menuItems.current[4] = node;
                }}
                type="button"
                role="menuitem"
                onClick={moveWorkspace}
              >
                <ArrowLeftRight size={14} aria-hidden />
                {item.workspace === "work" ? "В личное" : "В работу"}
              </button>
            </div>
          )}
        </div>
      )}
      {rescheduleOpen && (
        <RescheduleDialog
          dialogId={`overview-reschedule-${item.id}`}
          currentDueAt={item.effective_at}
          dateTimePreferences={dateTimePreferences}
          pending={mutation.isPending}
          error={rescheduleError}
          onSubmit={(selection: RescheduleSelection) =>
            mutation.mutate({ ...selection, expected_revision: item.revision })
          }
          onCancel={() => setRescheduleOpen(false)}
        />
      )}
    </article>
  );
}
