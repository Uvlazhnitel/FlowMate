import { reasonLabels } from "./presentation";

export function InboxFilters({
  kind,
  reason,
  onKindChange,
  onReasonChange,
}: {
  kind: string;
  reason: string;
  onKindChange: (value: string) => void;
  onReasonChange: (value: string) => void;
}) {
  return (
    <div className="filter-row">
      <select
        aria-label="Тип входящего"
        value={kind}
        onChange={(event) => onKindChange(event.target.value)}
      >
        <option value="">Все типы</option>
        <option value="draft">Черновики AI</option>
        <option value="work_item">Записи</option>
        <option value="note">Заметки</option>
      </select>
      <select
        aria-label="Причина"
        value={reason}
        onChange={(event) => onReasonChange(event.target.value)}
      >
        <option value="">Все причины</option>
        {Object.entries(reasonLabels).map(([value, label]) => (
          <option value={value} key={value}>
            {label}
          </option>
        ))}
      </select>
    </div>
  );
}
