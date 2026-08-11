import { CheckCheck, FilePenLine, Save, Trash2, XCircle } from "lucide-react";

import type {
  DraftInboxAction,
  DraftInboxEntry,
  SettingsPerson,
  SettingsTopic,
} from "../../api/remaining";
import { DraftItemEditor } from "./DraftItemEditor";

export function DraftInboxCard({
  entry,
  timezone,
  topics,
  people,
  pending,
  onAction,
  onSaved,
}: {
  entry: DraftInboxEntry;
  timezone: string;
  topics: SettingsTopic[];
  people: SettingsPerson[];
  pending: boolean;
  onAction: (action: DraftInboxAction) => void;
  onSaved: () => void;
}) {
  return (
    <>
      <div className="inbox-card__heading">
        <div>
          <span className="directory-kicker">Черновик AI · {entry.status}</span>
          <h2>{entry.items[0]?.title ?? "Черновик"}</h2>
        </div>
        <span>
          {Math.round(Math.min(...entry.items.map((item) => item.confidence), 1) * 100)}%
        </span>
      </div>
      <p>{entry.source_excerpt}</p>
      {entry.items.map((item) => (
        <details className="edit-panel" key={item.id}>
          <summary>
            <FilePenLine size={16} aria-hidden /> {item.position}. {item.title}
          </summary>
          <DraftItemEditor
            draft={entry}
            item={item}
            timezone={timezone}
            topics={topics}
            people={people}
            onSaved={onSaved}
          />
        </details>
      ))}
      <div className="work-card__actions">
        <button
          className="card-action card-action--primary"
          disabled={pending}
          onClick={() => onAction("confirm")}
        >
          <CheckCheck size={15} /> Подтвердить
        </button>
        {entry.recoverable && ["expired", "failed"].includes(entry.status) && (
          <button
            className="card-action"
            disabled={pending}
            onClick={() => onAction("recover")}
          >
            Восстановить
          </button>
        )}
        <button
          className="card-action"
          disabled={pending}
          onClick={() => onAction("save_as_note")}
        >
          <Save size={15} /> Превратить в заметку
        </button>
        <button
          className="card-action card-action--danger"
          disabled={pending}
          onClick={() => onAction("cancel")}
        >
          <XCircle size={15} /> Отменить
        </button>
        <button
          className="card-action card-action--danger"
          disabled={pending}
          onClick={() => {
            if (
              window.confirm("Удалить заметку и черновик без возможности восстановления?")
            )
              onAction("delete");
          }}
        >
          <Trash2 size={15} aria-hidden /> Удалить
        </button>
      </div>
    </>
  );
}
