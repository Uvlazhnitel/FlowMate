import { Trash2 } from "lucide-react";

import type { BulkInboxAction, InboxKind } from "../../api/remaining";

export function InboxBulkActions({
  count,
  commonKind,
  pending,
  onAction,
}: {
  count: number;
  commonKind: InboxKind | null;
  pending: boolean;
  onAction: (action: BulkInboxAction) => void;
}) {
  if (!count) return null;
  return (
    <div className="bulk-bar" role="region" aria-label="Групповые действия">
      <strong>Выбрано: {count}</strong>
      {commonKind === "draft" && (
        <>
          <button
            className="button button--danger"
            disabled={pending}
            onClick={() => {
              if (window.confirm("Отменить выбранные черновики без удаления заметок?"))
                onAction("cancel");
            }}
          >
            Отменить черновики
          </button>
          <button
            className="button button--danger"
            disabled={pending}
            onClick={() => {
              if (
                window.confirm(
                  "Безвозвратно удалить выбранные заметки и черновики? Восстановление невозможно.",
                )
              )
                onAction("delete");
            }}
          >
            <Trash2 size={15} aria-hidden /> Удалить выбранные
          </button>
        </>
      )}
      {commonKind === "note" && (
        <>
          <button
            className="button button--secondary"
            disabled={pending}
            onClick={() => onAction("keep")}
          >
            Оставить заметками
          </button>
          <button
            className="button button--danger"
            disabled={pending}
            onClick={() => {
              if (window.confirm("Архивировать выбранные заметки?")) onAction("archive");
            }}
          >
            В архив
          </button>
          <button
            className="button button--danger"
            disabled={pending}
            onClick={() => {
              if (
                window.confirm(
                  "Безвозвратно удалить выбранные заметки? Восстановление невозможно.",
                )
              )
                onAction("delete");
            }}
          >
            <Trash2 size={15} aria-hidden /> Удалить выбранные
          </button>
        </>
      )}
      {commonKind === "work_item" && (
        <button
          className="button button--danger"
          disabled={pending}
          onClick={() => {
            if (window.confirm("Архивировать выбранные записи?")) onAction("archive");
          }}
        >
          Архивировать
        </button>
      )}
      {!commonKind && <span className="muted-copy">Выберите записи одного типа.</span>}
    </div>
  );
}
