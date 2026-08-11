import { Archive, Save, Trash2 } from "lucide-react";

import type { NoteInboxAction, NoteInboxEntry } from "../../api/remaining";
import { formatDateTime, type DateTimePreferences } from "../../lib/dates";

export function NoteInboxCard({
  entry,
  dateTimePreferences,
  pending,
  onAction,
}: {
  entry: NoteInboxEntry;
  dateTimePreferences: DateTimePreferences;
  pending: boolean;
  onAction: (action: NoteInboxAction) => void;
}) {
  return (
    <>
      <span className="directory-kicker">
        {entry.source} · {formatDateTime(entry.created_at, dateTimePreferences)}
      </span>
      <h2>Неразобранная заметка</h2>
      <p>{entry.excerpt}</p>
      <div className="work-card__actions">
        <button
          className="card-action card-action--primary"
          disabled={pending}
          onClick={() => onAction("keep")}
        >
          <Save size={15} /> Оставить
        </button>
        <button
          className="card-action card-action--danger"
          disabled={pending}
          onClick={() => {
            if (window.confirm("Архивировать заметку? Текст сохранится."))
              onAction("archive");
          }}
        >
          <Archive size={15} /> В архив
        </button>
        <button
          className="card-action card-action--danger"
          disabled={pending}
          onClick={() => {
            if (window.confirm("Удалить заметку без возможности восстановления?"))
              onAction("delete");
          }}
        >
          <Trash2 size={15} aria-hidden /> Удалить
        </button>
      </div>
    </>
  );
}
