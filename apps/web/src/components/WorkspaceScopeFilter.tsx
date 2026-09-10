import type { WorkspaceCounts, WorkspaceScope } from "../lib/workspaceScope";

const options: [WorkspaceScope, string][] = [
  ["all", "Все"],
  ["work", "Работа"],
  ["personal", "Личное"],
];

export function WorkspaceScopeFilter({
  scope,
  counts,
  onChange,
}: {
  scope: WorkspaceScope;
  counts?: WorkspaceCounts;
  onChange: (scope: WorkspaceScope) => void;
}) {
  const hiddenScope = scope === "work" ? "personal" : "work";
  const hiddenCount = scope === "all" ? 0 : (counts?.[hiddenScope] ?? 0);
  const selectedLabel = scope === "work" ? "Работа" : "Личное";
  const hiddenLabel = hiddenScope === "work" ? "рабочем" : "личном";
  return (
    <div className="workspace-scope-control">
      <div className="workspace-scope-filter" aria-label="Показывать задачи">
        {options.map(([value, label]) => (
          <button
            type="button"
            key={value}
            aria-pressed={scope === value}
            className={scope === value ? "workspace-scope-filter__active" : undefined}
            onClick={() => onChange(value)}
          >
            <span>{label}</span>
            {counts && <strong>{counts[value]}</strong>}
          </button>
        ))}
      </div>
      {hiddenCount > 0 && (
        <p className="workspace-scope-hint" role="status">
          Показана только «{selectedLabel}». В {hiddenLabel} скрыто {hiddenCount} задач.
          <button type="button" onClick={() => onChange("all")}>
            Показать всё
          </button>
        </p>
      )}
    </div>
  );
}
